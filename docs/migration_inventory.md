# Migration inventory

Foundation migration, 23 October 2026. All files were copied unchanged (timestamps preserved); no original file was modified. Checksums of every copy matched its source, and every file in the source projects had the same checksum before and after the migration.

## Source projects

| Project | Location | Version control |
| --- | --- | --- |
| PI Research Funding Workspace | Separate local project folder | Git, commit `5a777d3`; uncommitted edit to `data/funding_needs.json` (+24 lines) |
| Legacy Grant Assistant | Kept outside this public copy (read-only, git-ignored in the working project) | None in that folder |
| Bright Path Youth Foundation (fictional demo) | Separate local folder | None |

## Copied

| Source (relative to its project) | Destination | Notes |
| --- | --- | --- |
| PI Tool `app.py` | `src/pi_workspace/app.py` | Streamlit interface (149 KB), written for researchers |
| PI Tool `matcher.py` | `src/pi_workspace/matcher.py` | Six-criterion suitability scoring, Python-calculated bands, hard-exclusion override |
| PI Tool `grant_drafter.py` | `src/pi_workspace/grant_drafter.py` | Draft + missing-information list; idea and grant titles hardcoded, prompt lab-specific |
| PI Tool `opportunity_workflow.py` | `src/pi_workspace/opportunity_workflow.py` | Pursue / Save / Reject, statuses, reminder preferences, saved drafts |
| PI Tool `prototype.py` | `src/pi_workspace/prototype.py` | Match display and best-alternative search; imported by app.py |
| PI Tool `notebook_store.py`, `research_notebook.py` | `src/pi_workspace/` | Notes, attachments, notes-to-idea structuring, voice transcription |
| PI Tool `pilot_storage.py`, `pilot_access.py`, `create_pilot_access.py` | `src/pi_workspace/` | Per-tester storage, atomic JSON writes, pilot login, access-code generator |
| PI Tool `ui_styles.py`, `.streamlit/config.toml` | `src/pi_workspace/` | Design system and theme |
| PI Tool `tests/` (4 files) | `src/pi_workspace/tests/` | Matching reliability, storage isolation, notebooks, reminders |
| PI Tool `requirements.txt`, `.python-version` | `src/pi_workspace/` | streamlit 1.64.0, openai 3.14.1, pydantic 2.13.5; Python 3.13 |
| PI Tool `data/grants.json`, `profile.json`, `funding_needs.json` | `src/pi_workspace/data/` | Fictional Northbridge University lab, 4 grants, 7 research ideas. `funding_needs.json` is the latest local version, including the uncommitted edit |
| PI Tool `DEPLOYMENT.md`, `railway.json`, `start.sh` | `docs/reference/pi_tool/` | Reference only; contains no secrets |
| Operations content `brightpath_dummy_knowledge.py` | `sample_data/brightpath_youth_foundation/` | 42 fictional records, all `safe_to_publish` and `approved`, same metadata schema as the real records it mirrors |

New files written for the foundation: `README.md`, `CLAUDE.md`, `.gitignore`, `.env.example`, `sample_data/README.md`, this inventory.

## Legacy Grant Assistant: documented, not copied

An earlier prototype for a real charity (OpenAI answers over Pinecone retrieval, Streamlit front end) mixed reusable logic with real organisational, staff and patient information in one processor file. Only its design was reused; no code or data was copied.

| Component | Reuse later |
| --- | --- |
| Hard-coded organisational data sets | Real data. Replaced with the fictional Bright Path records |
| Record builders turning data into text + metadata (`knowledge_type`, `source`, `external_use`, `approval_status`) | High: the evidence model |
| Embedding and vector-index upload | Medium. Ran on every import |
| Search and grant-answer generation with 19 evidence rules | High for the prompt; retrieval needed rework |

Known issues to avoid when reusing the design:
- Importing the processor re-embedded every record, re-uploaded to the vector index and printed all records to the log.
- Retrieval ran fixed queries and never used the user's question.
- Metadata was stored, but only record text reached the model, so approval and publish rules were not enforced.

The Bright Path demo also included a small Pinecone-based demo of the same approach. Not copied.

## Deliberately left behind

| Item | Where | Reason |
| --- | --- | --- |
| `.env` (API keys) | A developer's own `.env` outside the project | Secret. Create a new local `.env` from `.env.example` |
| `pilot-access-codes.txt`, `pilot-access-railway.json` | PI Tool folder | Pilot login codes and hashes |
| Railway variables | Railway dashboard | Belong to the deployed PI Tool |
| `opportunities.json`, `match_results.json`, `research_notebooks.json`, `uploads/`, `.workspace.lock` | PI Tool folder | Tester activity, not source |
| `.git`, `__pycache__`, `.DS_Store` | Both | Regenerated or tied to the old repository |
| An earlier Grant Management Platform profile workspace | Separate local folder | Out of scope for this migration |
