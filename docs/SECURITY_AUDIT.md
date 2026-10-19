# Security Audit

**Date:** 2026-10-20
**Scope:** backend runtime/WebSocket server, provider layer, frontend rendering, secrets and history.
**Status of the system:** local/demo software, not production. Findings are rated for the
current demo context and for the point at which the server is exposed to real users.

## Method

- Route/middleware inventory (`@app.websocket`, `add_middleware`, `CORSMiddleware`).
- Secret scan of the working tree and full git history (`api_key`, `secret`, `password`,
  `token`, `private_key`, `sk-`, `AIza`, `ghp_`, `AKIA`, `BEGIN ... PRIVATE KEY`).
- Dangerous-API scan (`subprocess`, `os.system`, `eval`, `exec`, `pickle`, `yaml.load`).
- Frontend sink scan (`dangerouslySetInnerHTML`, `innerHTML`, `eval`, `new Function`).
- Manual review of frame parsing, session lifecycle, cancellation, provider errors, and logging.

Commands used (abridged):

```bash
grep -rniE '(api[_-]?key|secret|password|token|private[_-]?key|sk-|AIza|ghp_|AKIA)' ...
git log --all -p --diff-filter=A -- '*.env' '*.pem' '*.key'
grep -rnE 'subprocess|os\.system|eval\(|exec\(|pickle|yaml\.load\(' aura src agent harness
grep -rnE 'dangerouslySetInnerHTML|innerHTML|new Function' frontend/src
```

## Findings

| ID | Severity | Finding | Status |
|---|---|---|---|
| S1 | **High** | `/ws` has no authentication or authorization | Open (accepted for local demo) |
| S2 | **High** | Client chooses its own `session_id`; no ownership binding | Open |
| S3 | Medium | Default transport is plaintext `ws://`; no TLS | Open (local bind is `127.0.0.1`) |
| S4 | Medium | No inbound message-size limit | Open |
| S5 | Medium | No rate limiting or connection cap; `_sessions` grows unbounded | Open |
| S6 | Medium | No `Origin` validation (cross-site WebSocket hijacking once auth exists) | Open |
| S7 | Medium | Prompt injection / model-output handling when a provider is enabled | Mitigated, monitored |
| S8 | Low | `JsonView` injects HTML via `dangerouslySetInnerHTML` | Accepted, hardening recommended |
| S9 | Low | No Python lockfile (reproducible installs / supply chain) | Open |
| S10 | Low | FastAPI docs routes enabled by default | Open |
| S11 | — | Secrets in repository or history | **None found (pass)** |
| S12 | — | Command execution / unsafe deserialization in app code | **None found (pass)** |
| S13 | — | Session state persisted to disk | **None (in-memory only, pass)** |

### S1 — No authentication on `/ws` (High)

`aura/runtime/websocket_server.py` accepts every connection and immediately starts a
session runtime. There are no tokens, cookies, or tenant checks. Anyone who can reach
the port can consume CPU/memory and drive tools.

**Why it is acceptable today:** the server binds `127.0.0.1` by default
(`uvicorn.run(app, host="127.0.0.1", port=8000)`), so it is not reachable off-host.

**Required before exposure:** authenticated handshake (short-lived token), per-user
session limits, and a reverse proxy that terminates TLS. Do not expose port 8000 directly.

### S2 — Client-chosen `session_id` (High)

The first `text`/`interrupt` frame supplies `session_id`; the server adopts it. A second
live connection with the same id is rejected (`4009 session already connected`), but:

- an attacker can race a victim to claim an id, or pre-claim many ids; and
- ids are not bound to an identity, so there is no ownership check.

**Recommendation:** issue ids server-side after authentication; reject unknown ids; bind
each id to an authenticated principal; expire idle sessions.

### S3 — Plaintext WebSocket (Medium)

`RealAgentClient` defaults to `ws://localhost:8000/ws`. The HTTP site itself is served
over TLS by Vercel, but the app must either be co-hosted or connect over `wss://`.
Use `VITE_WS_URL=wss://...` in any deployed build.

### S4 — Unbounded inbound messages (Medium)

`await websocket.receive_text()` is followed by `json.loads` with no size cap. A single
multi-megabyte frame is parsed and held in memory; the mailbox is bounded by *count*
(`maxsize=1000`) but each message's text is unbounded.

**Recommendation:** reject frames above a configured byte limit before parsing, and cap
`text` length (e.g. 8 KiB for a spoken instruction).

### S5 — No rate limiting / connection cap (Medium)

`_sessions` is a plain dict and each connection spawns a Coordinator, tool task, and
forwarder. There is no per-IP or global cap and no idle timeout. Sustained connections
exhaust memory.

**Recommendation:** connection cap, idle timeout, per-principal session quota, and
proxy-level rate limiting.

### S6 — No Origin validation (Medium)

The WebSocket handshake does not check `Origin`. With cookie-based auth this becomes
cross-site WebSocket hijacking. Until auth exists the impact is limited, but the check
should be added with the auth work.

### S7 — Prompt injection and model output (Medium, provider enabled)

When `AURA_LLM_PROVIDER=openai`, the responder sends the user's intent and the
gate-accepted tool result to the model. Properties that limit impact:

- model output is rendered as **text** (React escapes it), not HTML;
- tool results are locally generated demo data, not fetched attacker-controlled pages;
- no secrets, credentials, or system prompts containing secrets are included;
- provider failures degrade to the deterministic answer instead of surfacing raw errors;
- the reply is only generated for a result the Acceptance Gate already accepted, and a
  version check drops the answer if the user revised their intent meanwhile.

**Risks to monitor:** the model can be instructed to produce misleading prose (e.g.
claim a real booking). The system prompt states the data is simulated, and the UI labels
demo data, but this is social-engineering resistance, not a hard guarantee. Never let
model output become a tool call without the same validation as user input.

### S8 — `JsonView` HTML injection (Low)

`frontend/src/components/ui.tsx` builds highlighted JSON as an HTML string, escaping
`&` first and then `<` before injecting it. Because `<` is neutralised before any markup
is added, tag injection is blocked. Quotes/`>` are not escaped, which can distort the
highlighting but not execute script.

**Recommendation:** build React elements (or use a vetted highlighter) instead of
`dangerouslySetInnerHTML`; it removes the need to reason about escaping.

### S9 — No Python lockfile (Low)

`requirements.txt` uses ranges and there is no hash-pinned lockfile. Builds are not
byte-reproducible and are exposed to dependency substitution.

**Recommendation:** generate `requirements.lock` with `pip-compile --generate-hashes`
or use `uv lock`.

### S10 — FastAPI docs enabled (Low)

`FastAPI()` serves `/docs`, `/redoc`, and `/openapi.json` by default. With only a
WebSocket route they expose little, but they should be disabled in production
(`FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`).

## Passing checks

- **No secrets:** `.env` is git-ignored; only `.env.example` is tracked; the full history
  contains no added credential patterns; no `.pem`, `.key`, or `id_rsa` files are tracked.
  `ProviderConfig.redacted()` never includes the key, and the key is read server-side only.
- **No command execution:** no `subprocess`, `os.system`, `eval`, `exec`, `pickle`, or
  `yaml.load` in `aura/`, `src/`, `agent/`, or `harness/`.
- **No persistence of session data:** sessions are in-memory and removed on disconnect
  (`_close_session` pops both `_sessions` and `_QUERY_CONTEXT`).
- **Input validation:** frame `kind` is whitelisted; `session_id` and `text` are type-checked;
  malformed JSON is dropped; unsupported kinds are ignored rather than fatal.
- **Failure containment:** mailbox overflow drops DATA and logs (CONTROL cannot be dropped
  silently); provider errors never crash the forwarder; send failures trigger cleanup.
- **Model key handling:** no key in the browser bundle; no key in logs; timeout on every call.

## Production gate (must pass before real users)

1. Auth on `/ws` with server-issued, owner-bound session ids.
2. TLS (`wss://`) and `Origin` allow-listing; disable FastAPI docs.
3. Inbound byte/size caps, idle timeout, per-IP and per-user rate limits.
4. Secret manager for `AURA_LLM_API_KEY`; rotate keys; no keys in CI logs.
5. Audit logging (who ran which tool, which version, accept/reject outcome) with PII rules.
6. Dependency lockfiles and automated vulnerability scanning (Dependabot / `pip-audit`).
7. Explicit consent and retention rules for audio/image capture.
