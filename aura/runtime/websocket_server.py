"""FastAPI WebSocket adapter between the browser and the AURA runtime.

Scope
-----
This module is a **transport adapter only**. It introduces no new runtime
behaviour: every user fact flows through the existing ``Event`` /
``SessionMailbox`` / ``Coordinator`` APIs exactly as implemented, and no
runtime, frontend or test file is modified::

    browser --JSON--> /ws handler --Event--> SessionMailbox
                                                  |
                                    Coordinator.run()   (one per session)
                                                  |
                                     coordinator.emitted  (append-only)
                                                  |
                                    forwarder task --JSON--> browser

Design decisions (derived from checked-in code, not assumptions)
----------------------------------------------------------------
- **Single-consumer invariant (I2)**: the handler never calls
  ``Coordinator.process_next()``. Each session starts exactly one
  ``Coordinator.run()`` task in ``_open_session`` and that task is the
  session's only consumer, so the guard in ``coordinator.process_next()``
  cannot be violated from this adapter.
- **Reception never blocks behind a tool call**: the Coordinator runs
  tools in its own watcher tasks and ``run()`` only waits on the mailbox,
  so a long-running tool cannot delay the next inbound frame. Interrupts
  are additionally enqueued at ``Priority.CONTROL``, which the mailbox
  always serves before queued ``DATA`` events (``mailbox.get``).
- **Forwarding is cursor-based**: ``coordinator.emitted`` is append-only
  (``Coordinator._emit``), so a per-connection integer cursor forwards
  each runtime event exactly once — no duplicates, no missed events.
- **The runtime is silent about accepted results**: an accepted outcome
  only writes ``Coordinator.accepted_results`` and emits nothing (the
  ``ACCEPTED`` branch of ``_handle_task_outcome`` in ``coordinator.py``).
  The forwarder therefore also synthesises one presentation
  ``TOOL_COMPLETED`` frame per newly accepted call id (deduplicated via
  ``_Session.completed_sent``), followed by one ``FINAL_RESPONSE`` frame
  whose ``payload.auraText`` carries the readable answer the chat
  renders (``LiveAgent.tsx:12``; shape mirrors ``MockAgentClient.ts:114``),
  because the frontend clears its running tool row only on
  ``TOOL_COMPLETED``/``TOOL_CANCELLED`` (``LiveAgent.tsx:63``). Both
  frames are built exclusively from ``accepted_results`` — the gate's
  accepted, current outcomes — so a cancelled or stale call can never
  produce either.
- **The served tool is bounded**: the runtime never times a tool out, so
  the server injects ``_server_tool`` (deterministic answer after
  ``FLIGHT_SEARCH_DELAY_S``) instead of ``DemoFlightTool``, whose
  non-Mumbai branch waits forever — a scripted-demo hook, not a server
  behaviour. Cancellation stays advisory exactly as in the demo tool: a
  superseded call still returns its late result and the Acceptance Gate
  rejects it as STALE, so the cancellation/stale-result checks remain
  fully exercised. The generator is query-aware: origin, destination
  and travel date are parsed from the intent and shape the simulated
  quotes, and a parsed preference (cheapest/fastest/earliest) orders
  them and names the winner (``_route_of``/``_date_of``/
  ``_preference_of``).
- **Query continuity across interrupts**: a revision that changes only
  one part of the request keeps the unstated fields from the session's
  previous query (``_QUERY_CONTEXT``, keyed by the updated state's
  ``session_id`` and dropped with the session): "Instead of Delhi,
  find me for Mumbai." searches the original origin → Mumbai with the
  travel date and preference intact.
- **User frames are echoed for UI parity**: the Coordinator never echoes
  ``USER_INPUT``/``USER_INTERRUPT``, yet the frontend renders chat
  exclusively from received ``USER_INPUT`` events (``LiveAgent.tsx:35``)
  and the interruption row from ``INTERRUPTION`` events
  (``LiveAgent.tsx:192``) — the mock client emits both itself
  (``MockAgentClient.ts:85,136``). The adapter mirrors those two mock
  frames; nothing else is synthesised besides ``TOOL_COMPLETED`` and
  ``FINAL_RESPONSE`` (both acceptance-gated, see above).
- **Type translation**: the frontend event union is a closed set of
  UPPERCASE names (``types.ts:5``). Backend ``EventType`` values map onto
  it via ``_TYPE_MAP``; backend types with no frontend counterpart (e.g.
  ``runtime.error``) pass through unchanged rather than being mislabelled.

Honest status
-------------
On this branch the frontend constructs ``RealAgentClient`` pointing at
this server (working-tree ``AuraContext.tsx``); that wiring is checked
by the Python suite, the frontend build and live smoke probes — **not**
by an automated end-to-end test. Known protocol gaps found while
writing it (frontend results/snapshot population) are reported to the
team, not papered over here.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import time
from typing import Any, Dict, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from aura.runtime import (
    Coordinator,
    Event,
    EventType,
    MailboxFull,
    Priority,
)

__all__ = ["app", "websocket_endpoint"]

logger = logging.getLogger("aura.runtime.websocket_server")

app = FastAPI()

#: How often the forwarder checks the append-only outbox for new events.
FORWARD_INTERVAL_S = 0.05

#: How long disconnect cleanup waits for in-flight tool tasks to settle.
CLEANUP_TIMEOUT_S = 2.0

#: One search's wall-clock duration on the served path. Deterministic and
#: bounded: the runtime has no tool timeout, so the tool itself must
#: always finish. Raised to 10s (previously ~0.5-1s) purely to leave a
#: comfortable window for interrupting a search interactively in the
#: browser; cancellation and stale-result rejection are unaffected by
#: the duration.
FLIGHT_SEARCH_DELAY_S = 10.0

#: WebSocket close codes (application range 4000-4999).
WS_CLOSE_DUPLICATE_SESSION = 4009
WS_CLOSE_SESSION_MISMATCH = 4000

#: Tool name shown by the frontend (MockAgentClient.ts uses "flight_search").
DEFAULT_TOOL_NAME = "flight_search"

_ACCEPTED_KINDS = frozenset({"text", "interrupt"})

#: Backend EventType value -> frontend AuraEventType (types.ts:5).
#: Values with no frontend counterpart are passed through unchanged.
_TYPE_MAP: Dict[str, str] = {
    EventType.USER_INPUT.value: "USER_INPUT",
    EventType.USER_INTERRUPT.value: "INTERRUPTION",
    EventType.TASK_STARTED.value: "TOOL_STARTED",
    EventType.TASK_COMPLETED.value: "TOOL_COMPLETED",
    EventType.TASK_CANCEL_REQUESTED.value: "CANCEL_REQUESTED",
    EventType.TASK_CANCELLED.value: "TOOL_CANCELLED",
    EventType.TASK_RESULT_REJECTED.value: "STALE_RESULT_REJECTED",
    EventType.STATE_VERSION_CHANGED.value: "STATE_UPDATED",
    # No frontend counterpart: task.failed, plan.ready, plan.failed,
    # runtime.error -> pass through verbatim.
}

#: One Coordinator + its two per-connection tasks.
_sessions: Dict[str, "_Session"] = {}

#: Last query per session: ``origin``/``destination``/``date``/
#: ``preference`` as merged for the call that produced it. ``SessionState``
#: is frozen and carries only the latest intent (``state.py``), so an
#: interrupt revising one field ("Instead of Delhi, find me for Mumbai.")
#: finds the rest of the request here — keyed by the updated state's
#: ``session_id`` and cleared in ``_close_session`` alongside ``_sessions``.
_QUERY_CONTEXT: Dict[str, Dict[str, Any]] = {}


# ----------------------------------------------------------------- server tool
#: Cities the route parser recognises (superset of MockAgentClient.ts:4 —
#: a request naming an unlisted city falls back to the mock's defaults).
_CITIES = (
    "bangalore", "delhi", "mumbai", "chennai", "hyderabad",
    "kolkata", "goa", "jaipur", "pune", "kochi",
    "ahmedabad", "lucknow", "patna", "indore", "nagpur",
    "varanasi", "amritsar", "chandigarh", "bhopal", "srinagar",
)

#: Alternate spellings folded to the canonical city before route parsing.
_CITY_ALIASES = (
    ("bengaluru", "bangalore"),
    ("new delhi", "delhi"),
    ("bombay", "mumbai"),
    ("calcutta", "kolkata"),
    ("madras", "chennai"),
    ("poona", "pune"),
)

#: Simulated airline pool; five quotes draw five distinct carriers.
_AIRLINES = (
    "IndiGo", "Air India", "Vistara", "SpiceJet", "Akasa Air", "AirAsia India",
)

#: Simulated fare tiers per airline, in thousandths (no real fares).
_AIRLINE_BP = {
    "IndiGo": 970,
    "Air India": 1050,
    "Vistara": 1120,
    "SpiceJet": 930,
    "Akasa Air": 950,
    "AirAsia India": 900,
}

#: Plausible domestic departure slots; options rotate through these.
_DEPART_SLOTS = (
    (5, 40), (7, 55), (10, 20), (13, 5), (16, 45), (19, 30), (21, 50),
)

#: Month-name table for date parsing (sept accepted as September).
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}
_MONTH_ABBR = (
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)

#: Year assumed when the request gives a day/month but no year: the mock's
#: own default slot date is "25 Sep 2026" (MockAgentClient.ts:53). A fixed
#: default keeps identical inputs bit-identical — no wall-clock reads.
DEFAULT_YEAR = 2026


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _normalize(text: str) -> str:
    """Lowercase and fold alternate city spellings ("Bengaluru" -> "bangalore")."""
    t = text.lower()
    for alias, canonical in sorted(_CITY_ALIASES, key=lambda a: -len(a[0])):
        t = t.replace(alias, canonical)
    return t


def _route_of(
    intent: str, previous: Optional[Dict[str, Any]] = None
) -> tuple[str, str]:
    """Parse ``(origin, destination)`` from the user's text.

    Mirrors ``MockAgentClient.parseCities`` (MockAgentClient.ts:66):
    ``from``/``to`` regex picks, then the known-city fallbacks, then the
    mock's slot defaults (Bangalore / Delhi) — extended with a wider
    city list and alternate spellings (``_CITY_ALIASES``) so more
    requests resolve to the route the user actually asked for.

    When ``previous`` is the session's last query (``_QUERY_CONTEXT``),
    an interrupt that revises only part of the request keeps the rest
    of the route:

    - an explicit ``from``/``to`` still wins outright;
    - a single mentioned city that is not part of the previous route is
      the new destination ("Instead of Delhi, find me for Mumbai." →
      Mumbai, because Delhi *is* the previous destination being
      replaced, and Mumbai is the only city left to ask for);
    - two such cities revise the whole route (the legacy fallback's
      order);
    - anything unstated carries over from ``previous`` instead of
      hitting the mock's defaults.
    Nothing is hardcoded: the result is derived only from the text and
    ``previous``.
    """
    t = _normalize(intent)
    found = [c for c in _CITIES if c in t]
    from_m = re.search(r"from\s+([a-z]+)", t)
    to_m = re.search(r"to\s+([a-z]+)", t)

    def pick(match: Optional["re.Match[str]"]) -> Optional[str]:
        if match is None:
            return None
        word = match.group(1)
        hit = next(
            (c for c in found if c.startswith(word) or word.startswith(c)),
            None,
        )
        return _cap(hit) if hit else None

    origin = pick(from_m)
    destination = pick(to_m)
    if previous:
        prior_origin = previous.get("origin")
        prior_dest = previous.get("destination")
        # Mentioned cities that are not the previous route (and not
        # already picked by a marker) — i.e. what the revision is about.
        candidates = [
            _cap(c)
            for c in found
            if _cap(c) not in (prior_origin, prior_dest, origin, destination)
        ]
        if destination is None and len(candidates) == 1:
            destination = candidates[0]  # destination-only revision
        if origin is None and destination is None and len(candidates) >= 2:
            origin, destination = candidates[0], candidates[1]  # full revision
        if origin is None and destination is not None:
            # One-sided route ("goa to bangalore"): the mentioned city
            # that is neither the destination nor the previous route.
            excluded = (destination, prior_origin, prior_dest)
            other = next((c for c in found if _cap(c) not in excluded), None)
            origin = _cap(other) if other else prior_origin
        if origin is None:
            origin = prior_origin
        if destination is None:
            destination = prior_dest
        return origin or "Bangalore", destination or "Delhi"
    if origin is None and destination is None and len(found) == 1:
        destination = _cap(found[0])
    if origin is None and len(found) >= 2 and to_m is None and from_m is None:
        origin, destination = _cap(found[0]), _cap(found[1])
    if origin is None and found:
        # One-sided route ("goa to bangalore"): name the mentioned city
        # that is not the destination instead of repeating the default.
        other = next((c for c in found if _cap(c) != destination), None)
        origin = _cap(other) if other else origin
    return origin or "Bangalore", destination or "Delhi"


def _canonical_date(year: int, month: int, day: int) -> Optional[str]:
    """``(2026, 9, 25)`` -> ``"25 Sep 2026"``; ``None`` when not a real date."""
    if not 1900 <= year <= 2099:
        return None
    try:
        d = datetime.date(year, month, day)
    except ValueError:
        return None
    return f"{d.day} {_MONTH_ABBR[d.month]} {d.year}"


def _date_of(intent: str) -> Optional[str]:
    """Extract an explicit travel date from the request.

    Recognised, in order: ``2026-09-25``; ``25/09/2026`` (also ``.`` and
    ``-`` separators); ``25 Sep [2026]`` / ``25th of September [2026]``;
    ``Sep 25, [2026]``. Returns the canonical slot format ``"25 Sep 2026"``
    (mock parity) or ``None`` when the request states no explicit date.
    Relative phrases ("tomorrow") are deliberately *not* resolved:
    anchoring them to the wall clock would break determinism for identical
    inputs. A missing or implausible year falls back to ``DEFAULT_YEAR``.
    """
    t = intent.lower()
    for pat in (
        r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b",
        r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b",
        r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b",
    ):
        m = re.search(pat, t)
        if m is None:
            continue
        a, b, c = m.groups()
        if len(a) == 4:  # ISO: year-month-day
            year, month, day = int(a), int(b), int(c)
        else:            # day-first: 25/09/2026
            day, month, year = int(a), int(b), int(c)
            if year < 100:
                year += 2000
        date = _canonical_date(year, month, day) or _canonical_date(
            DEFAULT_YEAR, month, day
        )
        if date is not None:
            return date
    m = re.search(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3,9})[.,]?\s*(\d{4})?\b",
        t,
    )
    if m is not None and m.group(2) in _MONTHS:
        month = _MONTHS[m.group(2)]
        year = int(m.group(3)) if m.group(3) else DEFAULT_YEAR
        date = _canonical_date(year, month, int(m.group(1))) or _canonical_date(
            DEFAULT_YEAR, month, int(m.group(1))
        )
        if date is not None:
            return date
    m = re.search(
        r"\b([a-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?\b",
        t,
    )
    if m is not None and m.group(1) in _MONTHS:
        month = _MONTHS[m.group(1)]
        year = int(m.group(3)) if m.group(3) else DEFAULT_YEAR
        date = _canonical_date(year, month, int(m.group(2))) or _canonical_date(
            DEFAULT_YEAR, month, int(m.group(2))
        )
        if date is not None:
            return date
    return None


_CHEAPEST_WORDS = ("cheapest", "lowest price", "lowest fare", "least expensive", "budget")
_FASTEST_WORDS = ("fastest", "quickest", "shortest")
_EARLIEST_WORDS = ("earliest", "first flight")


def _preference_of(intent: str) -> Optional[str]:
    """Parse the requested ordering: ``"cheapest"``, ``"fastest"``,
    ``"earliest"`` or ``None`` (no preference stated).

    Checked cheapest-first, so "cheapest and fastest" deterministically
    resolves to the price interpretation.
    """
    t = intent.lower()
    if any(word in t for word in _CHEAPEST_WORDS):
        return "cheapest"
    if any(word in t for word in _FASTEST_WORDS):
        return "fastest"
    if any(word in t for word in _EARLIEST_WORDS):
        return "earliest"
    return None


def _query_of(
    intent: str, previous: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Merge one request with the session's previous query, field by field.

    Whatever the new intent states wins (route via ``_route_of``, plus
    ``_date_of`` / ``_preference_of``); whatever it omits — the norm for
    interrupts like "Instead of Delhi, find me for Mumbai." — carries
    over from ``previous``, so origin, travel date and preference
    survive a destination-only revision (and only the fields the user
    changes are replaced). Pure and deterministic: no clock, no session
    access — the caller reads and writes ``_QUERY_CONTEXT``.
    """
    prev = previous or {}
    origin, destination = _route_of(intent, prev or None)
    return {
        "origin": origin,
        "destination": destination,
        "date": _date_of(intent) or prev.get("date"),
        "preference": _preference_of(intent) or prev.get("preference"),
    }


def _seed_of(*parts: str) -> int:
    """Stable FNV-1a hash of the query parts.

    ``hash()`` is deliberately avoided — CPython salts it per process,
    which would make identical requests differ across restarts.
    """
    h = 2166136261
    for ch in "|".join(parts):
        h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
    return h


def _mix(value: int) -> int:
    """Deterministic 32-bit avalanche mix (integer-only, run-independent)."""
    value &= 0xFFFFFFFF
    value = ((value ^ (value >> 16)) * 0x7FEB352D) & 0xFFFFFFFF
    value = ((value ^ (value >> 15)) * 0x846CA68B) & 0xFFFFFFFF
    return (value ^ (value >> 16)) & 0xFFFFFFFF


def _season_bp(date: Optional[str]) -> int:
    """Fare factor from the travel date: holiday/summer peak + weekend (‰)."""
    if not date:
        return 1000
    try:
        day_s, mon_s, year_s = date.split()
        d = datetime.date(int(year_s), _MONTHS[mon_s.lower()], int(day_s))
    except (ValueError, KeyError):
        return 1000
    bp = 1000
    if d.month in (4, 5, 12):  # summer and year-end demand
        bp += 80
    if d.weekday() >= 5:       # Saturday / Sunday
        bp += 60
    return bp


def _time_bp(hour: int) -> int:
    """Fare factor by departure hour — a simulated demand curve (‰)."""
    if hour < 7:
        return 920   # red-eye / early-morning discount
    if hour < 10:
        return 1070  # morning peak
    if hour < 16:
        return 1000  # midday
    if hour < 20:
        return 1080  # evening peak
    return 950       # late evening


def _options_for(origin: str, destination: str, date: Optional[str]) -> list:
    """Five deterministic simulated quotes for one route + travel date.

    Every field — airline rotation, departure slots, base fare, block time
    and which options make a stop — derives from a stable hash of
    ``(origin, destination, date)`` (``_seed_of``), so identical requests
    yield identical lists while different routes or dates differ. Integer
    arithmetic only, no randomness and no clock: simulated demo data,
    never real or currently available inventory.
    """
    seed = _seed_of(origin, destination, date or "")
    r1 = _mix(seed)
    r2 = _mix(r1)
    r3 = _mix(r2)
    r4 = _mix(r3)
    r5 = _mix(r4)
    base_fare = 3600 + r3 % 3800        # route/date base fare
    base_min = 70 + r4 % 120            # non-stop block time (minutes)
    stop_idx = {r5 % 5, _mix(r5) % 5}   # 1-2 of the five make one stop
    season = _season_bp(date)
    used_prices = set()
    options = []
    for i in range(5):
        airline = _AIRLINES[(r1 + i) % len(_AIRLINES)]
        slot = _DEPART_SLOTS[(r2 + i) % len(_DEPART_SLOTS)]
        depart = slot[0] * 60 + slot[1] + (r3 + i * 13) % 11
        has_stop = i in stop_idx
        duration = base_min + (r4 + i * 29) % 25
        if has_stop:
            duration += 55 + (r3 + i) % 35
        price = base_fare * season // 1000
        price = price * _time_bp(depart // 60) // 1000
        price = price * (880 if has_stop else 1000) // 1000
        price = price * _AIRLINE_BP[airline] // 1000
        price = (price + 5) // 10 * 10            # round to a plausible ₹10 step
        while price in used_prices:               # keep quoted fares distinct
            price += 10
        used_prices.add(price)
        arrive = (depart + duration) % 1440
        options.append(
            {
                "id": f"opt-{origin[:3].lower()}{destination[:3].lower()}-{i + 1}",
                "airline": airline,
                "depart": f"{depart // 60:02d}:{depart % 60:02d}",
                "arrive": f"{arrive // 60:02d}:{arrive % 60:02d}",
                "duration": f"{duration // 60}h {duration % 60:02d}m",
                "stops": "1 stop" if has_stop else "Non-stop",
                "price": f"₹{price:,}",
            }
        )
    return options


def _price_value(price: Any) -> int:
    """``"₹4,120"`` -> ``4120`` (the currency string is display-only)."""
    digits = re.sub(r"\D", "", str(price))
    return int(digits) if digits else 0


def _duration_value(duration: Any) -> int:
    """``"2h 35m"`` -> ``155`` minutes (drives the fastest ordering)."""
    m = re.match(r"(\d+)h\s*(\d+)m", str(duration))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0


async def _server_tool(
    *, intent: str, call_id: str, state: Any
) -> Dict[str, Any]:
    """The served path's flight search: bounded and deterministic.

    Replaces ``DemoFlightTool`` on this path — that tool's non-Mumbai
    branch waits forever (``demo.py``: a scripted-demo hook), which hangs
    any live request. Behaviour contract preserved:

    - always completes, after ``FLIGHT_SEARCH_DELAY_S`` (the runtime has
      no tool timeout, so the tool itself must be bounded);
    - cancellation stays *advisory*, exactly like the demo tool's blocked
      branch: a superseded call swallows ``CancelledError`` and returns
      its late result, which the Acceptance Gate rejects as STALE. The
      runtime's cancellation and stale-result checks are used, never
      weakened.

    The answer reflects the actual query: origin/destination, travel date
    and preference (cheapest/fastest/earliest) are parsed from ``intent``
    — ``call_id``/``state`` are accepted to honour the tool contract
    (``coordinator.py:62``) but carry no route fields (``state.py``) —
    the quotes are generated from route + date, then ordered by the
    preference with the winner identified in ``_answer_text``. All
    simulated, all deterministic: identical inputs return identical
    results.

    A call also *continues* its session's query: ``intent`` is the
    updated state's text (``_supersede`` spawns every call with the
    latest intent, ``coordinator.py:227``) and is merged through
    ``_query_of`` with the previous query stored under the updated
    state's ``session_id`` (``_QUERY_CONTEXT``). An interrupt that
    revises only the destination therefore keeps the origin, travel
    date and preference of the request it replaces. The merge runs
    *after* the delay: a predecessor writes its context at most one
    delay after its own spawn (sooner still when cancellation wakes it
    early), while a revision spawns later and reads a full delay after
    that — the query being replaced is always stored first.
    """
    try:
        await asyncio.sleep(FLIGHT_SEARCH_DELAY_S)
    except asyncio.CancelledError:
        pass  # late truth for a superseded call: the gate decides
    session_id = getattr(state, "session_id", None)
    previous = _QUERY_CONTEXT.get(session_id) if session_id else None
    query = _query_of(intent, previous)
    if session_id:
        _QUERY_CONTEXT[session_id] = query
    origin, destination = query["origin"], query["destination"]
    date = query["date"]
    preference = query["preference"]
    options = _options_for(origin, destination, date)
    if preference == "cheapest":
        options.sort(
            key=lambda o: (_price_value(o["price"]), o["depart"], o["airline"])
        )
    elif preference == "fastest":
        options.sort(
            key=lambda o: (_duration_value(o["duration"]), o["depart"], o["airline"])
        )
    elif preference == "earliest":
        options.sort(key=lambda o: (o["depart"], o["airline"]))
    prices = [_price_value(o["price"]) for o in options]
    return {
        "origin": origin,
        "destination": destination,
        "date": date,
        "preference": preference,
        "options": options,
        "min_price": f"₹{min(prices):,}",
        "max_price": f"₹{max(prices):,}",
    }


class _Session:
    """Runtime state owned by exactly one WebSocket connection."""

    def __init__(self, session_id: str, websocket: WebSocket) -> None:
        self.session_id = session_id
        self.websocket = websocket
        self.tool_name = DEFAULT_TOOL_NAME

        # Bounded server tool (see _server_tool): satisfies the
        # Coordinator's tool contract exactly and always finishes — unlike
        # DemoFlightTool's scripted non-Mumbai branch, which waits forever.
        self.tool = _server_tool

        self.coordinator = Coordinator(
            session_id, tool=self.tool, tool_name=self.tool_name
        )

        # Forwarding bookkeeping (owned by the forwarder task only).
        self.cursor = 0
        self.completed_sent: Set[str] = set()
        self.send_lock = asyncio.Lock()

        self.run_task: Optional[asyncio.Task] = None
        self.forward_task: Optional[asyncio.Task] = None

    async def send_frame(self, frame: Dict[str, Any]) -> None:
        """Send one JSON frame; shared lock keeps ASGI sends from interleaving."""
        async with self.send_lock:
            await self.websocket.send_text(
                # default=str: a payload value the runtime may one day put
                # in a payload must never crash the forwarder.
                json.dumps(frame, ensure_ascii=False, default=str)
            )


# --------------------------------------------------------------- frame helpers
def _decode_frame(raw: str) -> Optional[Dict[str, Any]]:
    """Parse an inbound frame; ``None`` means malformed (caller ignores it)."""
    try:
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded


def _now_ms() -> int:
    # The frontend works in milliseconds (MockAgentClient.ts:62 uses Date.now()).
    return int(time.time() * 1000)


def _serialize_event(event: Event, session: _Session) -> Dict[str, Any]:
    """Coordinator event -> frontend frame (format required at /ws).

    Optional fields are only added when meaningful: ``call_id`` when the
    payload carries one, ``state_version`` from the event's authoritative
    version, ``ts`` in milliseconds.
    """
    payload = dict(event.payload)  # JSON-safe copy; the Event stays immutable
    etype = event.event_type.value

    if etype == EventType.TASK_STARTED.value:
        # The runtime payload has no tool name; the frontend reads
        # payload.tool_name (RealAgentClient.ts:36) and the adapter knows it.
        payload.setdefault("tool_name", session.tool_name)
    elif etype == EventType.STATE_VERSION_CHANGED.value:
        # Same data under the key names the UI renders (LiveAgent.tsx:199).
        payload.setdefault("fromVersion", payload.get("from"))
        payload.setdefault("toVersion", payload.get("to"))

    frame: Dict[str, Any] = {
        "type": _TYPE_MAP.get(etype, etype),
        "session_id": event.session_id,
        "payload": payload,
        "state_version": event.version,
        "ts": int(event.timestamp * 1000),
    }
    call_id = payload.get("call_id")
    if isinstance(call_id, str) and call_id:
        frame["call_id"] = call_id
    return frame


def _user_input_frame(session_id: str, text: str) -> Dict[str, Any]:
    """Echo of an accepted inbound message (mirrors MockAgentClient.ts:85)."""
    return {
        "type": "USER_INPUT",
        "session_id": session_id,
        "payload": {"summary": text, "text": text},
        "ts": _now_ms(),
    }


def _interruption_frame(session_id: str, text: str) -> Dict[str, Any]:
    """Echo of an interrupt (mirrors MockAgentClient.ts:136)."""
    return {
        "type": "INTERRUPTION",
        "session_id": session_id,
        "payload": {
            "summary": "User interruption detected",
            "text": text,
            "auraText": "Got it — switching right away.",
        },
        "ts": _now_ms(),
    }


def _completion_frame(session: _Session, call_id: str) -> Dict[str, Any]:
    """Presentation frame for a silently accepted result (see module docstring)."""
    coordinator = session.coordinator
    try:
        version = coordinator.registry.get(call_id).spawn_version
    except Exception:
        # Registry records are never deleted; this is belt-and-braces only.
        version = coordinator.current_version
    return {
        "type": "TOOL_COMPLETED",
        "session_id": session.session_id,
        "payload": {
            "summary": f"{call_id} completed",
            "call_id": call_id,
            "tool_name": session.tool_name,
        },
        "call_id": call_id,
        "state_version": version,
        "ts": _now_ms(),
    }


def _answer_text(result: Any) -> str:
    """Readable assistant answer derived from an accepted tool result.

    Names the route, the travel date (when one was parsed) and the user's
    preference, and states plainly that the quotes are simulated demo
    data — never real or currently available inventory. ``result`` is the
    dict ``_server_tool`` returns; unknown shapes degrade gracefully.
    """
    if not isinstance(result, dict):
        return f"Search complete. {result}"
    options = result.get("options")
    if not isinstance(options, list) or not options:
        return "No simulated options found for that search."
    origin = str(result.get("origin") or "")
    destination = str(result.get("destination") or "your destination")
    date = result.get("date")
    preference = result.get("preference")
    first = options[0]
    airline = str(first.get("airline", "an option"))
    price = str(first.get("price", ""))
    route = f"{origin} → {destination}" if origin else destination
    when = f" on {date}" if date else ""
    count = len(options)
    low = str(result.get("min_price") or price)
    high = str(result.get("max_price") or price)
    if preference == "cheapest":
        head = (
            f"Cheapest simulated fare {route}{when}: {airline} at {price}"
            f" — {count} options sorted by price ({low}–{high})"
        )
    elif preference == "fastest":
        head = (
            f"Fastest simulated option {route}{when}: {airline}, "
            f"{first.get('duration', '')} ({price})"
            f" — {count} options sorted by duration"
        )
    elif preference == "earliest":
        head = (
            f"Earliest simulated option {route}{when}: {airline} departs "
            f"{first.get('depart', '')} ({price})"
            f" — {count} options sorted by departure"
        )
    else:
        head = (
            f"Found {count} simulated options {route}{when}, "
            f"fares {low}–{high}"
        )
    return f"{head}. Simulated demo results — not real or currently available."


def _final_response_frame(session: _Session, call_id: str) -> Dict[str, Any]:
    """Final answer frame for a gate-accepted result (``payload.auraText``).

    Only ever built from ``Coordinator.accepted_results`` — outcomes the
    Acceptance Gate admitted as accepted **and** current — so a cancelled
    or stale call can never produce a FINAL_RESPONSE.
    """
    coordinator = session.coordinator
    result = coordinator.accepted_results.get(call_id)
    try:
        version = coordinator.registry.get(call_id).spawn_version
    except Exception:
        # Registry records are never deleted; this is belt-and-braces only.
        version = coordinator.current_version
    payload: Dict[str, Any] = {
        "summary": "Results ready",  # MockAgentClient.ts:114
        "call_id": call_id,
        "tool_name": session.tool_name,
        "auraText": _answer_text(result),
    }
    if isinstance(result, dict) and isinstance(result.get("options"), list):
        # The quotes ride along (additive) for the results panel.
        payload["options"] = result["options"]
    return {
        "type": "FINAL_RESPONSE",
        "session_id": session.session_id,
        "payload": payload,
        "call_id": call_id,
        "state_version": version,
        "ts": _now_ms(),
    }


# ------------------------------------------------------------------- lifecycle
def _open_session(session_id: str, websocket: WebSocket) -> _Session:
    """Create the session's Coordinator and start its single consumer."""
    session = _Session(session_id, websocket)
    _sessions[session_id] = session
    # Exactly one run() per session; it is the session's only consumer.
    session.run_task = asyncio.create_task(
        session.coordinator.run(), name=f"aura-run:{session_id}"
    )
    session.forward_task = asyncio.create_task(
        _forward(session), name=f"aura-forward:{session_id}"
    )
    return session


async def _close_session(session: _Session) -> None:
    """Disconnect cleanup: no orphaned Coordinator/background tasks remain."""
    _sessions.pop(session.session_id, None)
    _QUERY_CONTEXT.pop(session.session_id, None)
    coordinator = session.coordinator

    # 1. Stop forwarding — the socket is going away.
    if session.forward_task is not None:
        session.forward_task.cancel()

    # 2. Cancel in-flight tools through the public supervisor API so the
    #    Coordinator's watcher tasks can settle instead of being orphaned.
    for call_id in coordinator.active_call_ids:
        coordinator.supervisor.cancel(call_id)
    tool_tasks = []
    for call_id in coordinator.active_call_ids:
        try:
            tool_tasks.append(coordinator.supervisor.task_for(call_id))
        except Exception:
            pass  # nothing to wait for
    if tool_tasks:
        _done, pending = await asyncio.wait(tool_tasks, timeout=CLEANUP_TIMEOUT_S)
        if pending:
            logger.warning(
                "session %s: %d tool task(s) did not settle within %.1fs",
                session.session_id,
                len(pending),
                CLEANUP_TIMEOUT_S,
            )

    # 3. Stop the single consumer (its finally releases the I2 guard).
    if session.run_task is not None:
        session.run_task.cancel()
    await _await_cancelled(session.run_task)
    # 4. Await the forwarder cancelled in step 1.
    await _await_cancelled(session.forward_task)

    # 5. Let watcher tasks finish their mailbox bookkeeping: the tool tasks
    #    have settled, so each watcher only needs a few event-loop passes
    #    (its final mailbox.put does not suspend while the mailbox has room).
    for _ in range(3):
        await asyncio.sleep(0)


async def _await_cancelled(task: Optional[asyncio.Task]) -> None:
    if task is None:
        return
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.warning(
            "background task %s ended with an error",
            task.get_name(),
            exc_info=True,
        )


async def _forward(session: _Session) -> None:
    """Send each newly emitted Coordinator event exactly once, then accepted results."""
    coordinator = session.coordinator
    try:
        while True:
            run_task = session.run_task
            if (
                run_task is not None
                and run_task.done()
                and not run_task.cancelled()
            ):
                # The consumer died (should be impossible: process_next
                # contains handler bugs). Stop rather than stream a stall.
                logger.error(
                    "consumer for session %s stopped: %r",
                    session.session_id,
                    run_task.exception(),
                )
                return

            emitted = coordinator.emitted  # tuple snapshot of the append-only list
            while session.cursor < len(emitted):
                event = emitted[session.cursor]
                await session.send_frame(_serialize_event(event, session))
                session.cursor += 1  # advances only after a successful send

            # Accepted results emit nothing in the runtime; surface them once.
            # Both frames derive from accepted_results (accepted + current
            # only — a cancelled/stale outcome never lands here), so the
            # answer is only ever sent for a current, accepted result.
            for call_id in list(coordinator.accepted_results):
                if call_id in session.completed_sent:
                    continue
                session.completed_sent.add(call_id)
                await session.send_frame(_completion_frame(session, call_id))
                await session.send_frame(
                    _final_response_frame(session, call_id)
                )

            await asyncio.sleep(FORWARD_INTERVAL_S)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Socket gone (send failed): the connection task's finally owns cleanup.
        logger.debug(
            "forwarder for session %s stopped", session.session_id, exc_info=True
        )


async def _submit(
    session: _Session, event_type: EventType, text: str, priority: Priority
) -> None:
    """Enqueue a user fact through the normal mailbox path (no shortcuts)."""
    event = Event(
        session_id=session.session_id,
        event_type=event_type,
        payload={"intent": text},
        actor="user",
    )
    try:
        await session.coordinator.mailbox.put(event, priority=priority)
    except MailboxFull:
        # Only DATA can fail loud (mailbox contract); stay up, change nothing.
        logger.warning(
            "mailbox full for session %s; dropped %s",
            session.session_id,
            event_type.value,
        )


# --------------------------------------------------------------------- endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Accept one browser connection; it owns at most one session runtime."""
    await websocket.accept()
    session: Optional[_Session] = None
    try:
        while True:
            raw = await websocket.receive_text()

            frame = _decode_frame(raw)
            if frame is None:
                logger.warning("dropping malformed frame")
                continue

            kind = frame.get("kind")
            session_id = frame.get("session_id")
            if not isinstance(kind, str) or kind not in _ACCEPTED_KINDS:
                # Includes audio_request/image_request: ignored, never fatal.
                logger.info("ignoring unsupported frame kind: %r", kind)
                continue
            if not isinstance(session_id, str) or not session_id:
                logger.warning("dropping frame without a valid session_id")
                continue

            if session is None:
                if session_id in _sessions:
                    # The runtime is single-consumer: reject the newcomer
                    # loudly instead of double-driving one Coordinator.
                    await websocket.close(
                        code=WS_CLOSE_DUPLICATE_SESSION,
                        reason="session already connected",
                    )
                    return
                session = _open_session(session_id, websocket)
            elif session_id != session.session_id:
                await websocket.close(
                    code=WS_CLOSE_SESSION_MISMATCH, reason="session_id mismatch"
                )
                return

            text = frame.get("text")
            if kind == "text":
                if not isinstance(text, str) or not text.strip():
                    logger.warning("dropping 'text' frame without usable text")
                    continue
                await session.send_frame(_user_input_frame(session_id, text))
                await _submit(session, EventType.USER_INPUT, text, Priority.DATA)
            else:  # kind == "interrupt" (validated above)
                if text is None:
                    text = ""  # pure barge-in: the runtime treats it as a no-op
                if not isinstance(text, str):
                    logger.warning("dropping interrupt frame with non-string text")
                    continue
                await session.send_frame(_interruption_frame(session_id, text))
                # CONTROL: never queues behind pending DATA work.
                await _submit(session, EventType.USER_INTERRUPT, text, Priority.CONTROL)
    except WebSocketDisconnect:
        pass
    except RuntimeError:
        # Send on an already-closed socket (client vanished mid-send).
        logger.debug("socket closed under the handler", exc_info=True)
    finally:
        if session is not None:
            await _close_session(session)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
