# Member 3 — State / Tools / Multimodal

Owner: Member 3 (`feature/state-tools`). Covers `src/state/`,
`src/tools/`, `src/multimodal/` plus additive `src/protocol/` schemas.

Core invariants: **Latest Intent Wins · Perception Proposes →
Verification Commits · State is session-scoped · Snapshots immutable ·
Tool calls version-bound · Stale results never win · State-changing
actions idempotent · Committed actions never fakely "cancelled".**

## State (`src/state/`)

- `StateManager`: `create_session / get_state / update_slot /
  update_slots / update_intent / get_snapshot / get_history /
  current_version / validate_state / register_call / invalidate_call /
  remove_call / is_stale` (+ `get_call` read helper for Member 2).
  StateManager does NOT own tool execution lifecycle — it stores
  version bindings only (`CallBinding` has no status field).
- Fixed slot schemas per intent (`schemas.SLOT_SCHEMAS`):
  `flight_search{origin,destination,date}`,
  `support_ticket{device_model,error_code,description}`,
  `booking{option_id,passenger,idempotency_key}`.
  Unknown slots → `InvalidSlotError`; state untouched.
- Corrections are localized: only listed slots change; no-op writes do
  not bump `state_version`. Switching intent resets the slot frame.
- Exactly +1 per real change (atomic batches included), +0 on no-ops or
  failed validation (no partial mutation). Returned snapshots, history
  entries, and bindings are defensive copies; repeat `create_session`
  resets a session to v0 by design.
- `is_stale(session_id, version, call_id)`: stale if `version < current`
  OR `call_id` has no live binding (invalidated/unknown).
- `invalidate_call()` vs `remove_call()`: see Call Ownership below.

## Call Ownership (R1)

StateManager owns binding + version + invalidation + stale determination.
ToolRegistry owns execution + lifecycle + cancellation. Neither imports
or mutates the other.

- StateManager does NOT own tool execution lifecycle. For
  lifecycle/status (`pending/running/completed/failed/cancelled/
  committed`) use ToolRegistry (`get_call`/`call_status`/envelopes).
- For state_version/binding/invalidation/staleness use StateManager
  (`register_call`/`invalidate_call`/`is_stale`).
- For coordinating both use Member 2's Coordinator (register → execute;
  cancel → invalidate; gate every result on exact version equality plus
  `not is_stale`, with replays routed to reconciliation — see M2
  Integration Contract below).

- `register_call()` binds `call_id -> state_version` (no lifecycle).
- `invalidate_call(session_id, call_id)` means "this binding may no
  longer produce an accepted result" — for cancellation, supersession,
  obsolete calls, interruption. Idempotent (`True` if a live binding
  was dropped, `False` if none); never call it on success.
- `remove_call()` is the strict legacy equivalent (raises
  `UnknownCallError` if absent). New code prefers `invalidate_call`.
- Successful completion leaves the binding intact so its result is
  accepted; only the version gate or explicit invalidation rejects it.

## Idempotency + Commit Boundary (Step 7)

- Identity split: `call_id` = execution identity (unique per attempt,
  never reused — reuse raises `DuplicateCallIdError`, even for replays);
  `idempotency_key` = effect identity, scoped as `session|tool|key`
  (same key under another tool/session is a different effect).
- Replay rule: repeated key returns the ORIGINAL stored result with
  `idempotent_replay=True`; only successful (`completed`/`committed`)
  attempts are cached — failed/cancelled/timed-out attempts re-execute.
  Same key with different args still replays the original (key = same
  operation). Concurrent same-key executions are serialized per key:
  exactly one executes, the rest replay.
- Replays preserve the ORIGINAL execution's `state_version`, not the
  replay request's — M2 must special-case `idempotent_replay=True`
  (effect already committed; reconcile via `committed`, not the version
  gate) instead of laundering older-intent effects as current.
- Commit rule: cancel BEFORE the boundary prevents the effect
  (`cancelled`, `committed=False`, nothing cached). AFTER the boundary
  the envelope stays truthful (`committed=True`, never "cancelled");
  reconcile via compensating tools (`cancel_booking`). `cancel_call()`'s
  `cancelled` field means "signal delivered", not "effect undone" —
  always re-read the final envelope/call status.

## Version Contract (Step 6)

- Sole version mint: `StateManager._commit` (monotonic `+= 1`).
  Registry, protocol, and providers only echo caller-supplied versions;
  providers carry no version at all — nothing advances a result version
  independently of StateManager.
- Accepted result: `result.version == current session version` AND live
  binding. Older = stale (`is_stale() -> True`). Newer =
  protocol/coordination violation, NOT stale: `is_stale()` returns False
  by design (it answers staleness only); the Coordinator must log-and-drop
  via exact equality. `is_stale()` intentionally does not cover this case.
- Future versions are impossible by construction under a correct
  Coordinator (read-then-plan can only pass `<= current`); they arise
  only from fabricated versions, cross-session misrouting, or session
  recreation (reset to v0). Pinned by `tests/test_version_contract.py`.

Coordinator (Member 2) must perform BOTH steps, in order:

    out = await registry.cancel_call(call_id)
    state.invalidate_call(session_id, call_id)

**Registry cancellation alone does NOT invalidate StateManager.**
A cancelled-but-still-bound call at the current version passes
`is_stale()` — the Coordinator's invalidation step is what makes late
results stale.

## Replay vs State (Step 8 — contract for M2)

- A committed replay (`idempotent_replay=True`) from an older version
  FAILS the normal version gate by design — that is correct for STATE
  (it must never become the current answer or a slot write), but the
  committed external effect still exists and must not be forgotten.
- Split the handling: (i) state proposals — version-gated
  (`version == current` + live binding), the only path that may mutate
  slots or render current answers; (ii) effect reconciliation —
  `idempotent_replay`/`committed` envelopes routed to an effect ledger
  keyed by `(session, tool, idempotency_key)`, surfaced as
  "already-exists" facts, never as slot writes. Never rewrite a replay's
  version; never bypass the gate for committed payloads.
- Read-only tools never replay (no idempotency policy); their envelopes
  always take the normal version gate. Pinned by
  `tests/test_replay_staleness.py`.

## Tools (`src/tools/`)

- `ToolRegistry`: register/lookup/manifest retrieval, arg validation,
  provider selection, async execution, lifecycle, cooperative cancel,
  session-scoped idempotency store.
- Kinds: `read_only` (fully interruptible), `preparatory` (reversible
  before commit), `state_modifying` (commit boundary + idempotency),
  `compensating` (reconciliation, e.g. `cancel_booking`).
- `MockProvider` (default): deterministic, no network/keys, latency +
  failure injection, `commit_count` for tests. Swap via `set_provider()`.
- Cancel after commit returns `cancelled=False, committed=True` — never
  faked. Repeating an idempotency key returns the stored result with
  `idempotent_replay=True` and executes the effect exactly once.

## Multimodal (`src/multimodal/`)

- `transcribe_wav(bytes) -> Transcript(text, confidence, source)`.
- `extract_facts(bytes) -> VisionResult(facts, confidence, source)`.
- `verify_slot([Evidence]) -> verified | clarification | conflict`
  (default threshold 0.75; conflicts never silently resolved).
- `ground_facts(...) -> [GroundedProposal]`; `apply_verified(...)`
  commits only `verified` AND `safe_to_commit` proposals bound to the
  current version (unbound legacy proposals still apply -- M2 must always
  bind). Low-confidence guesses can never overwrite trusted slots.

## Protocol (`src/protocol/`, additive v1)

Timestamped `Event`/`Action` dataclasses + constructors
(`text_chunk`, `end_of_turn`, `audio_event`, `image_event`,
`interrupt_event`, `tool_call_action`, `tool_result_event`,
`tool_manifest_event`, `cancel_action`, `filler_action`,
`clarification_action`, `final_response_action`, `snapshot_action`).
Payloads are deep-copied on construction and at `to_dict()`.
Only additive changes allowed.

## Interfaces for other members

- Member 1: `get_snapshot/get_state/validate_state`, `get_manifest`,
  `ground_facts/filter_committable`, `VerificationOutcome`.
- Member 2: `register_call/invalidate_call/remove_call/is_stale`,
  `ToolRegistry.execute/cancel_call/get_call/call_status`.
- Member 4: `make_mock_registry()`, `MockProvider` injection hooks,
  `get_history`, protocol-valid envelopes.

## M2 Integration Contract

Ownership: State = session/versions/bindings/invalidation/staleness.
Registry = manifests/execution/lifecycle/cancel/idempotency/envelopes.
Providers = behavior. Multimodal = perceive/propose/verify/gated-commit.
M2 coordinates; M2 MUST NOT duplicate any owned behavior.

Canonical workflows (each step is an existing API):

- NORMAL TOOL CALL: `state.current_version(sid)` → `state.register_call`
  → `registry.execute` → accept iff `result.version == current` AND
  `not state.is_stale(...)`; else log-and-drop.
- INTERRUPTION: `await registry.cancel_call(call_id)` →
  `state.invalidate_call(sid, call_id)` (in order, both steps) →
  await/observe the final envelope → accept/drop/reconcile (committed
  effects need compensation, never fake-cancel).
- MULTIMODAL: `v = state.current_version(sid)` →
  `ground_facts(..., state_version=v)` → `apply_verified(...)`; only
  current-version verified proposals commit. Always bind; route the
  correct `session_id`.
- IDEMPOTENT REPLAY: `idempotent_replay=True` → effect ledger keyed by
  `(session, tool, idempotency_key)` → "already-exists" reconciliation.
  Never rewrite the version; never slot-write or render as current.
- DYNAMIC TOOL: `register_tool(manifest)` → `lookup`/validate →
  provider execution; missing implementation yields structured `failed`
  (plumbing signal, not a domain answer).

Rules: result acceptance = exact equality + live binding (future =
violation, log-and-drop). Registry cancel alone invalidates nothing.
`update_slots` is an intent-write primitive — never feed raw results
into it. Never call providers directly (bypasses lifecycle/idempotency/
cancel). Never reuse `call_id`.

Timeout/unknown-outcome warning: a timed-out/failed attempt is never
cached, so retry re-executes; if the effect may have committed
server-side unseen, query provider-side status before retrying —
otherwise the retry can duplicate outside the mock's model.
