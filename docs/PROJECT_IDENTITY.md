# AURA Project Identity

## Product identity

- **Name:** AURA
- **Category:** AI assistant with interruptible real-time agent capabilities
- **Core principle:** Latest Intent Wins
- **Supporting principle:** Perception Proposes, Verification Commits
- **Product philosophy:** Intelligence that adapts to your latest intent

AURA is an independently developed AI product. Its technical direction centers on real-time interaction, interruptible execution, reliable tool use, cancellation semantics, verification, and alignment with the user's newest intent.

## Purpose of this document

This document records the transition of the repository from a competition-oriented project presentation to an independent product codebase. It also separates implemented behavior from simulated behavior and planned work so that product documentation does not overstate readiness.

## Cleanup performed

### Product-facing content

- Rewrote `README.md` as standalone product documentation covering vision, principles, implementation status, architecture, setup, tools, limitations, roadmap, and contribution guidance.
- Removed legacy event and competition framing from the README, including theme alignment, team listing, judging notes, and submission-oriented language.
- Preserved the public website, GitHub repository, and product demo links.
- Kept only technically verifiable claims about the current tree.

### Interface and metadata

- Replaced the competition-specific account subtitle in `frontend/src/components/Shell.tsx` with a neutral `Preview build` label.
- Added a product description meta tag to `frontend/index.html`.
- Added product description and homepage metadata to `frontend/package.json`.
- Retained AURA's existing visual identity: the dark glass interface, lime accent, navigation, routes, and functional pages.

### Removed competition-only assets

The following files were removed from the working tree because their primary purpose was competition submission rather than product use:

- `docs/AURA_AI_Disclosure_Form.pdf` — a competition disclosure form with team and institution details. It had no product or engineering value. (An earlier `docs/AURA_AI_Disclosure_Form_Completed.pdf` had already been deleted on `main`.)
- `docs/AURA_Final_Presentation.pdf` — a competition presentation deck. Its useful technical material (execution flow, state consistency, tool semantics, multimodal verification, limitations, and roadmap) was consolidated into `README.md` before removal.

Git history is intentionally preserved, so earlier versions of these files remain recoverable from history. No history rewrite was performed.

## Feature classification

### Existing and runnable now

- React 18 + TypeScript + Vite frontend with nine routes: Overview, Live Agent, Sessions, Activity, Tools, Multimodal, Analytics, System, and Settings.
- Deterministic in-browser execution simulator for flight search.
- Simulated fast acknowledgement, asynchronous tool execution, interruption, cancellation, state-version advancement, re-planning, and stale-result rejection.
- Session snapshot display and a live event trace.
- Tool manifests with read-only, preparatory, state-modifying, and compensating classifications.
- Dark, light, and system theme handling with persisted preference.
- Responsive shell, command palette, mobile navigation, and toast notifications.
- Python backend runtime: single-writer Coordinator, bounded mailbox, supervised tasks, cooperative cancellation, Acceptance Gate, and a FastAPI WebSocket server at `/ws`.
- Optional model-provider layer (OpenAI-compatible HTTP or in-process mock) that phrases gate-accepted results, with a deterministic offline fallback and a provenance field on final frames.
- Backend test suite (409 tests) and CI for the backend matrix and frontend build.

### Simulated or mock-only

- All tool executions and dry runs.
- Flight inventory and results.
- Session history and seeded session rows.
- Analytics charts, latency figures, recovery rates, and stale-result counts.
- System-health cards, load figures, and uptime values.
- Audio transcription and image grounding outputs.
- Model answers when `AURA_LLM_PROVIDER` is unset; the deterministic offline text is used instead. When the UI cannot reach the backend it switches to the in-browser simulator and shows `Simulator`/`Local demo data` persistently (plus a toast), never `Agent Online`.

### Interface or contract only

- Tool lifecycle, idempotency, commit-boundary, and compensation behavior are represented in manifests and UI copy; the runtime supports cancellation and stale rejection, but no real external booking provider is connected.

### Planned, not implemented

- Real external tool providers and credentials management.
- Persistent session storage, replay, and audit history.
- Authentication, authorization, multi-tenant isolation, and production observability.
- Frontend unit tests (the production TypeScript build is the current frontend check).
- Docker and deployment automation (CI covers tests and the frontend build).

## Remaining legacy references and exceptions

- **Git history:** Commit messages and older file versions retain historical wording. They were deliberately not rewritten.
- **Repository identity:** The GitHub repository slug `Interruptible-RealTime-Agent` and its remote URL are preserved as instructed. Internal AURA identifiers already use the product name.
- **External deployment:** The public Vercel site and the external demo video link were not modified or redeployed by this cleanup. At audit time the public site was still serving the previous bundle (`/assets/index-BzXJbQVN.js`), which contains the old sidebar label. A redeploy of the cleaned build is required; it was not performed because public deployment was not authorized. The external demo video was not inspected and should be reviewed before the next public release.
- **This document:** It intentionally references the prior competition context to document what changed and why. It is an internal project record, not product-facing marketing copy.
- **Out-of-repository files:** Presentation source files and recordings stored outside the repository were not inspected or modified.

No competition-submission assets (disclosure forms, presentation decks) remain in the tracked working tree. The neutral evaluation kit (`run_local.py`, `harness/`, `scenarios/`, `agent/`) and the engineering note `docs/member3.md` are retained as functional scaffolding; they are not product-facing and are recorded in `docs/REPOSITORY_AUDIT.md`.

## Verification status

- The frontend production build passes with `npm run build` (strict TypeScript check plus Vite bundle).
- The backend suite passes: `python -m pytest tests -q` → 409 passed, 8 subtests passed.
- Repository-wide text searches were rerun after cleanup. The only retained references to the prior competition context are the explanatory ones in this document and historical Git metadata.
- No lint or format script is configured for either side.
- A live vendor model call is unverified (no credentials in this environment); the provider path is tested against a local fake endpoint and the in-process mock.

## Maintenance guidance

- Present AURA as an independent AI assistant, not as a competition entry or student project.
- Label simulated output as simulated in both code and interface.
- Do not describe planned or mocked capabilities as implemented.
- Keep the Latest Intent Wins and Perception Proposes, Verification Commits principles visible in product decisions and documentation.
- Keep `python -m pytest tests -q` and `npm run build` green before claiming backend or protocol reliability.
