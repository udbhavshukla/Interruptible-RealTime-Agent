"""Shared protocol schemas (Member 3 contribution, additive-only).

Covers the Theme's required concepts:
INPUTS: text chunks, end-of-turn, WAV audio, PNG frames, interruption
  signals, async tool results, tool manifests.
OUTPUTS: fillers/acks, non-blocking tool calls w/ explicit call_id,
  cancellations, clarification requests, final responses, state snapshots.

Backwards-compatibility rule: only ADD optional fields / new event types.
Member 1/2 own the event queue + Coordinator; this module only defines
the data structures Member 3 components emit/consume.

v1, additive changes only.
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "1.0"

# -- input event types -------------------------------------------------
TEXT_CHUNK = "text_chunk"
END_OF_TURN = "end_of_turn"
AUDIO_WAV = "audio_wav"
IMAGE_PNG = "image_png"
INTERRUPT = "interrupt"
TOOL_RESULT = "tool_result"
TOOL_MANIFEST = "tool_manifest"

# -- output action types ------------------------------------------------
FILLER = "filler"
TOOL_CALL = "tool_call"
CANCEL_CALL = "cancel_call"
CLARIFICATION = "clarification"
FINAL_RESPONSE = "final_response"
STATE_SNAPSHOT = "state_snapshot"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class Event:
    """Timestamped async input event.

    The payload is deep-copied on construction: later mutation of a
    caller-owned dict never corrupts an emitted event (same defensive
    policy as StateManager snapshots).
    """
    type: str
    session_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: new_id("evt"))
    ts: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "session_id": self.session_id,
                "payload": copy.deepcopy(self.payload),
                "event_id": self.event_id, "ts": self.ts}


@dataclass(frozen=True)
class Action:
    """Timestamped output action with structured identifiers.

    Payload is deep-copied on construction (see Event).
    """
    type: str
    session_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    action_id: str = field(default_factory=lambda: new_id("act"))
    ts: float = field(default_factory=time.time)
    # Optional version binding for tool calls / results:
    call_id: Optional[str] = None
    state_version: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", copy.deepcopy(self.payload))

    def to_dict(self) -> Dict[str, Any]:
        d = {"type": self.type, "session_id": self.session_id,
             "payload": copy.deepcopy(self.payload),
             "action_id": self.action_id, "ts": self.ts}
        if self.call_id is not None:
            d["call_id"] = self.call_id
        if self.state_version is not None:
            d["state_version"] = self.state_version
        return d


# -- constructors -------------------------------------------------------

def text_chunk(session_id: str, text: str) -> Event:
    return Event(type=TEXT_CHUNK, session_id=session_id, payload={"text": text})


def end_of_turn(session_id: str) -> Event:
    return Event(type=END_OF_TURN, session_id=session_id, payload={})


def audio_event(session_id: str, wav: bytes) -> Event:
    return Event(type=AUDIO_WAV, session_id=session_id,
                 payload={"wav_len": len(wav)})


def image_event(session_id: str, png: bytes) -> Event:
    return Event(type=IMAGE_PNG, session_id=session_id,
                 payload={"png_len": len(png)})


def interrupt_event(session_id: str, text: str = "") -> Event:
    return Event(type=INTERRUPT, session_id=session_id,
                 payload={"text": text})


def tool_result_event(session_id: str, call_id: str, state_version: int,
                      result: Dict[str, Any]) -> Event:
    return Event(type=TOOL_RESULT, session_id=session_id,
                 payload={"call_id": call_id, "state_version": state_version,
                          "result": copy.deepcopy(result)})


def tool_call_action(session_id: str, call_id: str, tool_name: str,
                     args: Dict[str, Any], state_version: int) -> Action:
    return Action(type=TOOL_CALL, session_id=session_id, call_id=call_id,
                  state_version=state_version,
                  payload={"tool_name": tool_name,
                           "args": copy.deepcopy(args)})


def cancel_action(session_id: str, call_id: str) -> Action:
    return Action(type=CANCEL_CALL, session_id=session_id, call_id=call_id,
                  payload={})


def clarification_action(session_id: str, slot: Optional[str],
                         reason: str,
                         options: Optional[List[str]] = None) -> Action:
    return Action(type=CLARIFICATION, session_id=session_id,
                  payload={"slot": slot, "reason": reason,
                           "options": options or []})


def final_response_action(session_id: str, text: str,
                          state_version: int) -> Action:
    return Action(type=FINAL_RESPONSE, session_id=session_id,
                  state_version=state_version,
                  payload={"text": text})


def snapshot_action(session_id: str, snapshot: Dict[str, Any]) -> Action:
    return Action(type=STATE_SNAPSHOT, session_id=session_id,
                  state_version=snapshot.get("state_version"),
                  payload={"snapshot": copy.deepcopy(snapshot)})


def filler_action(session_id: str, text: str) -> Action:
    """Spoken filler/acknowledgment (fast-path output, no version binding)."""
    return Action(type=FILLER, session_id=session_id,
                  payload={"text": text})


def tool_manifest_event(session_id: str,
                        manifest: Dict[str, Any]) -> Event:
    """Publish a tool manifest over the event stream (e.g. registry sync
    for M1/M2). The manifest dict is copied, never aliased."""
    return Event(type=TOOL_MANIFEST, session_id=session_id,
                 payload={"manifest": copy.deepcopy(manifest)})
