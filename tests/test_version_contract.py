"""Version contract (Step 6 audit lock-down).

Acceptance rule for a tool result:
    acceptable  <=>  result.version == current session version
                     AND the call binding is live (not invalidated/unknown)
    older       =>  stale            (StateManager.is_stale -> True)
    newer       =>  protocol/coordination violation, NOT "stale":
                    is_stale() answers staleness only and returns False;
                    the M2 Coordinator must log-and-drop via exact equality.

A future version is impossible by construction under a correct Coordinator:
StateManager._commit is the sole version mint; the registry and protocol
only echo the caller-supplied version, and providers carry no version at
all. It can only arise from fabricated versions, cross-session misrouting,
or session recreation (which resets to v0).
"""

from src.state import StateManager


def _acceptable(sm, sid, version, call_id=None):
    """Coordinator acceptance rule: exact equality + not stale."""
    return version == sm.current_version(sid) and not sm.is_stale(
        sid, version, call_id=call_id)


def _flight_session(sm, sid="s_42"):
    sm.create_session(sid)
    sm.update_intent(sid, "flight_search")
    sm.update_slots(sid, {"origin": "Bangalore", "destination": "Delhi",
                          "date": "2026-09-25"})
    return sm.current_version(sid)


def test_future_version_is_not_stale_but_not_acceptable():
    sm = StateManager()
    v = _flight_session(sm)
    sm.register_call("s_42", "call_001", state_version=v,
                     tool_name="flight_search")
    # Predicate answers staleness only: a future version is not "old".
    assert sm.is_stale("s_42", v + 5) is False
    assert sm.is_stale("s_42", v + 5, call_id="call_001") is False
    # ...but the acceptance rule rejects it: exact equality required.
    assert _acceptable(sm, "s_42", v + 5, call_id="call_001") is False
    assert _acceptable(sm, "s_42", v, call_id="call_001") is True
    assert _acceptable(sm, "s_42", v - 1, call_id="call_001") is False


def test_cross_session_result_rejected():
    sm = StateManager()
    va = _flight_session(sm, "s_A")
    vb = _flight_session(sm, "s_B")
    sm.register_call("s_B", "call_B", state_version=vb,
                     tool_name="flight_search")
    # B's call/version presented to A: unknown binding -> stale, even if
    # the numeric version coincides with A's current version.
    assert sm.is_stale("s_A", vb, call_id="call_B") is True
    assert _acceptable(sm, "s_A", vb, call_id="call_B") is False
    # Sanity: each session still accepts its own current result.
    assert _acceptable(sm, "s_A", va) is True


def test_session_recreation_makes_pre_reset_results_unacceptable():
    sm = StateManager()
    v_old = _flight_session(sm)
    sm.register_call("s_42", "call_old", state_version=v_old,
                     tool_name="flight_search")
    # Session restarts fresh at v0; the pre-reset v2 result is now "future".
    sm.create_session("s_42")
    assert sm.current_version("s_42") == 0
    # Version-only check passes (2 > 0 is not "old") -- the violation case.
    assert sm.is_stale("s_42", v_old) is False
    # The wiped binding is still caught by the call leg...
    assert sm.is_stale("s_42", v_old, call_id="call_old") is True
    # ...and the acceptance rule rejects it on both grounds.
    assert _acceptable(sm, "s_42", v_old) is False
    assert _acceptable(sm, "s_42", v_old, call_id="call_old") is False
