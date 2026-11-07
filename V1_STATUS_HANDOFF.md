# V1 Status Handoff

Grant Management Platform · Phase 1 (Audit and Resume) · 28 October 2026

This is the handoff a new phase thread reads after `CLAUDE.md`. It records what exists, what has been verified, what is missing and what Phase 2 should be. Phase 1 was read-only: the only files created or changed were `CLAUDE.md` (new "Thread and phase management" section) and this file. No code ran, nothing was installed and nothing inside `Grant Assistant/` was opened.

---

## 1. Current verified project state

**In one line:** the data foundation and the first three screens (onboarding, organisation profile, funding needs) are built. No grants, matching, applications, drafting, notifications, discovery, uploads, login or deployment exist yet.

| Item | State | How it was established |
| --- | --- | --- |
| Milestones built | M0, M1, M2 of `V1_IMPLEMENTATION_PLAN.md` | Code read in Phase 1 |
| Milestones verified on the product owner's Mac | M0 and M1 only (24 Oct 2026) | Recorded in `docs/baseline_test_results.md` |
| M2 on the product owner's Mac | **Not yet run** | The folder has compiled bytecode only for the three M1 test files, and there is no `instance/` database, so neither the M2 tests nor the seed command have run there |
| Files changed since 24 Oct | None apart from Phase 1's two files | File dates |
| Git | One commit (`f8a6a1a`, the foundation). **All of M1 and M2 is uncommitted** | `git status` |
| Local environment | `.venv` exists: Python 3.13.15 (python.org build), streamlit 1.64.0, openai 3.14.1, pydantic 2.13.5, httpx 0.28.1 | `.venv/pyvenv.cfg` and `.venv/bin` |
| Remote repository / deployment | None, by design | `CLAUDE.md` rule |

## 2. Completed milestones and features

### Milestones

| Milestone | Delivered | Acceptance status |
| --- | --- | --- |
| **M0** Safeguards and baseline | `Grant Assistant/` excluded from Git and Railway; rules in `CLAUDE.md`; `tests/test_safeguards.py`; `.venv` and the PI Tool baseline (30 tests); `requirements-dev.txt` fixes the 5 PI tests that could not import `httpx` | Verified on the Mac |
| **M1** Data foundation | `src/grant_platform/`: `models.py` (Pydantic), `db.py` (SQLite, numbered migrations), `repository.py` (all SQL, organisation-scoped), `seed.py` (Bright Path: 1 organisation, 42 evidence records, 3 funding needs, read with `ast.literal_eval`, never executed); 3 extra fixture records (draft, internal-only, outdated) | Verified on the Mac (17 of 17) |
| **M2** Profile and funding needs UI | `services/profile.py`, `services/needs.py` (validation, provenance, missing facts); `ui/app.py` with onboarding questionnaire, profile page and editor, funding needs list/add/edit, organisation switcher | 45 of 45 in a cloud test environment and a real-browser smoke test; **Mac verification pending** |

### Features that work today (in code, and tested at the level shown)

| Feature | What it does | Tested |
| --- | --- | --- |
| Onboarding questionnaire | Identity, registration, geography, mission, activities, beneficiaries. Only name and mission are required; anything else can be "Not provided", which means unknown, not ineligible | Service tests and a Streamlit `AppTest` walkthrough |
| Profile with source attribution | Each field shows "Entered by you" or the Bright Path evidence record it came from; a "Facts we do not have yet" box lists missing eligibility facts (for Bright Path: legal form, registration number, annual income) | Service and screen tests |
| Funding needs | Several per organisation; programme, project or operating costs; optional amount; titles unique per organisation | Service and screen tests |
| Organisation isolation | Every organisation-owned query is scoped by `organisation_id` | Repository tests with a second fictional organisation |
| Evidence approval rule | `usable_externally()` only allows `approved` + `safe_to_publish` records | Repository tests with the draft and internal-only fixtures |
| Persistence | SQLite file at `instance/grant_platform.sqlite3` (override with `GRANT_PLATFORM_DB`), survives restarts | Screen test across a restart |

## 3. Partially implemented and missing features

Against the product owner's confirmed V1 scope:

| V1 requirement | Status | Planned in |
| --- | --- | --- |
| Hybrid onboarding: questionnaire | **Done** (M2) | — |
| Hybrid onboarding: optional document uploads | **Missing.** The onboarding page says uploads "come later" | M9 (needs `pypdf`, `python-docx`) |
| Editable profile with source attribution | **Done** (M2). Partial in one respect: profile answers are not yet turned into evidence records that drafting can cite | Evidence link in M6 |
| Multiple funding needs | **Done**, except deleting a need (not in the plan's acceptance criteria) | — |
| Organisation-wide and need-specific matching | **Missing.** PI Tool `matcher.py` is the reference, not yet adapted | M4 |
| Automated discovery from curated international and regional sources | **Missing.** Note the plan only covers **one** real source (see §5 and §9) | M8 |
| Manual grant entry and URL submission | **Missing.** No `grant` table yet | M3 |
| Eligibility separate from suitability | **Missing** (design is in plan §4.3) | M4 |
| Application tracking | **Missing.** PI Tool `opportunity_workflow.py` is the reference | M5 |
| Evidence-grounded AI drafting | **Missing.** Evidence records and the approval rule exist; drafting service does not | M6 |
| New-grant alerts | **Missing** | M8 (uses M7's machinery) |
| Deadline reminders | **Missing** | M7 |
| User accounts, login, roles | **Missing.** Anyone who can open the app sees every organisation via the sidebar switcher | M10 |
| Deployment | **Missing** | M10 |

Not used and not wanted for V1: Pinecone and vector search (dropped in the plan), the PI Tool's research notebooks.

## 4. Test results and known issues

### Test suites

| Suite | Tests | On the product owner's Mac | In the cloud test environment | Phase 1 |
| --- | --- | --- | --- | --- |
| Grant platform | 45 (M1 17 + M2 28) | 17 of 17 (M1 only, 24 Oct) | 45 of 45 (24 Oct) | Not re-run: no installs and no code execution in this phase |
| PI Tool baseline (`src/pi_workspace/tests/`) | 30 | 30 of 30 (24 Oct) | 30 of 30 on 10 of 11 runs | Not re-run |

Platform test files: `test_repository.py` (5), `test_seed.py` (8), `test_safeguards.py` (4), `test_profile_service.py` (13), `test_needs_service.py` (8), `test_ui_m2.py` (7). All run offline with no API key. A mutation check (breaking `missing_facts`) made 4 tests fail, so the M2 tests detect that regression.

### Verified versus code-only

| Verified end to end | Exists in code, not verified end to end |
| --- | --- |
| M0 safeguards and PI baseline on the Mac | M2 tests on the Mac |
| M1 data layer and seed tests on the Mac | Seeding the real `instance/` database on the Mac |
| M2 in a cloud test environment, including a real Chromium smoke test of the profile and funding needs pages | Running the Streamlit app on the Mac and clicking through onboarding |

### Known issues

1. **M2 not verified on the Mac.** Commands are in §7.
2. **No Git checkpoint for M1 and M2.** Everything since the foundation commit exists only as uncommitted files in `~/Documents`. A mistaken delete or overwrite would be unrecoverable from Git.
3. **Intermittent PI Tool test error.** One error in 1 of 11 runs in the cloud environment on 24 Oct; never reproduced in 9 reruns (including a cold bytecode cache and three suites at once) and the output was not captured. It has not been seen on the Mac. It is in the unchanged PI baseline copy, not in platform code, so it does not block platform work. Action: if it recurs, save the full output to `docs/` before rerunning.
4. **Three stale Git lock files** (`.git/stale-index-lock*-from-claude`, empty). Safe to delete; left by earlier sandboxed Git commands.
6. **Documentation drift:**
   - `docs/migration_inventory.md` gave an outdated location for the legacy Grant Assistant (plan §0 records the move).
   - `.env.example` still lists `PINECONE_API_KEY`, which V1 does not use.
   - `docs/reference/grant_assistant/` is an empty folder.
   - README says M2's "local verification is pending", which is still correct.

### Dependencies

- Runtime pins (PI Tool `requirements.txt`): streamlit 1.64.0, openai 3.14.1, pydantic 2.13.5. Dev only: httpx 0.28.1.
- `grant_platform` currently uses pydantic, streamlit and the standard library (sqlite3). It does not yet use openai.
- Planned additions: `pypdf` and `python-docx` in M9 only (needs approval). HTTP fetching in M8 uses `httpx2`, already installed by openai.

## 5. Remaining development phases, in recommended order

Each phase gets its own thread and stops for approval (see `CLAUDE.md`).

| Phase | Plan milestone | Scope | Needs from the product owner |
| --- | --- | --- | --- |
| **2** | M3 | Grants: manual entry, URL capture (no fetching), verification labels, dedupe, 8 fictional scenario grants. Preceded by M2 verification and a local Git checkpoint | Run M2 commands; approve local commit; who verifies grants |
| 3 | M4 | Matching v2: eligibility separate from suitability, organisation-wide and need-specific | OpenAI API key in a local `.env` for the live check only |
| 4 | M5 | Application tracking: pursue/save/reject, statuses to outcome, tasks, requirements, deadlines | — |
| 5 | M6 | Evidence-grounded drafting with citations checked in Python and a missing-evidence panel | Whether to extract the Grant Assistant's 19 evidence rules (a separate approval) or use the Bright Path demo |
| 6 | M7 | Deadline reminders to a local outbox; real email behind an off-by-default setting. **End of the first end-to-end prototype (plan §6)** | Reminder defaults |
| 7 | M10 (part) | Pilot hardening: user accounts and roles, isolation across all services, backups and restore test, Railway deployment of a **fictional-data** controlled pilot | Hosting choice; approval to create a remote and deploy |
| 8 | M8 | Discovery from curated sources, change detection, weekly new-match digest | Which sources, and how many (see §9) |
| 9 | M9 | Document upload and AI-assisted profile extraction with review | Approval for two new packages |
| 10 | M10 (rest) | Real-data readiness: DPIA, privacy notice, OpenAI data terms, email provider with SPF/DKIM, retention and deletion | Real-data timeline, email provider |

**Shortest sensible route to a deployable V1.** The plan puts all of M10 last. The quickest route to a pilot people can use is to build the core loop first (M3 to M7), then do the deployment half of M10 so testers can use the fictional-data prototype, then add discovery and uploads behind that working pilot. That reorders phases 7 to 9 compared with the plan, so it is listed as a decision in §9. If the product owner prefers the plan's order, phases 7 and 10 merge and move after M9.

## 6. Technical and security risks

| Risk | Severity | Mitigation in place / needed |
| --- | --- | --- |
| Real staff and patient data in a legacy folder inside the project | High | Git-ignored, Railway-ignored, never imported (tested). Needed: any future Dockerfile or build must exclude it |
| No Git checkpoint for M1 and M2 | High (data loss) | Local commit after M2 verification (no remote) |
| No authentication; every organisation visible to any user of the app | Blocker for any deployment | M10: per-user accounts and roles; reuse the PI Tool's access-code login for a closed pilot |
| SQLite on a single volume | Medium for a pilot | Nightly backup with the SQLite backup API and a tested restore (M10) |
| AI output trusted as fact (grant fields, eligibility, drafts) | High once AI is added | Plan requires verification labels, Python-checked quotes and citations, "AI draft, not reviewed" labels |
| Prompt injection from fetched grant pages or uploaded documents | Medium (M8, M9) | Treat as data; test page with injected instructions must change nothing |
| Personal and special category data once real charities join | High | DPIA, privacy notice, lawful basis, retention, OpenAI as sub-processor; fictional data only until done |
| Email compliance (PECR) and deliverability | Medium (M7, M8) | Opt-in alerts, unsubscribe, SPF/DKIM; local outbox until then |
| Scraping terms and rate limits | Medium (M8) | robots.txt, source terms, rate limit, identifying user agent; saved pages in tests |
| Dependency pin mismatch (`openai` uses `httpx2`, PI tests import `httpx`) | Low | `requirements-dev.txt` |
| Intermittent PI test error | Low | Capture output if seen again |

**External services** needed later: OpenAI API key (M4 onward), an email provider and DNS access (real email), a hosting account such as Railway (deployment), and access to whichever funding sources are chosen (M8). None are configured.

## 7. Exact commands to run the application locally (Mac)

Run in Terminal from the project folder. The `.venv` already exists; the install line is only needed for a fresh environment.

```sh
cd <project folder>  # the repository root

# Only if .venv is missing
python3.13 -m venv .venv
.venv/bin/pip install -r src/pi_workspace/requirements.txt -r requirements-dev.txt

# 1. Platform tests (expect 45 of 45, offline)
PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v

# 2. Create the fictional Bright Path database (instance/grant_platform.sqlite3; safe to run twice)
PYTHONPATH=src .venv/bin/python -m grant_platform.seed

# 3. Start the app; it opens at http://localhost:8501
PYTHONPATH=src .venv/bin/streamlit run src/grant_platform/ui/app.py

# Optional: PI Tool baseline tests (expect 30 of 30)
PYTHONPATH=src/pi_workspace .venv/bin/python -m unittest discover -s src/pi_workspace/tests -p 'test_*.py' -v
```

**Entry point:** `src/grant_platform/ui/app.py` (`main()`), started with `streamlit run`. Without the seed step the app opens at the onboarding questionnaire. Set `GRANT_PLATFORM_DB=/path/to/file.sqlite3` to use another database.

## 8. Recommended Phase 2: scope, deliverables and acceptance criteria

**Phase 2: Checkpoint and grants (M3).** No AI, no web fetching, no new dependencies.

**Before any code (the product owner, about 5 minutes):**
1. Run the three commands in §7 and send the output.
2. Approve a local Git commit of M1, M2 and Phase 1 (no remote, no push), with `git status` checked so nothing from `Grant Assistant/` is staged.

**Deliverables**
- `grant` table via a new numbered migration, with provenance and verification fields: origin (manual or URL), canonical URL, title, funder, purpose, eligibility, scope, funding, duration, deadline text and parsed deadline date, key requirements, exclusions, open/closed/unknown status, verification status, verified by, verified at, first and last seen dates, dedupe key.
- `services/grants.py`: validation, URL normalisation, dedupe (normalised URL first, then funder + title), mark verified.
- Screens: grants list, "Add grant" form, "Add by URL" (stores the URL and the fields the user types; nothing is fetched), grant detail, and a "Mark verified" action.
- The 8 fictional scenario grants from plan §7.2 as test fixtures, loadable into the database.
- Tests for all of the above; README and this handoff updated.

**Acceptance criteria**
- Every grant shows source URL, origin, verification status and dates.
- Adding the same URL twice (including a different URL format, scenario #8) is blocked and points to the existing grant.
- New grants show as unverified until verified.
- All tests run offline; the 45 existing tests still pass; `test_safeguards.py` still passes.
- Organisation isolation is unaffected (grants are shared reference data; see the verification decision below).
- Stops at the end of M3 with a summary, test results and an updated handoff.

## 9. Decisions requiring the product owner's approval

| # | Decision | Needed by | Recommendation |
| --- | --- | --- | --- |
| 1 | Approve Phase 2 as described in §8 | Now | Yes |
| 2 | Local Git commit of M1, M2 and Phase 1 (no remote) | Start of Phase 2 | Yes |
| 3 | Who verifies a grant: a platform admin, or each organisation | Phase 2 (M3) | Platform admin; organisations see "unverified" labels. Grants are then shared across organisations |
| 4 | Discovery scope: the plan builds **one** UK source in M8, but the confirmed V1 scope says "curated international and regional funding sources" | Before Phase 8 (M8) | Build the source framework with one source first, then add sources one at a time; agree the list before M8 |
| 5 | Phase order: deploy a fictional-data pilot after M7 (before discovery and uploads), or keep the plan's order with all of M10 last | Before Phase 7 | Deploy earlier with fictional data, so testers see the core loop sooner |
| 6 | Hosting and creating a GitHub remote | Before deployment | Railway, as for the PI Tool; private GitHub repository |
| 7 | Real-data timeline (partner-charity data is special category health data) | Before any real charity uses it | Fictional data only until the DPIA and M10 security items are done |
| 8 | Housekeeping (small, can go in Phase 2): delete the three stale Git lock files; fix the migration inventory's Grant Assistant location; remove `PINECONE_API_KEY` from `.env.example`; remove the empty `docs/reference/grant_assistant/` | Phase 2 | Yes |
| 10 | Update `V1_IMPLEMENTATION_PLAN.md` so §2 and M8 reflect the confirmed V1 scope (international and regional discovery) | Any time | Yes, as part of the phase that settles decision 4 |
