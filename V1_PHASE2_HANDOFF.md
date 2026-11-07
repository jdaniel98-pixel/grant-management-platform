# Phase 2 handoff: Complete the core user journey

Grant Management Platform · 28 October 2026 · **Status: complete and accepted for the V1 local prototype. 239/239 tests pass on the product owner's Mac and the live AI check was reviewed and accepted (28 October 2026). Committed locally, not pushed. Phase 3 has not been started.**

Read `CLAUDE.md` first. This document follows `V1_STATUS_HANDOFF.md` (Phase 1).

## 1. What was built (steps A to E)

| Step | Delivered |
| --- | --- |
| A Grants | `grant_record` table, `services/grants.py` (URL normalisation, dedupe, verification), Grants screen (manual entry, add by URL with no fetching, mark verified), 8 fictional scenario grants (`seed --scenario-grants`). |
| B Matching | `services/matching.py`, `services/ai.py` (single OpenAI gateway). Eligibility is separate from suitability. Python decides status, band and recommendation. Quotes must appear verbatim in the supplied text or the item becomes "unclear". Unknown never means ineligible. Results are cached by fingerprint. Matches screen. |
| C Applications | `services/applications.py`, statuses, status history, requirements checklist, tasks with due dates, deadline countdown, Applications screen with workspace. |
| D Drafting | `services/drafting.py`. Only approved, safe-to-publish evidence for that organisation is sent. Citations must resolve. Figures must appear in the evidence. Missing evidence is listed. The original AI text is kept apart from edits. |
| E Reminders | `python -m grant_platform.jobs notify`. Writes files to `instance/outbox/`, never sends email, one reminder per application and threshold (28, 14, 7, 2 days). |

Also added: `live_check.py` for the real-key check (section 4), and a whole-journey automated test (`tests/test_journey.py`).

## 2. Test results

**Accepted by the product owner on 28 October 2026:** all 239 automated tests pass on the Mac. An initial 238/239 run failed on one test only (`test_the_default_outbox_lives_inside_the_git_ignored_instance_folder`), because macOS `/var/...` and `/private/var/...` are the same folder. The fix was in the test (`resolve()` on both sides); reminder behaviour was unchanged. The live check (one real match, one real draft, `gpt-5-mini`) was run and accepted; the report is `docs/evaluations/live_check_2026-10-28_scenario6.md`. The product owner also approved the added `rejected` status.

Earlier results in the cloud copy:

- Cloud copy: **239 passed, 0 failed** (45 earlier platform tests plus 194 new). Run on 28 October 2026 after the last copy to the Mac.
- Real-browser (Playwright) click-through of the whole journey with canned AI: no duplicate cards, workspace stays open after actions.
- **Not run:** the PI Tool baseline (30 tests; `src/pi_workspace` is untouched per `git status`). **Not run:** anything against the real OpenAI API.
- Files were copied to the Mac with checksums verified (all match).

## 3. Minor improvements for future work (from the live check)

These did not block acceptance:

1. **Organisation-fact quotes are over-strict.** "Be a UK registered charity" came back "unknown" because the model's fact quote ("Charity; United Kingdom") was a composed string, not text found verbatim in the profile. Consider matching against the individual profile fields, or letting the model cite the field name.
2. **Blank grant quotes are accepted.** The funding-amount check showed a quote made only of spaces. Treat a whitespace-only quote as no quote, so the item is downgraded.
3. **Application requirements appear as eligibility checks.** "Provide participant numbers / a budget" are application items, not eligibility, and add noise to the "unclear" count.
4. **Drafts are long and unfocused.** The draft was one very long paragraph that cited 19 records, including governance and policies, and a fictional case study. Consider a cap on records used, a default length when no word limit is set, and keeping the case study out of funder-facing text.
5. **"Missing evidence: none"** for a broad question. The checks passed, but a human should still read every draft before use.
6. The verdict section in the report file was left blank; the verdict is recorded here and in the thread.

## 4. Things to know or decide

1. **Added status `rejected`**, approved by the product owner on 28 October 2026 (a grant you decide not to pursue; it drops out of suggestions).
2. **Verification is development-only.** Mark verified is available to whoever runs the app, records "Development-only verification (no login)", and the Grants screen says so. It is not secure administrator access. Real roles belong with authentication.
3. **Strict quote checking is too strict in places** (see improvements 1 and 2 above); tune it in a later phase.
4. **Not built:** edit a grant after creation, delete a funding need, delete tasks.
5. The retained 2019 "outdated" evidence fixture is included in the commit under the approved rule (fictional data).
6. Open decisions 4 to 10 from `V1_STATUS_HANDOFF.md` remain open.
7. Fictional data only. Drafting and matching send grant text, needs and approved evidence to OpenAI, so keep real partner-charity data out until data protection work is done.

## 5. How to run (Mac, from the project folder)

Use your own key in the shell or `.env`. Never paste it into a chat.

```
PYTHONPATH=src:. .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
PYTHONPATH=src .venv/bin/python -m grant_platform.seed --scenario-grants
PYTHONPATH=src .venv/bin/streamlit run src/grant_platform/ui/app.py
PYTHONPATH=src .venv/bin/python -m grant_platform.live_check --run
```

`live_check --run` makes two real requests (one grant match, scenario 6 against "Digital Futures expansion", and one draft for "Describe the impact of your work") in a throwaway in-memory database. It writes `docs/evaluations/live_check_<date>_scenario6.md` with a section for your verdict. It never prints the key. Run on 28 October 2026 and accepted.

## 6. Git

Two local commits, no remote, no push: `764cf9f` (M1, M2, Phase 1) and the Phase 2 commit (see `git log`). Before committing, the file list was checked: nothing from `Grant Assistant/`, `instance/` (including the local database) or `.env` is included, and a scan found no API keys (only obviously fake `sk-test-FAKE...` strings in two tests). The live-check report contains fictional data only. Any further commit needs the product owner's approval.

## 7. Next phase

Not started and not proposed here. The product owner opens a new thread when ready. Likely candidates: the improvements in section 3, authentication and roles, document uploads, automated discovery (plan M8).
