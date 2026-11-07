# V1 Implementation Plan

Grant Management Platform · prepared 24 October 2026 · status: **approved; M0, M1 and M2 built (24 October 2026); M3 not started**

This plan is based on an inspection of the files in this folder: `README.md`, `CLAUDE.md`, `docs/migration_inventory.md`, every module, test and data file in `src/pi_workspace/`, the Bright Path sample data and both handover documents. No code was run, nothing was installed and no file other than this one was created or changed.

---

## 0. Read this first: a data-protection issue found during planning

An earlier prototype built for a real charity sat in a `Grant Assistant/` folder next to the code. It held real staff and patient information in its source files. At planning time `.gitignore` did not exclude it, so a routine `git add .` could have committed that data.

**Resolved 24 October 2026.** The folder was excluded from Git (`.gitignore`) and from Railway uploads (`.railwayignore`), handling rules were added to `CLAUDE.md`, and `tests/test_safeguards.py` checks the exclusions. The folder is not part of this public copy.

## 1. What exists today

### 1.1 PI Tool (`src/pi_workspace/`): working, researcher-specific

| Module | What the code actually does | Coupling / limits found |
| --- | --- | --- |
| `matcher.py` (13 KB) | One OpenAI structured-output call per idea × grant. The model scores six criteria from 1 to 5, and Python's `calculate_result()` computes the total out of 30, the percentage and the band (Poor <50, Potential 50–79, Strong ≥80). A `HardConstraint` category of `definite_ineligibility` or `activity_exclusion_or_purpose_conflict` forces Poor fit and Reject. The prompt insists that missing information is never a hard disqualifier, and that every gap must trace to a supplied input. | The prompt talks about a "lab", a "PI" and the MRC. Eligibility is one of the six scores, so it is mixed in with suitability. Grants are keyed by title, with no IDs. `load_json` is tied to pilot storage paths. |
| `opportunity_workflow.py` (10 KB) | Pursue / Save for review / Reject, statuses `Considering, Pursuing, Drafting, Submitted, Not pursuing`, requirement checklist built from grant fields, saved draft (original AI text kept beside the edited text), reminder *preference* only. Writes are serialised with a lock and saved atomically. | Records are keyed by `(idea title, grant title)`. There is no outcome, no tasks and no reminder delivery. |
| `grant_drafter.py` (7 KB) | Structured draft plus a "missing information" list, with strong anti-invention rules. | The idea and grant titles are hard-coded constants, and the model is fixed to `gpt-5-mini`. It sends the whole profile as context, with no evidence records and no citations. |
| `prototype.py` (11 KB) | CLI flow, `best_alternative()` ranking with parallel evaluation, partial-failure handling and cache, and `failure_diagnostic()`, which redacts secrets and never logs model inputs. | Generic and reusable as it stands. |
| `pilot_storage.py` / `pilot_access.py` / `create_pilot_access.py` | Access code login (SHA-256 hash, constant-time compare, lockout after 5 failures), per-tester folder isolation, `atomic_json()`, cross-process file lock. | Fine for a small pilot. Per-user JSON files won't scale to organisations, grants, matches and notifications that relate to each other. |
| `notebook_store.py`, `research_notebook.py` | Research notes, attachments, voice transcription, AI structuring of notes into ideas. | Not needed for V1. The "AI structures text into fields; user reviews before saving" pattern is reused for document extraction. |
| `app.py` (2,600 lines), `ui_styles.py` | Streamlit researcher UI. The NGO route is a "coming soon" placeholder. | UI and workflow logic are tightly mixed. Reuse the design system and patterns, not the file. |
| `data/` | Fictional Northbridge lab profile, 7 ideas and 4 grants (UKRI/Wellcome schemes, copied by hand, with deadlines that aren't verified). | The grant schema is useful: title, funder, purpose, eligibility, scope, development_stage, funding, duration, deadline (free text), key_requirements, exclusions, source_url. |

**Tests:** 4 files, 30 tests. That matches the "30 passing tests" reported at handover, but they haven't been run in this folder. About half drive the Streamlit researcher UI (`AppTest`). The logic-level tests below are worth carrying forward:

- `best_alternative` partial failure, retry and cache
- diagnostic redaction
- storage isolation and parallel-write safety
- draft save, edit and cancel that keeps the original
- generation failure leaves the record unchanged

### 1.2 legacy Grant Assistant: concepts only, code stays untouched

The processor is a single large file with real data hard-coded in it. Its documented strengths are the **evidence record model** (text plus `knowledge_type`, `source`, `external_use`, `approval_status`, reporting period) and a grant-answer prompt with 19 evidence rules.

Its documented weaknesses must not be carried over:

- importing the module re-indexes everything
- 9 fixed retrieval queries ignore the user's question
- metadata never reaches the model, so approval rules aren't enforced
- record text is printed to logs
- there's no reliable citation

**Recommendation:** reuse the *concepts* and rewrite the code. If the 19 evidence rules are wanted word for word, read only that prompt section, in a separate step the product owner approves. A safer source is the Bright Path `demo_processor.py` in `Operations content/`, which uses the same approach with no real data.

### 1.3 Bright Path Youth Foundation sample data

`brightpath_dummy_knowledge.py` defines `dummy_records`, a list of **42 fictional records**:

- **By type:** programme_capability 6, impact 6, organisation_fact 4, indicator 4, beneficiary 3, policy 3, problem_context 2, geography 2, partnership 2, governance 2, people_organisation 2, sustainability 2, knowledge_limit 2, financial 1, case_study 1.
- **Metadata:** all 42 are `approved` and `safe_to_publish`. Some carry `date_range`, `reported_value`, `measurement_basis`.
- **Deliberate gaps:** two `knowledge_limit` records state that salary scales and validated 2025 impact totals are *not* available. These are ideal for testing missing-evidence handling.
- **Missing structured eligibility facts:** no charity number, legal form, annual income or registered address. The record says only "UK-registered charity", which is good for testing "unknown" versus "ineligible".

Because every record is approved, a few extra fixture records will be added in the new tests (e.g., one `draft`, one `internal_only`, one out of date). They test that approval and publish rules are enforced. The original file stays unchanged and is read with `ast.literal_eval`, not imported or executed.

---

## 2. Requirement-by-requirement mapping

Key: **Reuse** = use as is or near as is. **Adapt** = existing code with real changes. **New** = nothing usable exists.

| # | V1 requirement | Reuse | Adapt | New |
| --- | --- | --- | --- | --- |
| 1 | **Onboarding**: questionnaire | `ui_styles.py` design system | — | Questionnaire covering identity, registration, geography, mission, activities and beneficiaries; organisation model |
| 1 | Optional document uploads | Attachment storage pattern (`app.py` uploads, per-workspace folders) | — | Upload of PDF/DOCX/TXT and text extraction (needs a parser dependency, see §3) |
| 1 | AI extraction, user review and approval | "AI structures into fields → user reviews → save" from `research_notebook.structure_idea` | Prompt and Pydantic schema rewritten for organisation fields | Proposed-versus-approved field status, evidence links back to the source document |
| 1 | Editable profile | — | — | Profile editor; profile values become approved evidence records |
| 2 | Multiple funding needs | Concept of `funding_needs.json` (ideas used as match units) | Schema changed from research idea to programme / project / core costs, with amount, timeframe, beneficiaries, geography | CRUD with IDs |
| 2 | Organisation-wide and need-specific matching | `matcher.match(profile, idea, grant)` signature | Need becomes optional; organisation-wide matching uses the profile plus a "general / core funding" context | — |
| 3 | Monitor curated sources, starting with one real source | `httpx2` (installed by `openai` 3.14.1) | — | Source registry, fetcher (robots.txt, rate limit), page-to-structured extraction, scheduled job |
| 3 | Source URL, verification dates, duplicates, updates | `source_url` field in `grants.json` | Grant schema gains IDs, provenance, parsed deadline date | Content hash, snapshots, dedupe key, change detection, open/closed status |
| 3 | Manual grants and grant URLs | — | — | Manual entry form; "paste a URL" capture, with optional AI pre-fill saved as *unverified* |
| 3 | AI output never treated as verified | Matcher rule: "Do not research or update grant facts" | — | Field-level `verification_status` (unverified / verified, verified_by, verified_at); UI labels; verified-only filter |
| 4 | Adapt PI matching engine | `calculate_result()` pattern, band thresholds, `best_alternative()`, `failure_diagnostic()`, reliability tests | Prompt rewritten for charities. Eligibility pulled out as its own assessment. Five suitability criteria (activity fit, purpose fit, readiness, capability, requirements) scored out of 25 | — |
| 4 | Eligibility separate from suitability | `HardConstraint` categories | Becomes a per-requirement eligibility checklist | Eligibility statuses: **Eligible on available information / Unclear: information missing / Ineligible: confirmed** |
| 4 | Explanations, uncertainties, confirmed versus missing | "Missing information is never a hard disqualifier" and the traceability rules | Output adds `uncertainties[]` and `missing_information[]`, each with the grant statement it depends on | Python check that every "confirmed ineligible" item quotes grant text and an organisation fact |
| 5 | Save / pursue / reject | `DECISIONS`, `record_decision`, `unresolved_points`, `change_status` | Records keyed by IDs and stored in SQLite | — |
| 5 | Status, deadlines, requirements, tasks, outcomes | Status list, requirement checklist from grant fields, `deadline_countdown` / `reminder_choices` in `app.py` | Statuses extended: Submitted → Successful / Unsuccessful / Withdrawn | Tasks with due dates; outcome and amount awarded; a parsed deadline date (the current one is free text) |
| 6 | Drafts from approved organisational information | `InitialDraft` schema, anti-invention prompt, `save_application_draft` / `edit_application_draft` (original AI text kept) | Hard-coded titles removed; works per application question, with word limit | Retrieval from **approved, publishable** evidence records only |
| 6 | Reuse Grant Assistant concepts | Evidence metadata model (concept) | Grant Assistant answer rules (concept) | — |
| 6 | Source references and missing evidence | `missing_information_pi_input_required` | — | Citations by evidence record ID, **checked in Python** (each cited ID must exist, belong to the organisation and be approved); unsupported claims flagged |
| 6 | User stays responsible | "PI makes the final decision" wording | — | Draft marked "AI draft, not reviewed" until the user edits or confirms it; no submission feature |
| 7 | Emails for new matches | — | — | Digest of new matches above a user-set band |
| 7 | Configurable deadline reminders | Reminder preference UI and `reminder_choices()` | Preference stored per application and per user | Scheduler job that computes due reminders |
| 7 | No duplicates; respect preferences | — | — | `notification_log` with a unique key per (user, type, subject, trigger); preferences and unsubscribe; email sending (SMTP) |

**In short:** about a third of the work is adapting proven PI logic (matching, workflow, drafting guard-rails, failure handling). About two thirds is new: the organisation and evidence data model, discovery, notifications and document extraction.

---

## 3. Missing product decisions and technical dependencies

### 3.1 Product decisions for the product owner (needed before the milestone shown)

| Decision | Why it matters | Needed by | Suggested default |
| --- | --- | --- | --- |
| **Who is a user?** Staff of one charity, or consultants managing several organisations? | Account and organisation model | M1 | One user belongs to one organisation; several users per organisation later |
| **First real funding source** | Decides the fetcher and the extraction format | M8 | Pick a UK source that suits Bright Path. Criteria: official publisher, open calls (not past awards), stable pages or a public API, terms that allow automated access. Shortlist 2–3 and compare them in M8 before building. |
| **Who verifies a grant?** A platform admin, or each organisation? | Verified grants could be shared across organisations | M3 | Platform admin verifies; organisations see "unverified" labels |
| **Required onboarding fields** | Eligibility matching needs structured facts (legal form, registration number, country, regions, annual income band, year founded) | M2 | Short required set plus optional fields; "unknown" allowed everywhere |
| **Document types for upload** | PDF/DOCX need a parsing library | M9 | PDF, DOCX and TXT, max 10 MB, no scanned images (no OCR) in V1 |
| **Reminder defaults** | Notification behaviour | M7 | 28, 14, 7 and 2 days before the deadline; user can change them |
| **New-match email frequency** | Avoid spam | M8 | Weekly digest, Potential fit or better, eligible or unclear only |
| **Email provider** | Needs an account and DNS (SPF/DKIM) for deliverability | M7 (real send) | Local outbox file until the pilot; pick an SMTP provider then |
| **Real customer data timeline** | Partner-charity data includes health information, which is special category data under UK GDPR | M10 | Fictional data only until M10 security items and a DPIA are done |
| **AI provider and model** | Cost, data terms | M4 | Keep OpenAI and `gpt-5-mini` (already used and configurable via `OPENAI_MODEL`) |

### 3.2 Technical dependencies

| Need | Proposal | New dependency? |
| --- | --- | --- |
| Storage | **SQLite**, from Python's standard library, one file on disk | No |
| Models / validation | Pydantic (already pinned, 2.13.5) | No |
| AI | `openai` (already pinned, 3.14.1) | No |
| UI | Streamlit (already pinned, 1.64.0) | No |
| HTTP fetching | `httpx2` (installed by `openai` 3.14.1, which replaced `httpx`; declare it explicitly) | No new install |
| HTML to text | Standard library `html.parser` first | No (add BeautifulSoup only if the chosen source needs it) |
| PDF / DOCX text | `pypdf`, `python-docx` | **Yes, in M9 only** |
| Email | Standard library `smtplib` plus provider credentials | No library; needs an account |
| Scheduling | A CLI command run by cron locally, or Railway cron later | No |
| Vector search | **Not in V1.** One organisation has tens to a few hundred records, so filtering by metadata plus simple keyword/topic ranking is enough and easy to test. Revisit embeddings past ~500 records per organisation. | No (Pinecone dropped for V1) |
| Test runner | Standard library `unittest` (what the existing tests use) | No |

---

## 4. Recommended architecture

**One Python application with clear internal layers, one SQLite database and one jobs command.** No separate frontend, no message queue and no vector database.

```
Streamlit UI (thin pages: no business logic)
        │ calls
Services: plain Python functions, no Streamlit imports
  profile · needs · grants · discovery · matching · applications · drafting · notifications
        │ uses                     │ uses
Repository (SQLite)        AI gateway (one module: OpenAI client, versioned prompts,
                            structured outputs, redacted diagnostics, timeouts)
        ▲
Jobs CLI: python -m grant_platform.jobs discover | match | notify  (run by cron)
```

The main lesson from `app.py` is that 2,600 lines of mixed UI and logic are hard to test. New code keeps every rule in the service layer, which is testable without Streamlit. UI tests only check that screens are wired up.

### 4.1 Proposed layout (new code only; `src/pi_workspace/` stays as an unchanged reference)

```
src/grant_platform/
  models.py          Pydantic models (Organisation, EvidenceRecord, FundingNeed, Grant, Match, Application, ...)
  db.py              SQLite connection, schema, numbered migrations
  repository.py      All SQL; every query scoped by organisation_id
  ai.py              OpenAI wrapper (adapted from matcher/grant_drafter call pattern)
  prompts/           matching_v1.md, drafting_v1.md, extraction_v1.md (prompt text versioned in files)
  services/          profile.py, needs.py, grants.py, discovery.py, matching.py,
                     applications.py, drafting.py, notifications.py
  ui/                app.py + pages; reuses ui_styles.py
  jobs.py            discover / match / notify commands
  seed.py            loads Bright Path via ast.literal_eval (never imports the file)
tests/
  fixtures/          scenario grants, extra evidence records, canned AI responses
  test_*.py
```

PI modules are **copied and adapted** into `grant_platform`, not edited in place, so `src/pi_workspace/` stays a working baseline with its own 30 tests.

### 4.2 Core data model (SQLite tables)

| Table | Key fields |
| --- | --- |
| `organisation` | id, name, legal_form, registration_number, country, regions, annual_income_band, founded_year, mission, activities, beneficiaries |
| `profile_field_proposal` | organisation_id, field, proposed_value, source_document_id, quote, status (pending / approved / rejected) |
| `evidence_record` | id, organisation_id, text, knowledge_type, topic, source, external_use, approval_status, reporting_period, origin (seed / profile / upload / manual) |
| `document` | id, organisation_id, filename, stored_path, sha256, uploaded_at, extraction_status |
| `funding_need` | id, organisation_id, title, kind (programme / project / core), description, amount_sought, timeframe, geography, beneficiaries |
| `funding_source` | id, name, base_url, region, enabled, last_checked_at, check_interval |
| `grant` | id, source_id (nullable), origin (monitored / manual / url), canonical_url, title, funder, purpose, eligibility, scope, funding, duration, deadline_text, deadline_date, key_requirements, exclusions, status (open / closed / unknown), verification_status, verified_at, verified_by, first_seen_at, last_seen_at, content_hash, dedupe_key |
| `grant_snapshot` | grant_id, fetched_at, content_hash, extracted_json (change history) |
| `match` | id, organisation_id, funding_need_id (nullable), grant_id, grant_content_hash, eligibility_status, eligibility_items, suitability_scores, suitability_total, band, recommendation, uncertainties, missing_information, model, prompt_version, created_at |
| `application` | id, organisation_id, grant_id, funding_need_id, match_id, status, deadline_date, outcome, amount_awarded, submitted_at |
| `application_task` / `application_requirement` | application_id, text, due_date / source, done |
| `draft_answer` | id, application_id, question, word_limit, ai_text, edited_text, citations, missing_evidence, reviewed_by_user, created_at, modified_at |
| `user`, `notification_preference`, `notification_log` | log has a UNIQUE (user_id, kind, subject_id, trigger_key) |

**Matching cache rule:** a match is reused until the grant's `content_hash`, the organisation profile, the need or the prompt version changes. This avoids paying for repeated AI calls.

**Dedupe rule:** first by normalised canonical URL. Failing that, by a normalised (funder + title) key, then a human merge for near-duplicates.

### 4.3 How matching will work (adapted from `matcher.py`)

1. **Deterministic pre-checks in Python**, wherever the grant has structured fields: country, region, legal form and income band. Unknown values give "unclear", never "ineligible".
2. **One AI call** returns:
   - an eligibility checklist: each requirement gets `met / not_met / unknown`, with a quote from the grant and, for met or not met, the organisation fact relied on
   - five suitability scores (1–5) with reasoning
   - uncertainties and missing information
3. **Python decides.** Any `not_met` with valid quotes gives **Ineligible (confirmed)**, and suitability is still shown for information. Otherwise any `unknown` gives **Eligibility unclear**, with the missing items listed. Otherwise the result is **Eligible on available information**. The suitability band uses the existing thresholds, rescaled to 25. The recommendation is Pursue, Investigate further or Reject, as now. The user always decides.
4. Results are labelled "based on unverified grant information" when the grant isn't verified.

### 4.4 How drafting will work (adapted from `grant_drafter.py` and Grant Assistant concepts)

1. The input is the application question, an optional word limit and optional funder guidance.
2. **Selection in Python:** only `approval_status = approved` and `external_use` allowing external use, for this organisation only. Records are ranked by topic and keyword overlap with the question. With few records, all eligible records are sent. Metadata (source, reporting period) is included with each record, which fixes the Grant Assistant's metadata gap.
3. **AI returns** answer text, the record IDs cited for each claim, and missing evidence.
4. **Python validates** that every cited ID was in the supplied set. Unknown IDs are flagged and the draft is marked "needs review". Any `knowledge_limit` record relevant to the question surfaces as missing evidence.
5. The draft is saved with the original AI text kept separately from the user's edits (existing pattern). There's no submit button, and the user copies the final text out.

---

## 5. Milestones

Each milestone is small enough to review in one sitting, leaves the app runnable and adds tests. No milestone sends real emails, deploys or touches real data unless the product owner approves it.

### M0: Safeguard and baseline (about half a day)
**Deliverables**
- `Grant Assistant/` copy handled as described in §0, and `.gitignore` updated.
- Local virtual environment created and the PI Tool's pinned requirements installed. *This is the first install and needs the product owner's approval.*
- The existing 30 PI tests run, with results recorded in `docs/baseline_test_results.md`.

**Acceptance criteria**
- `git status` shows no real-data files.
- The 30 tests pass, or each failure is written down with its cause.
- No source file in `src/pi_workspace/` changed.

### M1: Platform skeleton and Bright Path data (no AI, no UI)
**Deliverables**
- `src/grant_platform/` package with `models.py`, `db.py` (schema v1 and migrations), `repository.py`.
- `seed.py`, which reads the 42 records via `ast.literal_eval` and creates the organisation, its evidence records and three fictional funding needs:
  - Digital Futures expansion
  - BrightMentor volunteer mentoring
  - core operating costs
- Test fixtures: 3 extra evidence records (draft, internal-only, outdated) kept in `tests/fixtures/`.

**Acceptance criteria**
- Seeding a fresh database creates exactly 1 organisation, 42 evidence records and 3 funding needs.
- Running the seed again changes nothing.
- The source file's checksum is unchanged.
- Repository queries can't return another organisation's rows (tested with a second fictional organisation).
- All tests run offline in under 10 seconds.

### M2: Organisation profile and funding needs UI
**Deliverables**
- Streamlit onboarding questionnaire covering identity, registration, geography, mission, activities and beneficiaries.
- Profile editor and funding needs list, create and edit screens, reusing `ui_styles.py`.

**Acceptance criteria**
- A new fictional organisation can be onboarded entirely by hand.
- Bright Path's profile shows the seeded facts, with missing required facts (e.g., registration number, income band) shown as "Not provided".
- Edits persist across restarts.
- Service tests cover validation.

### M3: Grants: manual entry, URL capture and the scenario set
**Deliverables**
- Grant model with provenance and verification fields.
- Manual "Add grant" form and "Add by URL" (stores the URL and the fields the user enters; no AI yet).
- Dedupe check on save and an admin "mark verified" action.
- 8 fictional scenario grants (see §7).

**Acceptance criteria**
- Every grant shows source URL, origin, verification status and dates.
- Adding the same URL twice is blocked and points to the existing grant.
- Grants show as unverified until an admin verifies them.

### M4: Matching v2 (eligibility separated from suitability)
**Deliverables**
- `services/matching.py` adapted from `matcher.py` (prompt, schemas, `calculate_result`) and `prototype.best_alternative` / `failure_diagnostic`.
- Organisation-wide and need-specific modes, a match cache, and a results screen with explanations, uncertainties and missing information.

**Acceptance criteria**
- With canned AI responses, the 8 scenario grants give the expected eligibility status and band (table in §7).
- A "confirmed ineligible" result without a grant quote is downgraded to "unclear" by Python.
- Ported reliability tests pass: partial failure, retry, cache, redaction.
- A live run against Bright Path (API key required) is reviewed by hand and recorded.

### M5: Application management
**Deliverables**
- Adapted `opportunity_workflow`: save, pursue or reject; statuses through to outcome; tasks with due dates; requirements checklist built from the grant; deadline countdown.
- Applications list and workspace screen.

**Acceptance criteria**
- Every status change is persisted and listed.
- Rejected grants drop out of the suggestions for that need.
- An outcome can be recorded.
- Ported workflow tests pass with IDs instead of titles.

### M6: Evidence-grounded drafting
**Deliverables**
- `services/drafting.py` as described in §4.4.
- Draft editor showing citations (click to view the record) and a missing-evidence panel.

**Acceptance criteria**
- Unapproved and internal-only fixture records are never sent to the model. The test checks the exact payload.
- Every citation shown resolves to an approved Bright Path record.
- A question about 2025 impact totals or staff salaries produces a missing-evidence item, not a figure.
- The original AI text is preserved after edits.

**→ First end-to-end prototype is complete at the end of M6, plus the dry-run reminder from M7 (see §6).**

### M7: Notifications (deadline reminders first)
**Deliverables**
- Notification preferences, `jobs notify` command and `notification_log` with a unique key.
- An email renderer that writes to a local **outbox folder** by default. SMTP sending sits behind a setting that is off by default.

**Acceptance criteria**
- Running `notify` twice on the same day sends each reminder once.
- Turning a reminder type off stops it.
- Reminders stop for applications that are Submitted, Not pursuing or past their deadline.
- Times are calculated in Europe/London.

### M8: Discovery from one real source, plus new-match alerts
**Deliverables**
- Short written comparison of 2–3 candidate sources for the product owner to choose from.
- Fetcher that respects robots.txt and terms, with rate limiting and a clear user agent.
- AI or parser extraction into the grant schema (saved as unverified), snapshots and change detection, open/closed status, and the `jobs discover` command.
- A weekly new-match digest using the M7 machinery.

**Acceptance criteria**
- Tests run against **saved copies** of the source's pages, never live in CI.
- A changed page updates the grant and adds a snapshot.
- An unchanged page does nothing.
- A known duplicate isn't created twice.
- Fetched text is treated as data: a test page containing "ignore previous instructions" changes nothing.
- One live run is reviewed by hand before scheduling.

### M9: Document upload and AI-assisted profile extraction
**Deliverables**
- Upload of PDF, DOCX and TXT (adds `pypdf` and `python-docx`, with approval).
- Extraction into `profile_field_proposal`, each proposal with a quote from the document.
- Review screen to approve, edit or reject each proposal. Approved values update the profile and evidence records.

**Acceptance criteria**
- Nothing extracted reaches the profile or drafting without approval.
- Every proposal shows its source quote.
- Uploads are stored per organisation.
- Fictional Bright Path documents (an annual report and a policy, written as test fixtures) produce the expected proposals.

### M10: Pilot hardening and deployment readiness
**Deliverables**
- Real user accounts. The PI access code login is reusable for a closed pilot, and email-link or password login comes before any open sign-up.
- Organisation isolation tests across every service, database backups, privacy notice and DPIA draft.
- Railway deployment plan reusing the PI Tool's single service, volume and start script approach. *Deploying needs the product owner's approval.*

**Acceptance criteria**
- Security checklist in §8 complete.
- Restore from backup tested.
- No secrets in the repository.
- Logs contain no evidence text or documents.

---

## 6. The smallest end-to-end prototype

**Goal:** prove the core loop with Bright Path before building discovery, uploads or real email.

> Bright Path profile and evidence (seeded) → choose the funding need "Digital Futures expansion" → match against the 8 scenario grants (manually entered) → see eligibility and suitability explained → Pursue one → it appears in Applications with deadline, requirements and tasks → draft an answer to "Describe the impact of your work" with cited Bright Path records and a missing-evidence list → `jobs notify` writes a deadline reminder to the local outbox.

**Scope:** M1 to M6, plus the reminder part of M7 (outbox only).

**Explicitly out of scope for the prototype:** live source monitoring, document upload and extraction, real email, multi-user login, deployment.

**What it needs from outside:** an OpenAI API key for the live matching and drafting checks only. All automated tests run without it.

---

## 7. Testing the prototype with fictional data

### 7.1 Layers
1. **Unit tests (offline, every change):** scoring maths, eligibility decision rules, citation validation, dedupe, reminder scheduling, notification de-duplication, organisation isolation. These use canned AI responses, the way the PI tests already patch `matcher.match`.
2. **Payload tests:** assert exactly what is sent to the model. Only approved, publishable, same-organisation records may appear, and nothing goes to logs.
3. **Scenario tests:** the 8 fictional grants, each designed to trigger one behaviour.
4. **Live evaluation (manual, with API key, before each milestone sign-off):** run the scenarios for real. Record the model, prompt version, results and a short human verdict in `docs/evaluations/`. These runs are reviewed, not asserted, because model output varies.
5. **UI smoke tests:** Streamlit `AppTest` clicks through the prototype journey with AI mocked, as the existing tests do.

### 7.2 Scenario grants (fictional, stored in `tests/fixtures/`)

| # | Grant scenario | Expected eligibility | Expected suitability |
| --- | --- | --- | --- |
| 1 | UK youth employment fund for charities working with 16–25s | Eligible on available info | Strong |
| 2 | Same theme, applicants must be registered in Scotland only (Bright Path is in fictional Northbridge, UK region unstated) | Unclear (region missing) | Strong |
| 3 | US-registered 501(c)(3) organisations only | Ineligible, confirmed (Bright Path is UK-registered) | Shown, not decisive |
| 4 | Charities with annual income under £250k | Unclear (income not in the profile) | Potential or Strong |
| 5 | Climate adaptation research grant | Eligible or unclear | Poor (theme mismatch) |
| 6 | Digital skills for young people, explicitly excludes core costs | Eligible | Strong for "Digital Futures", Poor or Reject for "core costs" (need-specific matching) |
| 7 | Requires 3 years of audited accounts | Unclear (no accounts evidence) | Potential |
| 8 | Duplicate of #1 with a different URL format | Dedupe merges into #1 | — |

### 7.3 Drafting checks
- "Describe your impact": must cite `impact_2024_*` records and get the 312 and 154 figures right.
- "Provide your 2025 outcomes": must flag missing, per `knowledge_limit_2025`.
- "Provide staff salary costs": must flag missing, per `knowledge_limit_salary`.
- "Describe your safeguarding approach": must cite `policy_safeguarding`, and must never cite the internal-only fixture record.

---

## 8. Security, data protection and deployment

Kept proportionate: the prototype uses fictional data on a local machine. The items marked **before real data** are required before any real charity uses it.

### Do from M1
- **Organisation scoping in the repository layer:** every query takes `organisation_id`, with tests that try to cross organisations.
- **Secrets only in `.env`** (already ignored), never in logs or diagnostics. Reuse `failure_diagnostic()` redaction.
- **No evidence or document text in logs**, which avoids the Grant Assistant's log-exposure issue.
- **Treat all external text as data:** grant pages, uploaded documents and user answers are passed as structured input with explicit "data, not instructions" wording (existing PI practice). No AI output triggers an action without a user click.
- **No real data in the repository.** Keep `private_data/` git-ignored, keep the §0 fix, use fictional fixtures only.
- **AI output is labelled:** unverified grant fields, AI drafts and AI-proposed profile values are all marked until a person confirms them.

### Before real data (M10)
- **UK GDPR:** charities' documents may contain personal data about staff, volunteers and beneficiaries. Health information, as for the partner charity in the legacy system, is special category data. Needed:
  - a DPIA
  - a privacy notice
  - a lawful basis
  - retention and deletion (deleting an organisation removes its rows, uploads and backups on a schedule)
  - guidance telling users not to upload beneficiary-identifiable records
- **AI provider terms:** confirm OpenAI API data use and retention settings, and record OpenAI as a sub-processor.
- **Authentication:**
  - per-user accounts with roles (admin, member)
  - lockout (already in `pilot_access.py`)
  - secure session handling
  - HTTPS only
- **Email (PECR):**
  - opt-in for match alerts
  - one-click unsubscribe in every email
  - SPF and DKIM on the sending domain
  - transactional reminders kept separate from alerts
- **Scraping etiquette:** robots.txt, the source's terms, rate limits and an identifying user agent, so a source isn't hammered or misused.
- **Uploads:** size and type limits, files stored outside the web root, never executed, opened with standard parsers.

### Deployment (deferred decision, simple default)
- **Default path:** the existing Railway pattern. One service, one persistent volume holding the SQLite file and uploads, `start.sh` for Streamlit, and a Railway cron service running `jobs discover` / `jobs notify`.
- **Backups:** nightly copy of the SQLite file using SQLite's backup API, plus uploads, to separate storage. Test restore before the pilot.
- **Move to Postgres only** with several app instances or real concurrent write load. The repository layer keeps that change contained.
- Nothing is deployed, and no GitHub remote is created, without the product owner's explicit approval (CLAUDE.md rule).

---

## 9. Recommended first milestone

Start with **M0 + M1 together**, since M0 is tiny:

1. Deal with the `Grant Assistant/` copy (§0) so real patient data can't be committed.
2. Create a local virtual environment, install the PI Tool's three pinned packages and record the result of its 30 existing tests.
3. Build the `grant_platform` skeleton: models, SQLite schema, repository with organisation scoping.
4. Seed Bright Path's 42 records and three funding needs, read safely without executing the file.
5. Add offline tests for seeding, idempotency, organisation isolation and the extra approval-rule fixtures.

This needs no AI calls, no external services and no new dependencies beyond the already-pinned ones. It gives every later milestone a tested data foundation. The only approvals needed are the folder fix and the local package install.
