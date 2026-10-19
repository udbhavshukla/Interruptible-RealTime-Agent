# Repository Audit and Recovery Record

**Date:** 2026-10-20
**Scope:** history/branches, backend recovery, frontend buildability, test baseline, deployment state.
**Constraint:** no commit, push, force-push, or production deploy was performed during this audit.

## Why this audit

The working tree had a runnable-looking frontend but the README and earlier notes
claimed the Python backend was absent. A deployment was suspected of serving an
outdated build. Before changing anything, this audit established what actually
exists on each branch, what can be recovered, and what is genuinely missing.

## Method

```bash
git fetch --all --prune
git branch -a -vv
git log -1 --format='%h %ad %s' --date=short <branch>
git ls-tree -r --name-only origin/main | wc -l
git worktree add <tmp>/aura-main integration/aura-ai   # from origin/main
python -m pytest tests -q
cd frontend && npm run build
curl -s -o /dev/null -w '%{http_code}' https://interruptible-real-time-agent.vercel.app<path>
```

## Branch inventory

| Branch | HEAD | Date | What it holds |
|---|---|---|---|
| `origin/main` | `d58e439` | 2026-10-04 | **Full backend + competition scaffolding**, but a broken frontend merge (see below) |
| `origin/feature/frontend` | `ddf3b3c` | 2026-10-01 | Cleaned frontend + docs (the checkout that started this task) |
| `origin/feature/fullstack-integration` | `0a541f2` | 2026-09-30 | Merge of `main` into a fullstack branch |
| `origin/feature/member1-aura` | `27fec66` | 2026-09-26 | Member 1 agent implementation |
| `origin/feature/member2-runtime` | `b6db4df` | 2026-09-26 | Runtime; "handle cancellation before task start" |
| `origin/feature/member4-evaluation` | `52673d8` | 2026-09-26 | Evaluation; "handle multiple scenario schemas" |
| `origin/feature/state-tools` | `ab26f25` | 2026-09-25 | State, tools, multimodal, protocol |
| `toronto` (worktree) | `17a88d0` | — | Merge PR #4 from `member2-runtime` |
| `develop` | `fd03ddd` | — | Initial commit only |

`origin/main` contains **139 files**, including:

| Path | Files | Contents |
|---|---|---|
| `aura/runtime/` | 12 | events, mailbox, registry, supervisor, cancellation, acceptance gate, coordinator, WebSocket server |
| `src/` | 28 | state, tools, multimodal, protocol, evaluation |
| `tests/` | 32 | unit + integration tests |
| `frontend/` | 29 | React/TypeScript app |
| `harness/` | 6 | evaluation-kit runner/scorer |
| `agent/` | 2 | `ParticipantAgent` for the kit |
| `scenarios/` | 13 | kit scenarios |
| `audio/`, `frames/` | 5 | kit fixtures |

**Conclusion:** the backend was never lost; it lives on `origin/main` and the
member branches. The README's "sources absent" statement was stale.

## Defects found

### 1. `origin/main` frontend does not build (critical)

A bad merge left duplicate code in two files:

- `frontend/src/services/RealAgentClient.ts` — a duplicated/broken `connect()`.
- `frontend/src/store/AuraContext.tsx` — duplicate `const [client]` declarations
  (lines 43–44) and duplicated `setConnected` / return paths.

`tsc --noEmit` therefore fails on `main`, even though the backend is healthy.
**Fixed** on `integration/aura-ai` by rewriting the client and de-duplicating the
context; `npm run build` now succeeds.

### 2. Flaky/deterministic test on the recovered backend

Baseline on `integration/aura-ai`: **379 passed, 1 failed**.

`tests/runtime/test_coordinator.py::OrderingTests::test_events_handled_in_mailbox_seq_order`
waited for a tool coroutine to register itself *after* it could already have been
cancelled. The wait was timing-dependent and failed deterministically on this
machine. **Fixed** by asserting the registry state directly instead of racing the
wait; no runtime behaviour changed. Result: **380 passed, 8 subtests passed**.

### 3. Incomplete backend dependency manifest

`requirements.txt` listed only `pytest>=7`, omitting FastAPI, Uvicorn (and the
test-only `httpx`). **Fixed**: `requirements.txt` now declares runtime deps and
`requirements-dev.txt` adds test deps.

### 4. No CI and no deployment config

There was no `.github/` and no `vercel.json`. **Added:** a CI workflow
(`.github/workflows/ci.yml`) and `frontend/vercel.json` with an SPA rewrite (see
the caveat below).

## Deployment audit (read-only)

Live site: `https://interruptible-real-time-agent.vercel.app/`

| Route | HTTP status |
|---|---|
| `/` | 200 |
| `/agent?demo=1` | **404** |
| `/tools` | **404** |
| `/multimodal` | **404** |
| `/sessions` | **404** |
| `/activity` | **404** |

The deployed bundle is `/assets/index-BzXJbQVN.js` (229,848 bytes). It contains
the string `Hackathon build` and **not** `Preview build`, confirming the site was
built from pre-cleanup source. Deep links 404 because the deployment has no SPA
rewrite.

The current source builds to `/assets/index-KpUs2rJi.js` (232,560 bytes), which
contains `Preview build`.

**Not done (no authorization):** redeploying. `frontend/vercel.json` adds the
required rewrite, but it only applies if the Vercel project's **Root Directory**
is `frontend/`. If the root is the repository root, move/copy the rewrite to a
root `vercel.json` and set the build command to target `frontend/`. Verify in the
Vercel project settings before deploying.

## Recovery decision

- A separate worktree (`integration/aura-ai`, branched from `origin/main`) was used
  so the existing checkout and its uncommitted cleanup were left untouched.
- No history was rewritten. The broken merge on `main` is repaired forward on the
  integration branch; it has not been pushed or merged.
- Competition scaffolding (`run_local.py`, `harness/`, `submission.yaml`,
  `agent/`, `scenarios/`, `audio/`, `frames/`, `WALKTHROUGH.md`, `docs/member3.md`)
  is retained: it is functional and separate from the product app. Removing it is
  a product/legal decision, not an audit action.

## Verification snapshot

| Check | Result |
|---|---|
| Backend tests | **409 passed, 8 subtests passed** (`python -m pytest tests -q`, Python 3.13.15) |
| Frontend build | **passes** (`tsc --noEmit && vite build`) |
| Live deployment | stale bundle; 5 deep routes 404 |
| Secrets in tree/history | none found (`.env.example` only) |

## Open items

1. Merge/deploy `integration/aura-ai` only with explicit authorization.
2. Confirm the Vercel Root Directory before relying on `frontend/vercel.json`.
3. Add a Python lockfile (hashes) for reproducible backend installs.
4. Run the suite on Python 3.11 and 3.12 (CI matrix) — locally only 3.13 is available.
