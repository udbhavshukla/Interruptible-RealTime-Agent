"""Planner — planning and interruption-aware re-planning for AURA.

The planner takes the current/latest state and determines the actions
that should happen.  Plans are represented structurally (JSON-like dicts)
rather than natural-language text, so the coordination layer can inspect
them for versioning, cancellation, and stale-result rejection.

The planner is deliberately stateless — it receives the current state
and intent from the agent loop and returns a plan.  The agent (or
coordination layer) owns the state mutation and version tracking.
"""

from __future__ import annotations

import re as _re
from typing import Any, Dict, FrozenSet, Optional, Sequence

from .slots import Slots, compare_states, FIXED_SLOTS


# ---------------------------------------------------------------------------
# Plan structure
# ---------------------------------------------------------------------------

Plan = Dict[str, Any]


# Standard plan keys — the coordination layer expects these.
PLAN_STATE_VERSION = "state_version"
PLAN_ACTIONS = "actions"
PLAN_PLAN_ID = "plan_id"


def _next_state_version(current: int) -> int:
    """Increment the state version."""
    return current + 1


# ---------------------------------------------------------------------------
# Generic manifest-driven dispatch (no hardcoded single-tool assumption)
# ---------------------------------------------------------------------------

#: Required-arg names that carry a city/destination value.
_CITY_ARG_NAMES = frozenset({"city", "destination", "town", "location"})

#: Required-arg names that carry the user's raw question/request text.
_QUERY_ARG_NAMES = frozenset({"query", "question", "q"})

#: Words suggesting the user refers to a device/manual/port ("this port",
#: "my TV", "blinking LED", ...). Used to gate manual-style tools so
#: plain chitchat never triggers them.
_MANUAL_HINT_WORDS = frozenset({
    "port", "this", "device", "manual", "hdmi", "usb", "led", "tv",
    "screen", "laptop", "phone", "washer", "error", "troubleshoot",
    "blinking", "cable", "manuals",
})

#: Optional-arg names that may be filled from the date slot.
_DATE_ARG_NAMES = frozenset({"date", "day", "departure_date"})


def _resolve_arg_value(
    arg_name: str,
    arg_spec: Dict[str, Any],
    merged: Slots,
    raw_text: str,
) -> Any:
    """Resolve a value for one declared tool arg, or ``None`` if unknown.

    Only uses existing slots / the raw user text — never invents values.
    Returns the ``None`` sentinel when the arg cannot be filled.
    """
    lowered = arg_name.lower()
    value: Any = None

    if lowered in merged:
        value = merged[lowered]
    elif lowered in _CITY_ARG_NAMES and merged.get("destination"):
        value = merged["destination"]
    elif lowered in _QUERY_ARG_NAMES and raw_text.strip():
        value = raw_text.strip()
    elif lowered in _DATE_ARG_NAMES and merged.get("date"):
        value = merged["date"]
    else:
        return None

    if value is None or (isinstance(value, str) and not value.strip()):
        return None

    # Respect enum constraints: an invalid value means "cannot fill".
    if isinstance(value, str) and "enum" in (arg_spec or {}):
        if value not in arg_spec["enum"]:
            return None
    return value


def _fill_tool_args(
    spec: Dict[str, Any],
    merged: Slots,
    raw_text: str,
    *,
    optional_arrays: bool = False,
) -> Optional[Dict[str, Any]]:
    """Build valid args for one tool schema, or ``None`` if impossible.

    Only includes args declared in the schema. Required object/array args
    we cannot construct cause a skip (handled by later steps).
    """
    declared = spec.get("args", {}) or {}
    args: Dict[str, Any] = {}
    for arg_name, arg_spec in declared.items():
        arg_spec = arg_spec or {}
        if arg_spec.get("required"):
            kind = arg_spec.get("type", "string")
            if kind in ("object", "array") and not optional_arrays:
                return None
            value = _resolve_arg_value(arg_name, arg_spec, merged, raw_text)
            if value is None:
                return None
            args[arg_name] = value
    # Optional args: fill only what we know and can validate.
    for arg_name, arg_spec in declared.items():
        arg_spec = arg_spec or {}
        if arg_spec.get("required") or arg_name in args:
            continue
        kind = arg_spec.get("type", "string")
        if kind in ("object", "array"):
            continue  # constructed by later (vision/booking) steps
        value = _resolve_arg_value(arg_name, arg_spec, merged, raw_text)
        if value is not None:
            args[arg_name] = value
    return args


def _select_manifest_tool(
    tools: Dict[str, Any],
    merged: Slots,
    raw_text: str,
    frame: Dict[str, Any],
    new_version: int,
) -> list[Dict[str, Any]]:
    """Select one manifest tool generically. Returns a 0/1-length action list."""
    lowered_text = raw_text.lower()
    # Strip punctuation so "port?" matches the "port" hint.
    text_words = set(_re.sub(r"[^a-z ]", " ", lowered_text).split())

    # 1. City-like tools (weather_lookup pattern): need a destination.
    if merged.get("destination"):
        for tool_name in sorted(tools):
            if tool_name == "flight_search":
                continue  # handled by the dedicated branch above
            spec = tools[tool_name] or {}
            required = [n for n, s in (spec.get("args", {}) or {}).items()
                        if (s or {}).get("required")]
            if not required or not any(n.lower() in _CITY_ARG_NAMES for n in required):
                continue
            args = _fill_tool_args(spec, merged, raw_text)
            if args is not None:
                return [{
                    "action": tool_name,
                    "arguments": args,
                    "call_id": f"call_{new_version}",
                }]

    # 2. Query-style tools (lookup_manual pattern): need the user's text
    # plus a device/port signal or a video frame for context.
    if raw_text.strip() and (frame or (text_words & _MANUAL_HINT_WORDS)):
        for tool_name in sorted(tools):
            if tool_name == "flight_search":
                continue
            spec = tools[tool_name] or {}
            required = [n for n, s in (spec.get("args", {}) or {}).items()
                        if (s or {}).get("required")]
            if not required or not any(n.lower() in _QUERY_ARG_NAMES for n in required):
                continue
            args = _fill_tool_args(spec, merged, raw_text)
            if args is not None:
                return [{
                    "action": tool_name,
                    "arguments": args,
                    "call_id": f"call_{new_version}",
                }]

    return []


def make_plan(
    state: Dict[str, Any],
    intent: object,  # IntentInfo or simple namespace with .type and .slots
    tools: Optional[Dict[str, Any]] = None,
) -> Plan:
    """Produce a structural plan from the current *state* and *intent*.

    Parameters
    ----------
    state:
        Current session state dict with at least ``"slots"`` and
        optionally ``"state_version"``.
    intent:
        An IntentInfo instance (or equivalent) describing what the user
        wants.  The intent's ``.slots`` are merged into the session state.
    tools:
        Optional dict of tool manifests (``{name: schema}``).  If provided,
        the planner validates that required args are present.

    Returns
    -------
    plan : dict
        Structural plan with keys:
        - ``plan_id``: unique identifier
        - ``state_version``: monotonic version this plan was created from
        - ``actions``: list of ``{action, arguments}`` dicts
    """
    # Initialise state version
    current_version = state.get("state_version", 0)
    new_version = _next_state_version(current_version)

    # Gather slots from intent
    intent_slots: Slots = Slots()
    if hasattr(intent, "slots") and intent.slots:
        intent_slots = Slots(intent.slots)
    elif isinstance(intent, dict):
        intent_slots = Slots(intent.get("slots", {}))

    # Merge with existing state slots (new intent slots override old,
    # but unaffected slots are preserved)
    existing_slots = Slots(state.get("slots", {}))
    merged = Slots(existing_slots | intent_slots)  # new overrides old where both exist

    # Determine required slots for this intent; for flight_search they
    # are {origin, destination, date}.  If tools are provided we can
    # check which are missing; otherwise we just report.
    required = FIXED_SLOTS.get("flight_search", frozenset())

    # Check which required slots are still missing
    missing = merged.required_missing(required)

    # Build actions: for flight_search, a single tool call if we have
    # enough info; otherwise, no tool call (fast path ACK only, wait
    # for more info).
    actions: list[Dict[str, Any]] = []
    # Pending chained booking (filled when a new request asks to book;
    # book_flight itself is emitted only after the search result arrives).
    plan_booking_pending: Dict[str, Any] = {}

    if intent.type in ("new", "correction") and intent_slots:
        # New request — plan a tool call if we have the minimum info.
        # A correction with freshly extracted slots re-plans too; a
        # correction with NO slots plans nothing (never re-issue stale args).
        # flight_search requires destination only (date optional).
        has_dest = "destination" in merged and merged["destination"] != ""
        has_date = "date" in merged and merged["date"] != ""

        if has_dest:
            args: Dict[str, Any] = {
                "destination": merged.get("destination", ""),
            }
            if has_date:
                args["date"] = merged.get("date", "")

            # Validate against tool manifest if provided
            if tools and "flight_search" in tools:
                spec = tools["flight_search"]
                # Simple arg-presence check
                for arg_name in spec.get("args", {}):
                    if spec["args"][arg_name].get("required") and arg_name not in args:
                        # Missing required arg — don't call tool, fast path
                        # ACK only
                        args = {}
                        break

            if args:
                actions.append(
                    {
                        "action": "flight_search",
                        "arguments": args,
                        "call_id": f"call_{new_version}",
                    }
                )
                # Chained booking: the request also asks to book (e.g.
                # "... and book the 8AM one for Alice"). Record the
                # pending booking here, but emit ONLY flight_search —
                # book_flight is created after the search result arrives.
                raw_text = str(state.get("last_text", ""))
                passenger = merged.get("passenger_name", "")
                if passenger and _re.search(r"\bbook\b|\breserve\b", raw_text, _re.I):
                    plan_booking: Dict[str, Any] = {"passenger_name": passenger}
                    mt = _re.search(r"(\d{1,2}\s?(?:AM|PM))", raw_text, _re.I)
                    if mt:
                        plan_booking["time_hint"] = _re.sub(
                            r"\s+", "", mt.group(1).upper())
                    plan_booking_pending = plan_booking
                else:
                    plan_booking_pending = {}
        # If we don't have enough info, actions stays empty — the agent
        # will send a fast-path ACK asking for missing info.

    # Generic manifest-driven fallback (runs only when the flight branch
    # produced nothing): city-like tools (weather pattern) and
    # query-style tools (manual pattern), chosen from the live manifest.
    # Runs on raw text too (e.g. "What is this port?" carries no slots).
    if not actions and tools and (intent_slots or str(state.get("last_text", "")).strip()):
        raw_text = str(state.get("last_text", ""))
        frame = state.get("frame", {}) or {}
        if not isinstance(frame, dict):
            frame = {}
        actions = _select_manifest_tool(tools, merged, raw_text, frame, new_version)

    # If the intent is a correction/addition/retraction, actions may be
    # empty here; the coordination layer decides whether to re-plan or
    # cancel/continue based on the diff.

    plan: Plan = {
        PLAN_PLAN_ID: f"plan_{new_version}",
        PLAN_STATE_VERSION: new_version,
        PLAN_ACTIONS: actions,
    }
    if plan_booking_pending:
        plan["booking_pending"] = plan_booking_pending

    # Attach a human-readable snapshot of the merged slots for debugging
    # (the coordination layer / scorer reads state_snapshot from final_response)
    plan["_snapshot_slots"] = dict(merged)

    # Preserve the latest video frame reference (if any) so the later
    # visual step can build an image embedding for hybrid search.
    _frame = state.get("frame", {}) or {}
    if isinstance(_frame, dict) and _frame.get("image_ref"):
        plan["frame_ref"] = dict(_frame)

    return plan


def replan(
    state: Dict[str, Any],
    diff: Dict[str, Dict[str, str]],
    current_plan: Plan,
    tools: Optional[Dict[str, Any]] = None,
) -> Plan:
    """Produce a minimal new plan after a user interruption.

    The *diff* is the output of :func:`slots.compare_states`, showing
    which slots changed from old to new.

    Key design goals:
    1. Preserve unaffected slots from the old state.
    2. Increment state_version.
    3. Mark only the actions that depend on changed slots for re-planning.
    4. Do NOT unnecessarily discard still-valid work.

    Parameters
    ----------
    state:
        Current session state (will be mutated to the new version).
    diff:
        Output of ``compare_states(old_slots, new_slots)`` — see slots.py.
    current_plan:
        The plan that was previously executing (has ``state_version`` and
        ``actions``).
    tools:
        Optional tool manifests for arg validation.

    Returns
    -------
    new_plan : dict
        A fresh plan referencing the latest state version.
    """
    new_version = _next_state_version(
        state.get("state_version", current_plan.get("state_version", 0))
    )

    # Start from the current plan's structure
    new_plan: Plan = {
        "plan_id": current_plan.get("plan_id", f"plan_{new_version}"),
        PLAN_STATE_VERSION: new_version,
        PLAN_ACTIONS: [],
    }

    # Determine which actions from the current plan are affected by the slot changes.
    # An action is "affected" if any of its arguments overlap with the changed slots.
    changed_slots: FrozenSet[str] = frozenset(diff.get("changes", {}).keys())

    # Build a map of which slot each action depends on
    # (for flight_search, it depends on origin AND destination AND date)
    affected_actions: list[Dict[str, Any]] = []

    for action_entry in current_plan.get(PLAN_ACTIONS, []):
        args = action_entry.get("arguments", {})
        # Check if any changed slot appears in this action's arguments
        deps: FrozenSet[str] = frozenset(
            s for s in changed_slots if s in args
        )
        if deps:
            # This action depends on a changed slot → it must be re-planned
            # Create a new action with updated arguments
            new_args: Dict[str, Any] = dict(args)
            for slot in deps:
                # Pull the new value from diff
                slot_diff = diff.get("changes", {}).get(slot, {})
                new_val = slot_diff.get("new", "")
                if new_val:
                    new_args[slot] = new_val
            # Also preserve any unchanged deps from the old args
            for slot in args:
                if slot not in deps:
                    new_args.setdefault(slot, args[slot])

            affected_actions.append(
                {
                    "action": action_entry.get("action"),
                    "arguments": new_args,
                    "call_id": f"call_{new_version}",
                }
            )
        else:
            # Action does NOT depend on changed slots → preserve it
            # (copy the whole entry, but update call_id to new version)
            preserved = dict(action_entry)
            preserved["call_id"] = f"call_{new_version}"
            # Keep the action as-is in the new plan
            new_plan.setdefault(PLAN_ACTIONS, []).append(preserved)

    # If there are affected actions, add them
    if affected_actions:
        new_plan[PLAN_ACTIONS] = affected_actions + new_plan.get(PLAN_ACTIONS, [])

    # If no actions were preserved or affected, the plan may be empty
    # (the agent will send a fast-path ACK and wait for more info).

    # Attach snapshot slots for the coordination layer
    # Merge: preserve old unaffected slots, apply new changed slots
    old_slots = Slots(state.get("slots", {}))
    changed_dict = diff.get("changes", {})
    preserved_dict: Dict[str, str] = {}
    for slot in old_slots:
        if slot not in changed_dict:
            preserved_dict[slot] = old_slots[slot]
    # Apply new values for changed slots
    for slot, change in changed_dict.items():
        preserved_dict[slot] = change.get("new", "")

    new_plan["_snapshot_slots"] = dict(preserved_dict)

    return new_plan