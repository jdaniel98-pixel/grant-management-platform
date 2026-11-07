# Grant Management Platform

The Grant Management Platform is an AI-assisted **local prototype**, an internal operations improvement project for a small charity's grant work. It is not a commercial product or a business. The platform helps staff find relevant grants, judge whether to apply, track applications and deadlines, and draft answers grounded in the charity's own approved evidence. It uses **fictional data only** and is **not deployed**.

> **Transparency.** The code was written by AI coding assistants under my direction; I did not hand-write it. My contribution was identifying the problem, gathering requirements, designing the workflow, directing and reviewing the implementation, and testing and approving the results. See [My role](#4-my-role).

**Contents:** [Problem](#1-the-operational-problem) · [How the project evolved](#2-how-the-project-evolved) · [What it does](#3-what-the-platform-does) · [My role](#4-my-role) · [Architecture](#5-architecture-and-technology) · [Design decisions](#6-key-design-decisions) · [Testing](#7-testing) · [Limitations](#8-current-limitations) · [Setup](#9-local-setup)

---

## 1. The operational problem

Small charities depend on grant funding, but the process around it is slow and error-prone:

- **Finding opportunities** means repeatedly searching funder websites, and funders change their details or close schemes without notice.
- **Judging fit** is hard. Eligibility rules (who may apply, where, with what income or legal form) are often mixed with softer questions about whether the grant suits the charity's work. Staff can waste days on applications they were never eligible for.
- **Tracking** deadlines, requirements and tasks usually lives in spreadsheets and memory.
- **Writing** applications means re-finding the same facts about mission, impact and governance each time, and there is a real risk of overstating or inventing figures.

The platform explores a single workflow that covers all four, with AI used only where it adds value and with Python rules checking the AI's output.

## 2. How the project evolved

**Where it started.** The project began with an earlier tool, the *Grant Assistant*: a question-and-answer assistant (OpenAI, Pinecone retrieval, Streamlit) built to help one charity bring its organisational information together and draft answers to grant application questions. That original tool is not part of this repository (it was built around real organisational data).

**What I learned from its user.** After the first version I gathered feedback from its intended user, the charity's founder, who runs it. This feedback was gathered informally and is not recorded in this repository. She wanted three improvements:

1. **Natural back-and-forth conversation** rather than isolated prompts.
2. **Conversation history and continuity**, so earlier discussions could inform later ones.
3. **An interface that does not require specialist prompting knowledge.**

The lesson for me was that a technically functional AI tool is not necessarily a practical tool for the people who use it. Getting correct answers out of a model was not enough; the way people interact with it, and what it remembers, decides whether it gets used.

**What the project became.** I widened the scope from answering questions to supporting more of the grant-management workflow: organisational profiles, grant discovery, matching, application tracking, drafting and reminders (section 3). Guided screens and structured forms (a profile questionnaire, funding needs, grant and application screens) replace free-form prompting, so users do not write prompts themselves. For drafting, they supply the application question.

**What is and is not delivered.** To avoid overstating this:

| Feedback from the user | Status in this repository |
| --- | --- |
| Natural back-and-forth conversation | **Not implemented.** There is no chat interface. Each matching or drafting request is a single call to the model. |
| Conversation history and continuity | **Not implemented.** The platform stores profiles, grants, applications and saved drafts, but no conversation history, and earlier discussions do not feed into later AI requests. |
| No specialist prompting knowledge needed | **Partly addressed.** Users work through guided screens and structured inputs; prompts are written once, in code. This has not been tested with real users. |

Conversational interaction and memory remain **future requirements**, not features of this prototype.

## 3. What the platform does

Everything below is implemented and covered by automated tests, using fictional data (a made-up youth charity, "Bright Path Youth Foundation").

```
 Onboarding  ->  Discovery  ->  Matching  ->  Decide & track  ->  Drafting  ->  Alerts & reminders
 profile and     GOV.UK Find    eligibility   pursue / reject,    answers cited    consent-based digests,
 funding needs   a Grant,       kept apart    checklist, tasks    to approved      deadline reminders
                 saved as       from                              evidence,        (local outbox by default)
                 unverified     suitability                       gaps flagged
```

1. **Onboarding.** A questionnaire builds the charity's profile and its funding needs (for example "grow a digital skills programme").
2. **Grant discovery.** A polite web fetcher reads **GOV.UK Find a Grant**, a public source published under the Open Government Licence. It checks `robots.txt`, identifies itself, waits at least 5 seconds between requests, caps pages per run and stays on its own host. Everything it finds is saved as **unverified**. Sources start **disabled** and must be switched on deliberately. Grants can also be entered by hand or by web address.
3. **Change detection.** Each fetch is compared with the saved grant. Only *material* changes (title, funder, eligibility, funding, deadline, purpose, requirements, scope, status) reset verification; formatting changes do not. A grant that disappears from a listing, or whose page fails, is flagged for review and never deleted.
4. **Matching.** For the whole organisation or for one funding need, the AI proposes an eligibility checklist and five suitability scores (out of 25). Python then decides what counts (see [design decisions](#6-key-design-decisions)).
5. **Decide and track.** Save, pursue or reject a grant, move it through statuses to an outcome, keep a requirements checklist and tasks.
6. **Drafting.** Draft answers to application questions using only the organisation's approved evidence, with citations. Missing evidence is listed as a gap instead of being filled in. A person edits and reviews; **nothing is submitted anywhere**.
7. **Alerts and reminders.** Per-organisation contacts with recorded consent receive digests of newly relevant grants and deadline reminders. By default messages are written to a **local outbox folder**; real email is off unless explicitly configured.
8. **Scheduled run.** A `run-daily` command chains discovery, housekeeping, matching, digests and reminders. Each step runs on its own, so one failure does not stop the others, and a log is kept.

## 4. My role

The code in this repository was written by AI coding assistants (Claude Code) working under my direction, session by session. I did not write it by hand, and I make no claim to be the author of individual functions. My role was product owner and project lead:

| Area | What I did | Where to see it |
| --- | --- | --- |
| **Identifying operational problems** | Spotted that a working AI answer tool still left charities with the slower parts of grant work: finding grants, judging fit, tracking and drafting. | [How the project evolved](#2-how-the-project-evolved), `V1_IMPLEMENTATION_PLAN.md` |
| **Gathering requirements and stakeholder feedback** | Collected feedback from the first version's intended user and turned it into the three improvement requests above. | [How the project evolved](#2-how-the-project-evolved) (the feedback itself is not recorded in the repository) |
| **Translating user needs into product requirements** | Defined the V1 scope and what was in or out (for example, vector search was dropped as unnecessary for V1). | `V1_IMPLEMENTATION_PLAN.md` |
| **Designing and improving workflows** | Set the workflow: eligibility kept separate from suitability, unverified-by-default discovery, human review before anything is sent or submitted, and a milestone structure with a stop after each phase. | Plan, `docs/migration_inventory.md` |
| **Directing AI-assisted implementation** | Wrote the standing rules the assistants had to follow: fictional data only, no push or deploy without approval, one phase per session, a written handoff at the end of each phase. | `CLAUDE.md`, `V1_*_HANDOFF.md` |
| **Testing, reviewing and approving changes** | Ran the test suites and a live discovery dry run on my own machine, reviewed results and approved or sent back each phase. The handoffs record problems found and fixed, such as four issues from the first live discovery dry run. | `V1_PHASE2_HANDOFF.md`, `V1_PHASE3_HANDOFF.md`, `docs/evaluations/` |
| **Decisions on reliability, security and data protection** | Decided that AI output must be checked by Python rules, that only fictional data is used until data-protection work is done, that real organisational data stays out of the repository, and that email stays off by default. | [Design decisions](#6-key-design-decisions), `tests/test_safeguards.py` |

The `V1_*_HANDOFF.md` documents were written by the assistants at my instruction and record what was built, what was verified and what was left open.

## 5. Architecture and technology

**Stack:** Python 3.13, Streamlit (UI), SQLite (storage), Pydantic (schemas and validation of AI output), the OpenAI API (`gpt-5-mini` by default, structured outputs), `unittest` for testing. Pinned runtime packages: `streamlit==1.64.0`, `openai==3.14.1`, `pydantic==2.13.5`. Scheduling is a command-line job plus an example macOS `launchd` file.

```
src/grant_platform/
  ui/        thin Streamlit screens (onboarding, profile, needs, grants, matches, applications, notifications)
  services/  all business rules; no Streamlit, no SQL
     ai.py            the single gateway to OpenAI; keys read from the environment or a git-ignored .env
     matching.py      eligibility vs suitability, quote verification
     drafting.py      evidence selection, citation and figure checks, gap listing
     discovery.py, fetcher.py, sources/find_a_grant.py, changes.py
     notifications.py, reminders.py, delivery.py   consent, digests, outbox / SMTP
  repository.py   every SQL statement lives here
  db.py           numbered migrations
  jobs.py         command-line jobs (discover, housekeep, match, digest, notify, run-daily, ...)
  seed.py         loads the fictional charity and scenario grants
src/pi_workspace/   an earlier, separate prototype (PI Research Funding Workspace), kept as a tested reference
sample_data/        fictional charity and fictional scenario grants
tests/              offline tests, canned AI responses, saved sample pages
```

Keeping rules in `services/` and SQL in `repository.py` means the logic can be tested without a browser or a database server.

## 6. Key design decisions

**AI reliability: the model proposes, Python decides.**
- *Eligibility and suitability are separate.* A grant can fit a charity's mission yet be ineligible, or the reverse. The two are reported separately.
- *Quotes must be real.* For each eligibility requirement the model must quote the grant text and the charity fact it relied on. A requirement is only treated as met or not met if the quotes genuinely appear in the supplied text. Otherwise the result is **"unknown"**.
- *Uncertainty never becomes rejection.* Missing information produces "unclear"; only a verified "not met" makes a grant ineligible.
- *Untrusted text is treated as data.* The prompts tell the model not to follow instructions found inside grant or organisation text. This is a mitigation, not a guarantee.
- *Versioned and traceable.* Prompts carry versions (`match-v1`, `draft-v1`); results record the model and a fingerprint of the inputs.
- *One narrow gateway.* All OpenAI calls pass through one module that validates output against a schema and reduces errors to a short safe message.

**Evidence validation in drafting.**
- Only records that are *approved* and cleared for *external use*, for the current organisation, can reach the model.
- Every citation must refer to a record that was actually supplied; every figure must appear in the supplied text; known gaps in the evidence are always listed as missing.
- Drafts are labelled as unreviewed AI drafts. A person must review them; nothing is submitted.

**Data protection and consent.**
- Development uses fictional data only. Real organisational data from an earlier prototype was deliberately kept out of this repository, and a test checks that platform code never references it.
- The planning documents record UK GDPR considerations (including special-category data) that would have to be addressed before any real charity used the platform.
- Alert contacts store the consent wording and time. A contact without recorded consent is never emailed. Every email includes an unsubscribe code.

**Automation safeguards.**
- Discovered grants are saved *unverified*; a human verifies them.
- Material changes reset verification; disappearing pages are flagged, not deleted.
- The fetcher respects `robots.txt`, rate limits itself and stays on its own host.
- Email is **off by default**. When on, it sends only to an explicit allow-list, a message is logged only after successful delivery (so failures retry without repeating), and each contact receives a given alert once.
- Scheduled steps are isolated: one failing step is reported and the rest still run.
- Tests cannot reach the internet or use a real API key (see below).

## 7. Testing

Both suites run **offline**. `tests/offline_guard.py` is imported first by every test file; it removes `OPENAI_*` settings from the environment and refuses any network connection except to the local machine, so a test that forgets to mock the AI gateway, the fetcher or SMTP fails instead of calling a real service. AI behaviour is tested with canned responses and fictional fixtures; the web fetcher is tested against sample GOV.UK pages saved once by hand.

Results on 7 November 2026 (Python 3.13, macOS, fresh virtual environment outside the project folder):

| Suite | Result |
| --- | --- |
| Platform suite (`tests/`) | **421 of 421 passed** (about 8 seconds), both with a clean environment and with a dummy `OPENAI_API_KEY` exported |
| PI workspace suite (`src/pi_workspace/tests/`) | **30 of 30 passed** in a clean environment |

**Unresolved issue: intermittent PI test error.** With a dummy API key exported, one PI workspace run produced a single test error. I could not capture its output, and the next 13 consecutive runs passed. The same kind of intermittent PI error is recorded in [`V1_STATUS_HANDOFF.md`](V1_STATUS_HANDOFF.md) from October 2026. The cause is **unknown**. The PI workspace is the earlier prototype, not the platform code, but I am not treating the issue as fixed.

**One live AI check.** A single live evaluation with `gpt-5-mini` on a fictional scenario is recorded in [`docs/evaluations/`](docs/evaluations/). It shows the output format and the checks working; it is *not* evidence of match quality at scale. A live discovery dry run against the real GOV.UK site (98 pages, 85 grants found, nothing saved) is recorded in the Phase 3 handoff.

**A defect found along the way.** One email-delivery test could make a live OpenAI call whenever a real key was set in the shell. I found it while preparing this portfolio and fixed it by clearing the key in the test and adding the offline guard above.

## 8. Current limitations

- **Local prototype.** Not deployed anywhere and **not production-ready**. There are no real users.
- **Fictional data only.** Bright Path Youth Foundation and the Northbridge University lab are invented. Match quality on real charities is untested.
- **No conversational interface or memory.** No chat, conversation history or cross-session context; these are requirements from user feedback that are not yet built.
- **No authentication or roles.** Anyone who can open the app sees the data.
- **No document upload or indexing.** Evidence comes from structured records only.
- **Email is a prototype.** The default is a local outbox; SMTP delivery exists behind explicit settings and allow-listing but has been tested only against test doubles, not a real provider.
- **One discovery source** (GOV.UK Find a Grant), disabled by default.
- **One recorded live AI evaluation.** No systematic accuracy measurement.
- **Not scheduled automatically.** An example `launchd` file is included but nothing installs it.
- **UK GDPR work not done.** A data protection impact assessment, privacy notice and security hardening are noted as prerequisites for any real use.
- **Unresolved intermittent PI test error** (see above).

## 9. Local setup

Requires Python 3.13.

```sh
python3.13 -m venv .venv
.venv/bin/pip install -r src/pi_workspace/requirements.txt -r requirements-dev.txt
```

**Run the tests** (offline, no API key needed):

```sh
# Grant platform (421 tests)
PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -p 'test_*.py'

# PI workspace reference prototype (30 tests)
PYTHONPATH=src/pi_workspace .venv/bin/python -m unittest discover -s src/pi_workspace/tests -p 'test_*.py'
```

**Try the app with fictional data:**

```sh
# Create the fictional database (instance/grant_platform.sqlite3, git-ignored)
PYTHONPATH=src .venv/bin/python -m grant_platform.seed --scenario-grants

# Start the app
PYTHONPATH=src .venv/bin/streamlit run src/grant_platform/ui/app.py

# Write deadline reminders to instance/outbox/ (never emailed); --dry-run previews
PYTHONPATH=src .venv/bin/python -m grant_platform.jobs notify --dry-run
```

**AI features (matching and drafting)** need your own OpenAI API key: copy `.env.example` to `.env` (git-ignored) and set `OPENAI_API_KEY`, or export it in your shell. The key is never printed, logged or stored in the database. Without a key everything else works and the AI screens say AI is not set up. Matching and drafting send grant text, funding needs and approved evidence to OpenAI, so use fictional data only.

`requirements-dev.txt` adds `httpx` for the PI test files, because the pinned `openai` package uses `httpx2`.

## More documents

- [`V1_IMPLEMENTATION_PLAN.md`](V1_IMPLEMENTATION_PLAN.md): requirements, milestones and risks
- [`V1_STATUS_HANDOFF.md`](V1_STATUS_HANDOFF.md), [`V1_PHASE2_HANDOFF.md`](V1_PHASE2_HANDOFF.md), [`V1_PHASE3_HANDOFF.md`](V1_PHASE3_HANDOFF.md): per-phase records of what was built and verified
- [`CLAUDE.md`](CLAUDE.md): the standing rules given to the AI coding assistants
- [`docs/migration_inventory.md`](docs/migration_inventory.md): where the reused components came from
- [`scheduling/SCHEDULING.md`](scheduling/SCHEDULING.md): the example scheduled run
- GOV.UK sample pages in `tests/fixtures/sources/find_a_grant/` are Crown copyright, used under the Open Government Licence v3.0.
