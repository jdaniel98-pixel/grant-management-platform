# Private Railway pilot setup

Design notes for a private Railway pilot of the PI Research Funding Workspace
prototype (`src/pi_workspace/`). These notes describe the intended configuration;
the application is not deployed from this repository and no live deployment has
been verified.

## Deployment configuration

- Python 3.13 (`.python-version`); dependencies pinned in `requirements.txt`
- Railway start command: `sh start.sh` (already set in `railway.json`)
- Streamlit binds `0.0.0.0` and uses Railway's `PORT`
- Keep `.streamlit/config.toml` for the existing design
- Use one Railway service, one replica and one attached persistent volume
- Mount that volume at **`/persistent`**, outside the source project directory
- Use Railway's HTTPS public domain for access and microphone recording

Set these service variables in Railway (never in committed source):

| Variable | Value |
| --- | --- |
| `PILOT_MODE` | `1` |
| `WORKSPACE_STORAGE_ROOT` | `/persistent` |
| `PILOT_USERS_JSON` | JSON mapping tester names to generated access-code hashes |
| `OPENAI_API_KEY` | Your OpenAI API key |

Railway supplies `PORT`, `RAILWAY_ENVIRONMENT_ID`, and, after attaching the volume,
`RAILWAY_VOLUME_MOUNT_PATH`. Do not set those system values manually. The app
requires private access whenever it detects Railway, even if `PILOT_MODE` is
omitted. It refuses access if credentials are missing or the storage root does
not match the attached volume's reported mount path. Creating a normal container
folder at `/persistent` alone is not sufficient.

## Issue private access codes

Run locally:

```sh
python3 create_pilot_access.py tester_one tester_two
```

This prints a distinct cryptographically random 256-bit access code for each
tester, followed by the `PILOT_USERS_JSON` value containing only SHA-256 hashes.
Send each tester only their own name and code through a private channel. Paste
the JSON hash mapping into Railway variables. Do not share the whole code output,
commit it, use short memorable passwords, or put codes in links/query parameters.
The app has no workspace-ID selector or URL-based workspace access.

The login uses the access code as a private bearer credential. A tester who
shares that code gives another person access to their workspace. Treat it like
a password. No third-party paid authentication provider is needed.

Tester names are stable account identities: keep them unchanged across deploys.
To rotate a code, generate a replacement for the SAME tester name and replace
only its hash in the mapping, preserving all other testers' entries. To revoke
access, remove the tester from the mapping. Credential changes invalidate existing
sessions on their next data access. They do not delete saved data. Renaming a
tester creates a new empty workspace; do not casually rename account keys.

## Isolation and persistence

Each authenticated tester has a server-selected folder:

```
/persistent/workspaces/<server-derived-account-folder>/
  funding_needs.json
  opportunities.json
  research_notebooks.json
  uploads/<notebook-id>/...
  .workspace.lock
```

Identity is kept in server-side Streamlit session state. Data paths are resolved
for each operation, including callbacks; shared module globals are never switched
between tester accounts. Changing a notebook ID cannot open another account's
notebook. Saved applications, rejection history, drafts, reminder preferences,
notes, uploads and new research ideas are all scoped to this folder.

The fictional lab and sample grants stay read-only in source `data/`. Each
workspace receives the bundled funding-needs collection once on first use. It is
not overwritten on later logins or deployments. New ideas are appended only to
that tester's persisted copy. Local developer notebooks, opportunities and
uploads are never copied into pilot accounts.

The existing development reset affects ONLY the signed-in tester's opportunity
records (including their drafts, reminders and rejected combinations) and their
browser session state. Existing reset semantics for notes/ideas are unchanged.
It never resets another account. Sign out clears that browser session without
removing saved data; the next tester must authenticate independently.

All saved-data mutations are serialized by workspace-level thread and advisory
process locks. JSON replacements are atomic, and full read-modify-write
transactions are locked. Attachment saving/deletion shares that lock. Simultaneous
adds do not lose independent entries/decisions or duplicate deduplicated entries.
Editing the same field from two browser tabs remains last-save-wins; there is no
merge/version/conflict UI. No lock is held during OpenAI API calls.

## Before inviting testers

1. Attach the volume and set the variables above
2. Generate and privately distribute separate credentials
3. After deploying, smoke-test two accounts and verify isolation
4. Save a note, attachment and draft, redeploy, and confirm they persist
5. Configure volume backups and keep the service single-replica

No live Railway restart/redeployment has been tested. Local checks cover durable rereads/fresh sessions, authentication,
revocation, tenant isolation, attachment deletion and cross-process simultaneous
writes without real OpenAI calls.

## Remaining pilot limitations

- This is a small private pilot, not full account management: no password recovery,
  MFA or persistent login cookie; users may need to re-enter their code after a
  refresh/reconnection or restart. The organiser controls issuance and revocation
- Failed-login cooldown is in memory and resets on process restart; codes must
  remain high entropy. This is not intended for a large public launch
- Saved data persists on the mounted volume; unsaved UI edits and match caches
  held only in session memory can be lost when the session/process ends
- Multiple independent service replicas/storage volumes are not supported
- Volume removal/corruption still requires backups; uploads consume volume space
- Pilot users can trigger paid OpenAI calls using the organiser's key; there are
  no per-account usage quotas in this change
- Attachments are saved files only; existing no-AI-extraction behaviour is preserved
- Email/calendar notifications remain inactive
- Local pilot testing requires macOS/Linux (`fcntl` advisory locks); Railway Linux
  is supported. Plain local mode remains unchanged when `PILOT_MODE` is unset
- Do not enable Streamlit static file serving for private uploads

`.gitignore` excludes secrets, local state, uploads and access-code export files.

## Railway references

- https://docs.railway.com/deployments/start-command
- https://docs.railway.com/services
- https://docs.railway.com/volumes/reference
- https://docs.railway.com/variables/reference
