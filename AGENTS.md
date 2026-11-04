# AGENTS.md

Guidance for coding agents working on `hil-eval`.

- Specs live in `docs/` (Japanese): `requirements.md`, `plan.md`, `architecture.md`,
  `open-questions.md`. Read `architecture.md` before touching core APIs.
- Layout: `src/hil_eval/` (core: `types.py`, `decorator.py`, `context.py`, `blobs.py`,
  `store/`), tests in `tests/`.
- Python >= 3.10. Runtime has **zero required dependencies**; Pillow is optional (`[image]`).
- Commands: `uv sync`, `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format .`.
  All must be green before committing.
- Invariants: the decorated function's result/exceptions are never altered; recording is
  fail-open (log, never raise); confidence must be finite in [0, 1] (reject, don't clamp).
- Contract changes (types, `Store` protocol) need an ADR in `docs/adr/`.
- Commits: small, conventional (`feat:`, `fix:`, `test:`, `docs:`, `build:`). Do not push.
- Review UI will be Vite + React + TypeScript under `ui/`, built into static files served by FastAPI.
