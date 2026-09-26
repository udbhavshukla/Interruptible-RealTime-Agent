"""Your entry point (ParticipantAgent) and a minimal reference agent (BaselineAgent).

Contract: __init__(in_queue, out_queue), optional `async def setup()`, `async def run()`.
Member 1 modules (intent, slots, planner, fastpath) are integrated for
interruptible real-time agent behavior — see src/agent/ for the components.

Read docs/PROTOCOL.md §5 before wiring an LLM: run() shares the harness event loop,
so a blocking (sync) API call freezes the whole simulation.
"""

from __future__ import annotations
import asyncio
import re
from typing import Any, Dict, List, Optional

from src.agent import intent as intent_mod
from src.agent import slots as slots_mod
from src.agent import planner as planner_mod
from src.agent import fastpath as fastpath_mod

CITY_CANON = {
    "boston": "Boston", "bos": "Boston",
    "new york": "New York", "nyc": "New York",
    "chicago": "Chicago", "denver": "Denver",
    "seattle": "Seattle", "miami": "Miami", "austin": "Austin",
}
_CITY_PATTERN = re.compile(
    r"\b(" + "|".join(sorted(CITY_CANON, key=len, reverse=True)) + r")\b", re.I)


# ---------------------------------------------------------------------------
# Member 1 integration — lazily initialised after tool_manifest
# ---------------------------------------------------------------------------

_MEMBER1: Dict[str, Any] | None = None


def _member1_init(tools: Dict[str, Any]) -> None:
    """Initialise Member 1 components after the tool_manifest event.

    This is idempotent — called once when the manifest arrives.
    """
    global _MEMBER1
    if _MEMBER1 is not None:
        return

    # Slot management
    _MEMBER1 = {
        "slots": slots_mod.Slots(),
        "compare": slots_mod.compare_states,
        "FIXED_SLOTS": slots_mod.FIXED_SLOTS,
    }

    # Intent classification
    _MEMBER1["classify"] = intent_mod.classify_intent

    # Planning / re-planning
    _MEMBER1["make_plan"] = planner_mod.make_plan
    _MEMBER1["replan"] = planner_mod.replan

    # Fast path ACK generation
    _MEMBER1["fastpath"] = fastpath_mod

    # Tools for plan validation
    _MEMBER1["tools"] = tools


# ---------------------------------------------------------------------------
# ParticipantAgent
# ---------------------------------------------------------------------------

class ParticipantAgent:
    def __init__(self, in_queue: asyncio.Queue, out_queue: asyncio.Queue):
        self.in_q = in_queue
        self.out_q = out_queue
        self.tools: Dict[str, Any] = {}
        # Session state — versioned
        self.state: Dict[str, Any] = {
            "intent": None,
            "slots": {},
            "state_version": 0,
        }
        # Track pending tool calls: call_id -> {"api", "args", "version"}
        self.pending: Dict[str, Dict] = {}
        # Current executing plan
        self.current_plan: Dict[str, Any] = {}
        # Buffer for multi-chunk turns
        self.buffer: List[str] = []
        # Buffer for multi-clip audio turns (refs only; no transcript)
        self.audio_buffer: List[str] = []
        # Latest video frame context (preserved for the visual step)
        self.latest_frame: Dict[str, Any] = {}
        # Last successful tool results by api_name (runtime grounding only)
        self.last_results: Dict[str, Dict] = {}

    async def setup(self):
        """Optional. Load models / warm clients here — runs before the clock starts."""
        pass

    async def run(self):
        """Main event loop: read events from in_q, process, send actions to out_q."""
        async for event in self._event_stream():
            await self._process_event(event)

    async def _event_stream(self):
        """Yield events from in_q indefinitely (until cancelled)."""
        while True:
            event = await self.in_q.get()
            yield event

    async def _process_event(self, event: Dict[str, Any]) -> None:
        """Dispatch event to the appropriate handler."""
        etype = event.get("event_type")

        if etype == "tool_manifest":
            self.tools = event.get("payload", {}).get("tools", {})
            _member1_init(self.tools)

        elif etype == "user_speech_chunk":
            await self._on_user_speech(event)

        elif etype == "user_audio_chunk":
            await self._on_user_audio(event.get("payload", {}) or {})

        elif etype == "video_frame":
            # Keep the latest frame for manual-style dispatch/grounding.
            payload = event.get("payload", {}) or {}
            if isinstance(payload, dict):
                self.latest_frame = dict(payload)

        elif etype == "interruption":
            await self._on_interruption(event.get("payload", {}).get("text", ""))

        elif etype == "tool_result":
            await self._on_tool_result(event.get("payload", {}))

        elif etype == "scenario_end":
            # Final response with latest state snapshot
            await self._final_response()

    async def _on_user_audio(self, payload: Dict[str, Any]) -> None:
        """Handle a user_audio_chunk event (raw audio, no transcript).

        No offline transcription exists, so the content is genuinely
        ambiguous: acknowledge fast (latency), buffer multi-clip turns,
        and clarify at end-of-turn. Never fire a tool on a guess.
        """
        ref = payload.get("audio_ref", "")
        end_of_turn = payload.get("end_of_turn", False)
        if ref:
            self.audio_buffer.append(ref)

        if len(self.audio_buffer) <= 1 and not end_of_turn:
            filler = "Give me a moment to listen carefully."
        elif not end_of_turn:
            filler = "Still listening — almost done."
        elif len(self.audio_buffer) <= 1:
            filler = "Got it — let me make sure I heard you correctly."
        else:
            filler = "Thanks — let me confirm the full request."
        await self.out_q.put({
            "action": "filler_speech",
            "payload": {"text": filler},
        })

        if not end_of_turn:
            return
        self.audio_buffer = []
        await self.out_q.put({
            "action": "clarification_request",
            "payload": {"text": "Just to confirm — which city did you say?"},
        })

    async def _on_user_speech(self, event: Dict[str, Any]) -> None:
        """Handle a user_speech_chunk event."""
        text = event.get("payload", {}).get("text", "")
        end_of_turn = event.get("payload", {}).get("end_of_turn", False)

        if not end_of_turn:
            # Accumulate but don't process yet
            self.buffer.append(text)
            return

        # Turn is complete — include final chunk and process the full utterance
        turn = " ".join(self.buffer + [text]).strip()
        self.buffer = []

        if not turn:
            return

        # --- Member 1: intent classification ---
        classified: intent_mod.IntentInfo = _MEMBER1["classify"](turn)

        # --- Member 1: slot extraction ---
        # (Already inside classify_intent, but we also compute a diff
        #  against the current state for correction-awareness.)
        new_slots = slots_mod.Slots()
        for k, v in classified.slots.items():
            if v:  # only non-empty
                new_slots[k] = v

        # Compare with current state to detect what changed
        current_slots = slots_mod.Slots(self.state.get("slots", {}))
        diff = slots_mod.compare_states(current_slots, new_slots)

        # --- Update state with new slots (localized correction) ---
        # Preserve unaffected slots, apply changed ones
        updated_slots = dict(current_slots)
        for slot, change in diff.get("changes", {}).items():
            updated_slots[slot] = change.get("new", "")
        # Also apply any new slots from the utterance that aren't corrections
        for slot, value in new_slots.items():
            if slot not in diff.get("changes", {}):
                # New information addition or initial request
                updated_slots[slot] = value

        # Update state
        new_version = _MEMBER1["compare"](
            slots_mod.Slots(self.state.get("slots", {})),
            slots_mod.Slots(updated_slots),
        )
        # Actually, let's just increment the version
        new_version = self.state.get("state_version", 0) + 1

        self.state = {
            "intent": classified.type,
            "slots": updated_slots,
            "state_version": new_version,
        }

        # --- Member 1: plan making ---
        # Pass raw text + latest frame through (copies only; the stored
        # state shape is unchanged) so generic dispatch can build
        # query args and preserve frame context.
        plan_state = dict(self.state)
        plan_state["last_text"] = turn
        plan_state["frame"] = dict(self.latest_frame)
        plan = _MEMBER1["make_plan"](
            plan_state,
            classified,
            tools=_MEMBER1.get("tools", self.tools),
        )

        # Store the current plan for potential interruption handling
        self.current_plan = plan

        # --- Fast Path ACK (immediate, never blocks) ---
        ack_text = _MEMBER1["fastpath"].generate_ack(
            classified,
            interrupt=(classified.type != "new"),
        )
        await self.out_q.put({
            "action": "filler_speech",
            "payload": {"text": ack_text},
        })

        # --- If we have a plan with actions, emit tool_call ---
        actions = plan.get("actions", [])
        if actions:
            # Use the first action's arguments and assign a call_id
            action = actions[0]
            args = action.get("arguments", {})
            api_name = action.get("action", "flight_search")
            call_id = action.get("call_id", f"call_{new_version}")

            # Emit tool_call
            await self.out_q.put({
                "action": "tool_call",
                "payload": {
                    "api_name": api_name,
                    "args": args,
                    "call_id": call_id,
                },
            })

            # Track the pending call with its state version
            self.pending[call_id] = {
                "api": api_name,
                "args": args,
                "version": new_version,
            }

    async def _on_interruption(self, text: str) -> None:
        """Handle an interruption event from the user.

        Fresh-turn replan: the interruption text is classified/extracted
        like a new turn, all in-flight work is cancelled (stale by
        definition), state takes the new non-empty values, and a fresh
        plan is built. Stale arguments are never re-issued.
        """
        if not text.strip():
            return

        # --- Member 1: classify the interruption ---
        classified: intent_mod.IntentInfo = _MEMBER1["classify"](text)

        # --- Member 1: extract new slots from the interruption ---
        new_slots = slots_mod.Slots()
        for k, v in classified.slots.items():
            if v:
                new_slots[k] = v

        current_slots = slots_mod.Slots(self.state.get("slots", {}))

        # --- Cancel all in-flight work (the interruption invalidates it) ---
        for call_id in list(self.pending):
            await self.out_q.put({
                "action": "cancel_tool",
                "payload": {"call_id": call_id},
            })
            del self.pending[call_id]

        # --- Update state: new non-empty values replace old ones ---
        # Empty/missing slots mean "no information", never a deletion.
        updated = dict(current_slots)
        for slot, value in new_slots.items():
            if value:
                updated[slot] = value

        new_version = self.state.get("state_version", 0) + 1
        self.state = {
            "intent": classified.type,
            "slots": updated,
            "state_version": new_version,
        }

        # --- Fresh replan from the corrected state (never reuse old args) ---
        plan_state = dict(self.state)
        plan_state["last_text"] = text
        plan_state["frame"] = dict(self.latest_frame)
        self.current_plan = _MEMBER1["make_plan"](
            plan_state,
            classified,
            tools=_MEMBER1.get("tools", self.tools),
        )

        # --- Fast Path ACK for the interruption (content-aware when possible) ---
        if new_slots.get("destination"):
            affected = "destination"
        elif new_slots:
            affected = next(iter(new_slots))
        else:
            affected = "destination"
        ack_text = _MEMBER1["fastpath"].ack_for_interruption(
            current_slots,
            slots_mod.Slots(updated),
            affected,
        )
        await self.out_q.put({
            "action": "filler_speech",
            "payload": {"text": ack_text},
        })

        # --- Emit the fresh plan's action, if it earned one ---
        actions = self.current_plan.get("actions", [])
        if actions:
            action = actions[0]
            args = action.get("arguments", {})
            api_name = action.get("action", "flight_search")
            call_id = action.get("call_id", f"call_{new_version}")

            await self.out_q.put({
                "action": "tool_call",
                "payload": {
                    "api_name": api_name,
                    "args": args,
                    "call_id": call_id,
                },
            })

            self.pending[call_id] = {
                "api": api_name,
                "args": args,
                "version": new_version,
            }

    async def _on_tool_result(self, payload: Dict[str, Any]) -> None:
        """Handle a tool_result event from the harness."""
        call_id = payload.get("call_id", "")

        # If this call was already cancelled/removed, ignore
        if call_id not in self.pending:
            # Stale result — could be a late arrival after cancellation.
            # The coordination layer / scorer handles version-based rejection,
            # but we still need to avoid crashing.
            return

        call_info = self.pending[call_id]
        call_version = call_info.get("version", 0)

        # Check version: if the result's state_version doesn't match current,
        # it's stale and should be rejected by the coordination layer.
        result_version = payload.get("state_version", call_version)
        current_version = self.state.get("state_version", 0)

        stale = result_version != current_version and result_version < current_version
        stored = False
        if stale:
            # Stale result from an older version — ignore/reject
            # (The harness scorer will also flag this; we just don't act on it.)
            pass
        else:
            # Valid result — store successful results for final grounding.
            if payload.get("status") == "success":
                api_name = payload.get("api_name", call_info.get("api", ""))
                result = payload.get("result", {})
                if isinstance(result, dict) and api_name:
                    self.last_results[api_name] = result
                    stored = True
            else:
                # Failed read-only tool (e.g. timeout): retry once with a
                # NEW call_id. Never auto-retry state-modifying tools.
                api_name = payload.get("api_name", call_info.get("api", ""))
                result = payload.get("result", {}) or {}
                err = result.get("error", "") if isinstance(result, dict) else ""
                kind = (self.tools.get(api_name, {}) or {}).get("kind", "")
                retried = call_info.get("retries", 0)
                if (kind == "read_only" and retried < 1
                        and err not in ("invalid_args", "unknown_tool")):
                    new_version = self.state.get("state_version", 0)
                    retry_id = f"{call_id}_retry1"
                    args = dict(call_info.get("args", {}))
                    await self.out_q.put({
                        "action": "tool_call",
                        "payload": {"api_name": api_name, "args": args,
                                    "call_id": retry_id},
                    })
                    self.pending[retry_id] = {
                        "api": api_name, "args": args,
                        "version": new_version, "retries": retried + 1,
                    }

        # Remove the call from pending regardless
        self.pending.pop(call_id, None)

        # Chained booking: a successful flight_search may unlock a
        # pending book_flight (recorded on the current plan). Emit it
        # through the normal tool-call path using the actual result.
        if stored and payload.get("api_name", call_info.get("api", "")) == "flight_search":
            await self._maybe_emit_booking()

        # Tool may complete after scenario_end's first final_response;
        # emit a revised grounded final_response (multiple finals allowed).
        if stored:
            await self._final_response()

    @staticmethod
    def _match_flight(flights: Any, time_hint: str) -> Optional[Dict[str, Any]]:
        """Pick the flight matching a hint like "8AM" ("08:00"), else None."""
        if not isinstance(flights, list) or not flights:
            return None
        hint = (time_hint or "").upper().replace(" ", "")
        m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(AM|PM)", hint)
        if m:
            hour = int(m.group(1)) % 12
            if m.group(3) == "PM":
                hour += 12
            for f in flights:
                if not isinstance(f, dict):
                    continue
                dm = re.match(r"(\d{1,2}):(\d{2})", str(f.get("depart", "")))
                if dm and int(dm.group(1)) % 24 == hour % 24:
                    return f
            return None
        for f in flights:
            if isinstance(f, dict) and f.get("flight_id"):
                return f
        return None

    async def _maybe_emit_booking(self) -> None:
        """Emit a pending chained book_flight from the search result."""
        booking = (self.current_plan or {}).get("booking_pending", {}) or {}
        if not booking or (self.current_plan or {}).get("booking_emitted"):
            return
        passenger = booking.get("passenger_name", "")
        if not passenger:
            return
        search_result = self.last_results.get("flight_search", {}) or {}
        flight = self._match_flight(search_result.get("flights"), booking.get("time_hint", ""))
        if not flight or not flight.get("flight_id"):
            return  # never invent a flight_id
        new_version = self.state.get("state_version", 0)
        call_id = f"call_{new_version}_book"
        args = {"flight_id": flight["flight_id"], "passenger_name": passenger}
        await self.out_q.put({
            "action": "tool_call",
            "payload": {"api_name": "book_flight", "args": args, "call_id": call_id},
        })
        self.pending[call_id] = {"api": "book_flight", "args": args, "version": new_version}
        self.current_plan["booking_emitted"] = True

    async def _final_response(self) -> None:
        """Emit a final_response with the latest state snapshot."""
        snap = {
            "intent": self.state.get("intent"),
            "slots": dict(self.state.get("slots", {})),
        }
        await self.out_q.put({
            "action": "final_response",
            "payload": {
                "text": self._synthesize_response(),
            },
            "state_snapshot": snap,
        })

    def _synthesize_response(self) -> str:
        """Produce a final response text from the latest valid state.

        IMPORTANT: This must consume the current state snapshot, NOT
        remember old conversational assumptions.  The coordination layer
        ensures only the latest valid state/results are used.
        """
        slots = self.state.get("slots", {})
        intent = self.state.get("intent")

        # Chained booking confirmation (actual runtime result only).
        book_result = self.last_results.get("book_flight", {})
        if isinstance(book_result, dict) and book_result.get("booking_id"):
            return (f"Booked flight {book_result.get('flight_id', '')} for "
                    f"{slots.get('passenger_name', 'you')}: "
                    f"booking {book_result['booking_id']}.")

        # Weather-style results (actual fields only, never invented).
        # Matches weather_lookup and any hidden tool returning these fields.
        for result in self.last_results.values():
            if not isinstance(result, dict):
                continue
            if not any(k in result for k in ("condition", "temp_f", "temperature", "forecast")):
                continue
            city = slots.get("destination", "") or result.get("city", "")
            bits: list[str] = []
            if result.get("condition"):
                bits.append(str(result["condition"]))
            temp = result.get("temp_f", result.get("temperature", ""))
            if temp != "" and temp is not None:
                try:
                    bits.append(f"{int(float(temp))}°F")
                except (TypeError, ValueError):
                    bits.append(str(temp))
            if result.get("forecast"):
                bits.append(str(result["forecast"]))
            if bits and city:
                return f"The weather in {city} is {', '.join(bits)}."
            if bits:
                return f"Current conditions: {', '.join(bits)}."
            break

        # Manual-style results (actual pages only, never invented).
        for result in self.last_results.values():
            if not isinstance(result, dict) or "pages" not in result:
                continue
            pages = result.get("pages", []) or []
            if pages:
                for p in pages:
                    if not isinstance(p, dict):
                        continue
                    blob = f"{p.get('doc', '')} {p.get('title', '')}".lower()
                    if "hdmi" in blob:
                        return (f"This looks like an HDMI port — see "
                                f"{p.get('doc', 'the manual')}, page "
                                f"{p.get('page', '?')} ({p.get('title', 'HDMI')}).")
                first = pages[0] if isinstance(pages[0], dict) else {}
                return (f"I searched the device manual and found "
                        f"{len(pages)} candidate page(s) "
                        f"({first.get('doc', 'manual')}, page "
                        f"{first.get('page', '?')}), but I can't confidently "
                        f"identify the port from text search alone.")
            return "I searched the device manual but found no matching pages."

        # Ground in actual runtime tool results when available.
        flight_result = self.last_results.get("flight_search", {})
        flights = flight_result.get("flights", []) if isinstance(flight_result, dict) else []
        if flights:
            best = flights[0] if isinstance(flights[0], dict) else {}
            flight_id = best.get("flight_id", "")
            depart = best.get("depart", "")
            price = best.get("price_usd", "")
            dest = slots.get("destination", "")
            if flight_id:
                detail = f"{flight_id}"
                if depart:
                    detail += f" departing {depart}"
                if price != "":
                    detail += f" for ${price}"
                if dest:
                    return f"I found flight options to {dest}: {detail}."
                return f"I found flight options: {detail}."

        # Build a simple speakable summary from the latest state
        parts: list[str] = []

        origin = slots.get("origin")
        dest = slots.get("destination")
        date = slots.get("date")

        if intent and intent == "flight_search":
            if origin and dest:
                part = f"from {origin} to {dest}"
                if date:
                    part += f" on {date}"
                parts.append(part)
            elif dest:
                parts.append(f"to {dest}")
            if not parts:
                parts.append("flight search")

        return " ".join(parts) or "I'm ready to help."


# ---------------------------------------------------------------------------
# BaselineAgent — reference implementation (unchanged, for pub_01/pub_02)
# ---------------------------------------------------------------------------

class BaselineAgent:
    """Reference agent that handles pub_01/pub_02 only.

    Everything else is your job — use Member 1 modules (src/agent/) for
    interruptible, version-aware behavior.
    """

    def __init__(self, in_queue: asyncio.Queue, out_queue: asyncio.Queue):
        self.in_q = in_queue
        self.out_q = out_queue
        self.buffer: List[str] = []
        self.state: Dict[str, Any] = {"intent": None, "slots": {}}
        self.call_seq = 0
        self.pending: Dict[str, Dict] = {}   # call_id -> {"api", "args"}
        self.tools: Dict[str, Any] = {}      # from the tool_manifest event

    async def emit(self, action: str, payload: Dict[str, Any]):
        msg: Dict[str, Any] = {"action": action, "payload": payload}
        if action == "final_response":
            msg["state_snapshot"] = {"intent": self.state["intent"],
                                     "slots": dict(self.state["slots"])}
        await self.out_q.put(msg)

    async def call_tool(self, api_name: str, args: Dict[str, Any]) -> str:
        self.call_seq += 1
        call_id = f"c{self.call_seq}"
        self.pending[call_id] = {"api": api_name, "args": args}
        await self.emit("tool_call",
                        {"call_id": call_id, "api_name": api_name, "args": args})
        return call_id

    async def cancel_all_pending(self):
        for call_id in list(self.pending):
            await self.emit("cancel_tool", {"call_id": call_id})
            del self.pending[call_id]

    @staticmethod
    def find_city(text: str) -> Optional[str]:
        matches = _CITY_PATTERN.findall(text)
        return CITY_CANON[matches[-1].lower()] if matches else None

    async def run(self):
        while True:
            event = await self.in_q.get()
            etype = event.get("event_type")
            payload = event.get("payload", {})
            if etype == "tool_manifest":
                self.tools = payload.get("tools", {})
            elif etype == "user_speech_chunk":
                await self.on_user_text(payload.get("text", ""),
                                        payload.get("end_of_turn", False))
            elif etype == "interruption":
                await self.on_interruption(payload.get("text", ""))
            elif etype == "tool_result":
                await self.on_tool_result(payload)
            # user_audio_chunk, video_frame, scenario_end: not handled here on purpose

    async def on_user_text(self, text: str, end_of_turn: bool):
        self.buffer.append(text)
        if not end_of_turn:
            return
        turn = " ".join(self.buffer).strip()
        self.buffer = []
        low = turn.lower()

        if any(w in low for w in ("flight", "fly", "flights")):
            city = self.find_city(low)
            if city is None:
                await self.emit("clarification_request",
                                {"text": "Sure — which city would you like to fly to?"})
                return
            self.state["intent"] = "book_flight"
            self.state["slots"]["destination"] = city
            await self.emit("filler_speech",
                            {"text": f"Looking up flights to {city} — one moment."})
            await self.call_tool("flight_search", {"destination": city})
            return

        self.state["intent"] = "chitchat"
        await self.emit("final_response",
                        {"text": "Hi! I can help you search for flights — "
                                 "just tell me where you want to fly."})

    async def on_interruption(self, text: str):
        new_city = self.find_city(text)
        await self.emit("filler_speech",                       # 1. acknowledge fast
                        {"text": f"Got it — switching to {new_city}." if new_city
                                 else "Okay, one moment."})
        await self.cancel_all_pending()                        # 2. abort stale work
        if new_city:                                           # 3. update state, re-delegate
            self.state["intent"] = "book_flight"
            self.state["slots"]["destination"] = new_city
            await self.call_tool("flight_search", {"destination": new_city})

    async def on_tool_result(self, payload: Dict[str, Any]):
        call_id = payload.get("call_id", "")
        if self.pending.pop(call_id, None) is None:
            return  # cancelled call — never ground on it
        result = payload.get("result", {})

        if payload.get("status") == "error":
            await self.emit("final_response",
                            {"text": "Sorry — I couldn't complete that right now."})
            return

        flights = result.get("flights", [])
        if not flights:
            await self.emit("final_response",
                            {"text": "I couldn't find any flights for that search."})
            return
        best = flights[0]
        self.state["slots"]["flight_id"] = best["flight_id"]
        await self.emit("final_response",
                        {"text": f"I found a flight to "
                                 f"{self.state['slots']['destination']}: "
                                 f"{best['flight_id']} departing {best['depart']} "
                                 f"for ${best['price_usd']}."})