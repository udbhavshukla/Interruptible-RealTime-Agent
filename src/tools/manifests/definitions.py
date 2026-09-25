"""Static tool manifest definitions (schema-driven, no hardcoded agent logic).

Each manifest carries everything the runtime needs: kind, version,
args_schema, timeout, cancellability, reversibility, commit boundary,
idempotency policy.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

TOOL_MANIFESTS: Dict[str, Dict[str, Any]] = {
    "flight_search": {
        "name": "flight_search",
        "kind": "read_only",
        "version": "1.0",
        "args_schema": {
            "origin": "string",
            "destination": "string",
            "date": "string",
        },
        "required_args": ["origin", "destination", "date"],
        "timeout_s": 20,
        "cancellable": True,
        "reversibility": "fully_interruptible",
        "commit_boundary": False,
        "idempotency": None,
    },
    "manual_lookup": {
        "name": "manual_lookup",
        "kind": "read_only",
        "version": "1.0",
        "args_schema": {"query": "string"},
        "required_args": ["query"],
        "timeout_s": 10,
        "cancellable": True,
        "reversibility": "fully_interruptible",
        "commit_boundary": False,
        "idempotency": None,
    },
    "barcode_lookup": {
        "name": "barcode_lookup",
        "kind": "read_only",
        "version": "1.0",
        "args_schema": {"barcode": "string"},
        "required_args": ["barcode"],
        "timeout_s": 10,
        "cancellable": True,
        "reversibility": "fully_interruptible",
        "commit_boundary": False,
        "idempotency": None,
    },
    "reserve": {
        "name": "reserve",
        "kind": "preparatory",
        "version": "1.0",
        "args_schema": {"option_id": "string", "session_id": "string"},
        "required_args": ["option_id"],
        "timeout_s": 20,
        "cancellable": True,
        "reversibility": "reversible_before_commit",
        "commit_boundary": False,
        "idempotency": {"key_field": "idempotency_key", "scope": "session"},
    },
    "booking": {
        "name": "booking",
        "kind": "state_modifying",
        "version": "1.0",
        "args_schema": {
            "option_id": "string",
            "passenger": "string",
            "idempotency_key": "string",
        },
        "required_args": ["option_id", "passenger", "idempotency_key"],
        "timeout_s": 30,
        "cancellable": True,  # cooperative: only before commit boundary
        "reversibility": "requires_compensation",
        "commit_boundary": True,
        "idempotency": {"key_field": "idempotency_key", "scope": "session"},
    },
    "cancel_booking": {
        "name": "cancel_booking",
        "kind": "compensating",
        "version": "1.0",
        "args_schema": {"booking_id": "string", "idempotency_key": "string"},
        "required_args": ["booking_id", "idempotency_key"],
        "timeout_s": 30,
        "cancellable": False,
        "reversibility": "compensating",
        "commit_boundary": True,
        "idempotency": {"key_field": "idempotency_key", "scope": "session"},
    },
}


def get_manifest_dict(name: str) -> Dict[str, Any]:
    try:
        return copy.deepcopy(TOOL_MANIFESTS[name])
    except KeyError:
        raise KeyError(f"Unknown tool '{name}'") from None
