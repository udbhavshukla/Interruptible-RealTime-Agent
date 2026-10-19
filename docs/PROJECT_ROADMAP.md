# AURA Roadmap

**Updated:** 2026-10-20

This document records what is done, what is next, and how each milestone is
verified. It separates **verified** work from **unverified** work. Nothing here
authorizes a commit, merge, or deployment; those remain explicit decisions.

## Where the project stands

| Area | State | Evidence |
|---|---|---|
| Interruptible runtime (mailbox, supervisor, cancellation, acceptance gate, coordinator) | **Done** | `aura/runtime/`, runtime tests |
| WebSocket transport (`/ws`) and frame contract | **Done** | `aura/runtime/websocket_server.py`, `tests/runtime/test_websocket_ai.py` |
| Frontend ↔ backend wiring with mock fallback | **Done** | `RealAgentClient.ts`, `AuraContext.tsx`; `npm run build` passes |
| Provider abstraction (OpenAI-compatible + mock + offline) | **Done** | `aura/llm/`, `aura/assistant/`, 25 provider/responder tests |
| Model-backed answer with stale-answer guard | **Done (mock/fake endpoint)** | `tests/runtime/test_websocket_ai.py` |
| Live vendor call | **Unverified** | No credentials in this environment |
| Token streaming to the UI | **Not wired** | Provider `stream()` exists and is tested; transport sends one completed frame |
| CI (backend matrix + frontend build) | **Done** | `.github/workflows/ci.yml` |
| Authentication, persistence, real tools | **Not started** | — |
| Production deployment of current build | **Pending** | Live site serves a stale bundle; deep routes 404 |

Backend suite: **409 passed, 8 subtests**. Frontend: `tsc --noEmit && vite build` clean.

## Milestones

### M0 — Release the verified build (P0)

The live site still serves `index-BzXJbQVN.js` ("Hackathon build") and returns 404 on
`/agent`, `/tools`, `/multimodal`, `/sessions`, `/activity`.

- Confirm the Vercel project's Root Directory; apply the SPA rewrite (`frontend/vercel.json`
  if the root is `frontend/`, otherwise a root `vercel.json`).
- Deploy the `integration/aura-ai` build.
- **Done when:** `/` serves `index-KpUs2rJi.js` (or newer) and every deep route returns the
  app shell with HTTP 200; `/agent?demo=1` runs.

### M1 — Verify a live model provider (P0/P1)

The provider code path is tested against a local fake endpoint, but no real vendor call
has been made.

- Run `AURA_LLM_PROVIDER=openai` against a real endpoint (or a local vLLM/Ollama gateway).
- Record: first-token latency, end-to-end latency, failure/timeout behaviour, and fallback
  rate. Confirm the key never appears in logs or the browser bundle.
- Label model-generated answers in the UI (e.g. a "model" badge) so simulated data and
  generated prose are distinguishable.
- **Done when:** a scripted conversation produces a model answer, an interrupt drops the
  stale generation, and the run is reproducible from a documented command with no secrets
  committed.

### M2 — Stream answers end to end (P1)

- Emit `FINAL_RESPONSE` deltas (`payload.delta`) over `/ws`, then a final frame with the
  complete `auraText` and snapshot.
- Append deltas in `LiveAgent`; stop rendering a stream the moment a newer version wins.
- **Done when:** the UI shows text progressively, an interrupt truncates the stream, and
  tests cover delta ordering, cancellation, and final-frame reconciliation.

### M3 — Security hardening (P0 before exposure)

See [`docs/SECURITY_AUDIT.md`](SECURITY_AUDIT.md) for the full list. Minimum:

- Authenticated `/ws`, server-issued owner-bound session ids.
- `wss://` + `Origin` allow-list; disable FastAPI docs.
- Inbound size caps, idle timeout, per-user/IP rate limits.
- Secret manager for provider keys; audit log of tool calls and accept/reject decisions.
- **Done when:** an unauthenticated client is rejected, oversized frames are refused
  before parsing, and the production gate checklist passes.

### M4 — Persistence and multi-session (P2)

- Pluggable store for session state and event history (start with SQLite, keep the in-memory
  path for tests).
- Reconnect/resume: rehydrate a session's version and accepted results.
- Retention policy and export/delete.
- **Done when:** a session survives a server restart, versions never go backwards, and
  replay reproduces the same accept/reject outcomes.

### M5 — Real read-only tools (P2)

- Replace the deterministic `_server_tool` flight search with a read-only provider behind
  the same contract; keep the simulated tool for demos/tests.
- Per-tool timeouts, retries with jitter, and response validation.
- **Done when:** a real search flows through the gate, stale results are rejected, and
  provider failures surface as `runtime.error` without corrupting state.

### M6 — Commit-boundary operations (P3)

- `reserve`, `booking`, `cancel_booking` with idempotency keys, commit boundaries, and
  compensation.
- Human confirmation for irreversible effects.
- **Done when:** duplicate deliveries execute once and a post-commit cancellation reports
  the true outcome and compensates rather than claiming an undo.

### M7 — Multimodal with verification (P3)

- Transcription/image proposals with confidence; low confidence asks for clarification.
- Explicit consent, retention, and redaction for captured audio/images.
- **Done when:** unverified perception never becomes trusted state and conflicts are
  surfaced, not silently merged.

### M8 — Observability and evaluation (P3)

- Structured logs, metrics (latency, cancellation rate, stale-rejection rate, fallback rate),
  and traces per session.
- Keep the evaluation harness green (`run_local.py --all`) and publish a scored run.
- **Done when:** a dashboard answers "what happened to call X?" from logs alone.

## Suggested sequence

1. **M0** (release the verified build) — highest visible impact, lowest risk.
2. **M1** (live provider) — turns "model-capable" into "model-verified".
3. **M2** (streaming) — the remaining gap in the interruptible UX.
4. **M3** (hardening) — required before any public exposure.
5. **M4 → M5 → M6 → M7 → M8** — depth, in that order.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Deploying the wrong directory / stale config | Confirm Root Directory; verify bundle hash and deep routes after deploy |
| Provider outage or bad key breaking turns | Responder already falls back to deterministic text; add alerting on fallback rate |
| Prompt injection via tool output | Keep tools simulated/read-only until output validation exists; never execute model output |
| Session hijack in demo | Keep the server on `127.0.0.1` until M3 lands |
| Test drift across Python versions | CI matrix runs 3.11 and 3.12; locally 3.13 is the only interpreter |
| Competition scaffolding confusing product scope | Documented separately in `docs/REPOSITORY_AUDIT.md`; decide keep/remove deliberately |

## Explicit non-goals (for now)

- Real bookings, payments, or inventory.
- Multi-tenant SaaS operation.
- On-device audio/image processing.
- Training or fine-tuning models.
