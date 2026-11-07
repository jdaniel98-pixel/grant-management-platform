# Phase 3 handoff: Add automation

Date: 29 October 2026. Phase 3 is finished and stopped. Phase 4 has not been started.
Test result: **418 tests, all passing** (407 passed on the product owner's Mac before the live-run fixes in section 7; 418 pass in the cloud copy after them). Offline, fictional data only, no real email.

## 1. What was built

| Milestone | What you get |
|---|---|
| 3.1 Discovery | `grant_source` registry and an adapter pattern. First real source: **GOV.UK Find a grant** (Open Government Licence v3.0; no robots.txt published; terms checked 29 Oct 2026; ordinary public pages only, reading each page's embedded JSON, no AI). Polite fetcher: identifies itself, 5 s between requests, page cap per run, own host only. Everything found is saved **unverified**. Only grants open to non-profits are kept. New sources need only a registry row and an adapter. |
| 3.2 Change detection | Each fetch is compared with the saved grant. Only **material** changes (title, funder, eligibility, funding, deadline, purpose, requirements, scope, status) reset verification; formatting alone never does. Snapshots, an event history, "last checked" and source shown on the Grants screen. A grant that disappears from a listing or whose page fails is **flagged for review, never closed**. Grants close automatically only when a recorded deadline has clearly passed (`jobs housekeep`) or the person closes them. |
| 3.3 Alerts | Per-organisation notification contacts (development-only, no login yet) with recorded consent, preferences (weekly, daily or never; weekday; reminders on or off) and an unsubscribe code. Digest lists newly relevant grants for that organisation only; a grant is offered to a contact once (`message_log`). Notifications page. |
| 3.4 Reminders and email | Deadline reminders go to each consenting contact once, with the unsubscribe footer. Delivery channel: **local outbox by default**; SMTP only when `EMAIL_DELIVERY=smtp` and host, sender and allow-list are all set, and then only to allow-listed addresses. A message is logged only if delivery worked, so failures retry on the next run without repeating to others. `jobs run-daily` runs everything with a run log; launchd template in `scheduling/` (not installed). |

## 2. Commands (Mac, project folder)

    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs sources                      # list sources (all start disabled)
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs discover find-a-grant --dry-run
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs sources --enable find-a-grant
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs housekeep [--dry-run]
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs match [--dry-run]            # needs OPENAI_API_KEY
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs digest [--dry-run]
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs notify [--dry-run]
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs run-daily
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs email-status
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs unsubscribe CODE
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs send-test --to ADDRESS --confirm-real-send   # one real email; needs approval

## 3. Safeguards in place

- Email is off unless fully configured, consent is recorded, and the recipient is on `EMAIL_ALLOWLIST`. No real email has ever been sent.
- Organisation isolation by construction: contact, digest and reminder queries are scoped by organisation; two-charity tests cover each path.
- AI output and discovered grants are never treated as verified; verification is human only.
- Nothing is installed or scheduled automatically. `Grant Assistant/` was never touched.

## 4. Outstanding issues and decisions for later

1. **Re-alerting on changed grants:** a grant is alerted once; a later material change does not re-alert. Decide if it should.
2. **Mac fixture leftovers:** `tests/fixtures/sources/find_a_grant/` holds `.status` files and `robots.txt` from the curl download that no test uses. They were left out of the Phase 3 commit and can be deleted. One saved grant page includes a public Forestry Commission contact address (Open Government Licence content, not personal data).
3. **Phase 4 (production email):** choose a provider, domain authentication (SPF, DKIM, DMARC), bounce and complaint handling, PECR/GDPR review of consent wording, and replace the dev-only contact model with login and roles. The Notifications page carries a "no login yet" warning until then.
4. **Find a Grant first live run:** do the `--dry-run` and read it before enabling. Some grants show a closing date that is only the end of confirmed funding; these are stored as "no fixed closing date", not as a deadline.
5. **Unsubscribe** is by code or on the Notifications page; there is no public web link yet (needs a hosted app).
6. **Regional/international sources:** the adapter pattern is ready; each new source needs its own terms check and your approval.
7. launchd needs the Mac awake and logged in; see `scheduling/SCHEDULING.md`.

## 7. Live dry run and fixes made after the first handoff (30 October 2026)

The product owner ran the Find a Grant dry run on the product owner's Mac. Four problems were found and fixed; the final full dry run was **ok: 98 pages, 85 grants found, nothing saved**. Source is still disabled.

1. A dry run is now allowed on a disabled source (it saves nothing and does not enable it); a real run is still refused.
2. Certificates: the fetcher uses the `certifi` bundle (verification fully on) and error messages now name the real cause.
3. Page cap: grants are read as each listing page is read, so a capped run keeps what it reached (the listing alone is 13 pages).
4. Redirects: only `www.find-government-grants.service.gov.uk` to `find-government-grants.service.gov.uk` is accepted (exact hosts). One failing grant page is skipped and reported; three failures in a row stop the run.
5. Duplicates: a grant listed twice with the same funder and title (for example "Youth Jobs Grant") is kept once per run and reported as "duplicate", with both addresses. Before this, a dry run counted it twice and a real run would have let the two pages overwrite each other every run. Grants with the same title but a different funder are different grants and are both kept. The cause of the live duplicate was not inspected (no live data in the cloud copy); rerun the dry run and look for the `duplicate` lines.

## 5. Git

One local commit for Phase 3 was approved by the product owner on 30 October 2026. Nothing has been pushed. `Grant Assistant/`, `.env` and `instance/` are git-ignored and were verified not staged.

## 6. Next phase

Wait for the product owner's explicit approval before any further development.
