"""Regression tests for destination-change handling in the WS flight demo.

Covers the fix in ``aura.runtime.websocket_server`` for the bug where an
interrupt such as "Instead of Delhi, find me for Mumbai." produced the
route Delhi → Mumbai instead of keeping the original origin
(Bangalore → Mumbai). Nothing about the cities is hardcoded: every
expectation is derived from the previous query handed to the parser, and
a second pair of city names exercises the same path.

The merge itself (origin/destination/date/preference, ``_query_of``) and
the per-session context store (``_QUERY_CONTEXT``, ``_server_tool``) are
covered alongside the route parser. Cancellation and stale-result
rejection are runtime behaviour of the Coordinator/Acceptance Gate and
remain covered by the existing suite (``tests/test_replay_staleness.py``
et al.) — nothing here touches them.
"""

from __future__ import annotations

import asyncio
import types
import unittest

try:
    from aura.runtime import websocket_server as ws
except Exception as exc:  # pragma: no cover - optional server dependencies
    ws = None
    _IMPORT_ERROR = exc
else:
    _IMPORT_ERROR = None


#: A completed previous request, as the tool would have stored it.
PREVIOUS = {
    "origin": "Bangalore",
    "destination": "Delhi",
    "date": "25 Sep 2026",
    "preference": "cheapest",
}


@unittest.skipIf(ws is None, f"websocket_server unavailable: {_IMPORT_ERROR}")
class RouteContinuityTests(unittest.TestCase):
    """``_route_of`` keeps the untouched half of the route."""

    def setUp(self) -> None:
        ws._QUERY_CONTEXT.clear()

    def test_destination_only_revision_keeps_origin(self) -> None:
        # The reported bug: destination changes, origin must not.
        self.assertEqual(
            ws._route_of("Instead of Delhi, find me for Mumbai.", PREVIOUS),
            ("Bangalore", "Mumbai"),
        )

    def test_destination_revision_works_for_other_city_pairs(self) -> None:
        # Same rule with different cities: no Bangalore/Mumbai special case.
        previous = {"origin": "Chennai", "destination": "Pune"}
        self.assertEqual(
            ws._route_of("Instead of Pune, find me for Jaipur.", previous),
            ("Chennai", "Jaipur"),
        )

    def test_origin_only_revision_keeps_destination(self) -> None:
        self.assertEqual(
            ws._route_of("From Mumbai instead.", PREVIOUS),
            ("Mumbai", "Delhi"),
        )

    def test_explicit_markers_override_context(self) -> None:
        self.assertEqual(
            ws._route_of("Actually fly from Mumbai to Pune instead.", PREVIOUS),
            ("Mumbai", "Pune"),
        )

    def test_unstated_route_keeps_previous(self) -> None:
        self.assertEqual(
            ws._route_of("cheapest please", PREVIOUS),
            ("Bangalore", "Delhi"),
        )

    def test_one_sided_route_with_context(self) -> None:
        self.assertEqual(
            ws._route_of("goa to bangalore.", PREVIOUS),
            ("Goa", "Bangalore"),
        )

    def test_first_request_without_context_unchanged(self) -> None:
        # No previous query -> the pre-existing fallbacks still apply.
        self.assertEqual(
            ws._route_of("Find a flight from Bangalore to Delhi."),
            ("Bangalore", "Delhi"),
        )
        self.assertEqual(
            ws._route_of("Actually Mumbai, not Delhi."),
            ("Delhi", "Mumbai"),
        )
        self.assertEqual(ws._route_of(""), ("Bangalore", "Delhi"))


@unittest.skipIf(ws is None, f"websocket_server unavailable: {_IMPORT_ERROR}")
class QueryMergeTests(unittest.TestCase):
    """``_query_of`` replaces only the fields the new intent states."""

    def setUp(self) -> None:
        ws._QUERY_CONTEXT.clear()

    def test_destination_revision_preserves_date_and_preference(self) -> None:
        self.assertEqual(
            ws._query_of("Instead of Delhi, find me for Mumbai.", PREVIOUS),
            {
                "origin": "Bangalore",
                "destination": "Mumbai",
                "date": "25 Sep 2026",
                "preference": "cheapest",
            },
        )

    def test_changed_fields_replace_previous(self) -> None:
        self.assertEqual(
            ws._query_of(
                "Fastest flight from Chennai to Pune on 1 Dec 2026", PREVIOUS
            ),
            {
                "origin": "Chennai",
                "destination": "Pune",
                "date": "1 Dec 2026",
                "preference": "fastest",
            },
        )

    def test_first_request_has_no_inherited_fields(self) -> None:
        self.assertEqual(
            ws._query_of("bangalore to delhi"),
            {
                "origin": "Bangalore",
                "destination": "Delhi",
                "date": None,
                "preference": None,
            },
        )


@unittest.skipIf(ws is None, f"websocket_server unavailable: {_IMPORT_ERROR}")
class ToolContextTests(unittest.TestCase):
    """``_server_tool`` persists the merged query per session id."""

    def setUp(self) -> None:
        ws._QUERY_CONTEXT.clear()

    def test_context_is_stored_per_updated_session(self) -> None:
        async def scenario():
            session_a = types.SimpleNamespace(session_id="sess-regress-a")
            first = await ws._server_tool(
                intent="Cheapest flight from Bangalore to Delhi on 25 Sep 2026",
                call_id="call_a1",
                state=session_a,
            )
            second = await ws._server_tool(
                intent="Instead of Delhi, find me for Mumbai.",
                call_id="call_a2",
                state=session_a,
            )
            other_session = await ws._server_tool(
                intent="Instead of Delhi, find me for Mumbai.",
                call_id="call_b1",
                state=types.SimpleNamespace(session_id="sess-regress-b"),
            )
            bare = await ws._server_tool(
                intent="bangalore to delhi", call_id="call_n1", state=None
            )
            return first, second, other_session, bare

        first, second, other, bare = asyncio.run(scenario())

        # First request: parsed in full, then stored under its session.
        self.assertEqual(
            (first["origin"], first["destination"]), ("Bangalore", "Delhi")
        )
        self.assertEqual(first["date"], "25 Sep 2026")
        self.assertEqual(first["preference"], "cheapest")

        # The reported bug, end to end: destination-only revision keeps
        # origin, travel date and preference of the request it replaces.
        self.assertEqual(
            (second["origin"], second["destination"]), ("Bangalore", "Mumbai")
        )
        self.assertEqual(second["date"], "25 Sep 2026")
        self.assertEqual(second["preference"], "cheapest")
        prices = [ws._price_value(o["price"]) for o in second["options"]]
        self.assertEqual(prices, sorted(prices))  # inherited "cheapest" applied

        # A different session sees no leaked context: legacy fallback only.
        self.assertEqual(
            (other["origin"], other["destination"]), ("Delhi", "Mumbai")
        )
        self.assertIsNone(other["date"])
        self.assertIsNone(other["preference"])

        # The tool contract still tolerates a state without a session id.
        self.assertEqual(
            (bare["origin"], bare["destination"]), ("Bangalore", "Delhi")
        )

        # Both sessions own exactly their own merged query — the second
        # session inherited nothing from the first (its date stayed None).
        self.assertEqual(
            set(ws._QUERY_CONTEXT), {"sess-regress-a", "sess-regress-b"}
        )
        self.assertEqual(
            ws._QUERY_CONTEXT["sess-regress-a"]["destination"], "Mumbai"
        )
        self.assertIsNone(ws._QUERY_CONTEXT["sess-regress-b"]["date"])


if __name__ == "__main__":
    unittest.main()
