# CLAUDE.md

Guidance for AI coding assistants working in this repository.

## Project

Grant Management Platform: discovery, matching, application management, evidence-based drafting, email alerts and deadline reminders for charities. See README.md for status, `docs/migration_inventory.md` for where every file came from and `V1_IMPLEMENTATION_PLAN.md` for the agreed plan.

## Rules

- Never modify the earlier prototypes (the PI Research Funding Workspace and the Bright Path demo data). They are read-only references; their copies live in `src/pi_workspace/` and `sample_data/`.
- Use fictional sample data only (`sample_data/`, `src/pi_workspace/data/`). Do not copy real staff, beneficiary or patient information into this repository.
- Never commit secrets. Keys belong in a local `.env` (git-ignored); `.env.example` lists names only.
- Do not create a remote repository, push, or deploy (GitHub, Railway) without explicit approval from the product owner.
- Do not install dependencies or merge the apps' requirements files until compatibility has been assessed.
- Tests must stay offline. `tests/offline_guard.py` strips OpenAI settings and refuses non-loopback connections; keep it as the first import of every test file.
- AI output must stay grounded in supplied evidence: flag missing information rather than inventing it.

## Thread and phase management (permanent)

These rules apply to all future Grant Management Platform development. They sit alongside every other rule in this file and never relax the safeguards above or below.

1. Each development phase must have its own dedicated Claude Code thread.
2. Never create, initiate, or switch to a new thread without the product owner's explicit instruction.
3. Never automatically begin the next phase, even when the current phase is complete.
4. Only perform work explicitly authorised for the current phase.
5. At the end of each phase, stop, summarise the work completed, report test results, and create or update a handoff document.
6. Wait for the product owner's explicit approval before beginning any further development.
7. When the product owner manually opens a new thread, read `CLAUDE.md` and the latest handoff document before beginning work.
8. If the current thread becomes too large or approaches its practical context limits, stop and prepare a handoff rather than automatically continuing elsewhere.
9. Do not treat completion of a phase as permission to start another.

The latest handoff document is `V1_PHASE3_HANDOFF.md` in this folder (Phase 3, 29 October 2026); it follows `V1_PHASE2_HANDOFF.md` (Phase 2) and `V1_STATUS_HANDOFF.md` (Phase 1). Later phases update it or add a newer handoff and name it here.

## Legacy real-data folder (not part of this repository)

During development a separate earlier prototype, built for a real charity, was kept in a `Grant Assistant/` folder next to the code as reference. It contained identifiable staff and patient information, so it is excluded from this public copy and these rules applied to it:

- It was git-ignored and Railway-ignored, and nothing in `src/` may import, read or load data from it. `tests/test_safeguards.py` checks the ignore rules and that platform code never references legacy code or data. Keep it passing.
- No real data may be copied into the application, sample data, test fixtures, logs, documentation, commit messages or AI prompts.
- Reusing its logic meant proposing an extraction first, showing that the extracted code carried no data.
- It must never be uploaded, built or bundled. Any future `Dockerfile`, `.dockerignore` or build script must exclude it; package only `src/grant_platform`.

## Layout

- `src/grant_platform/` — the new platform (V1): `services/` holds all rules (no Streamlit, no SQL), `repository.py` holds all SQL, `ui/` holds thin Streamlit screens. Run tests with `PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -p 'test_*.py'`.
- `src/pi_workspace/` — PI Tool modules copied unchanged; a baseline reference with its own tests. They import each other as flat modules, so keep them in one folder until refactored.
- `sample_data/` — fictional organisations for development and demos. Bright Path is read with `ast.literal_eval`, never imported.
- `tests/` — tests for `grant_platform`; offline, no API keys.
- `docs/` — migration inventory, baseline test results and reference material.
