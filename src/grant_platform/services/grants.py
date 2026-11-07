"""Grant rules: cleaning, URL normalisation, duplicate detection and verification.

Grants are shared reference data. Nothing here fetches a web page: a URL is stored with the facts a person types,
or with the facts a monitored source's adapter parsed (services/discovery.py). Either way the grant starts unverified.
"""
import re
import sqlite3
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit

from grant_platform.models import Grant
from grant_platform.services import ValidationError

ORIGINS = {"manual": "Entered by hand", "url": "Added by web address", "monitored": "Found by monitoring"}
STATUSES = {"open": "Open", "closed": "Closed", "unknown": "Not known"}
TITLE_LIMIT, SHORT_LIMIT, LONG_LIMIT, URL_LIMIT = 200, 300, 4000, 2000
# Until accounts and roles exist (plan M10) nobody is signed in, so the record says so rather than naming a person.
DEVELOPMENT_VERIFIER = "Development-only verification (no login)"

_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "msclkid"}
_INDEX_FILE = re.compile(r"/index\.(html?|php)$", re.IGNORECASE)


class DuplicateGrantError(ValidationError):
    """The grant is already stored; .existing is the stored row."""

    def __init__(self, field, existing, reason):
        self.existing = existing
        super().__init__({field: f"{reason}: #{existing['id']} {existing['title']} ({existing['funder']})"})


def normalise_url(value):
    """Return one canonical form so the same page typed differently is recognised: https, no www, no
    fragment or tracking parameters, no trailing slash, sorted query. Raises ValueError if unusable."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("Enter a web address")
    if len(text) > URL_LIMIT or re.search(r"\s", text):
        raise ValueError("This does not look like a web address")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https"):
        raise ValueError("Use an address that starts with http:// or https://")
    try:
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        raise ValueError("This does not look like a web address") from None
    if parts.username is not None or parts.password is not None:
        raise ValueError("Leave out any user name or password from the address")
    if "." not in host.strip(".") or host.startswith(".") or host.endswith("."):
        raise ValueError("This does not look like a web address")
    host = host.removeprefix("www.")
    if port not in (None, 80, 443):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parts.path)
    path = _INDEX_FILE.sub("", path).rstrip("/")
    query = sorted(
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_PARAMS
    )
    return f"https://{host}{path}" + (f"?{urlencode(query)}" if query else "")


def dedupe_key(funder, title):
    """Funder and title with case, punctuation and spacing removed."""
    def squash(text):
        return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())
    return f"{squash(funder)}|{squash(title)}"


def parse_deadline(value):
    """ISO (2027-03-31) or UK (31/03/2027) to ISO text; None when blank. Raises ValueError otherwise."""
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    raise ValueError("Enter a date such as 31/03/2027")


def _text(raw, field, limit, errors, multiline=False):
    value = raw.get(field)
    if multiline:
        text = "\n".join(line.rstrip() for line in str(value or "").strip().splitlines()).strip()
    else:
        text = " ".join(str(value or "").split())
    if len(text) > limit:
        errors[field] = f"Keep this under {limit} characters"
    return text or None


def clean_grant(raw, origin):
    errors = {}
    if origin not in ORIGINS:
        raise ValueError(f"Unsupported origin {origin!r}")
    title = _text(raw, "title", TITLE_LIMIT, errors)
    funder = _text(raw, "funder", SHORT_LIMIT, errors)
    for field, value in (("title", title), ("funder", funder)):
        if not value:
            errors[field] = "This is required"

    source_url = canonical_url = None
    entered = str(raw.get("source_url") or "").strip()
    if entered or origin in ("url", "monitored"):
        try:
            canonical_url = normalise_url(entered)
            source_url = entered
        except ValueError as error:
            errors["source_url"] = str(error)

    status = raw.get("status") or "unknown"
    if status not in STATUSES:
        errors["status"] = "Choose open, closed or not known"
    try:
        deadline_date = parse_deadline(raw.get("deadline_date"))
    except ValueError as error:
        deadline_date = None
        errors["deadline_date"] = str(error)

    fields = {name: _text(raw, name, SHORT_LIMIT, errors) for name in ("scope", "funding", "duration", "deadline_text")}
    fields.update({name: _text(raw, name, LONG_LIMIT, errors, multiline=True)
                   for name in ("purpose", "eligibility", "key_requirements", "exclusions")})
    if errors:
        raise ValidationError(errors)
    return Grant(
        origin=origin, source_url=source_url, canonical_url=canonical_url, title=title, funder=funder,
        status=status, deadline_date=deadline_date, dedupe_key=dedupe_key(funder, title), **fields,
    )


def create_grant(repository, raw, origin="manual"):
    """Validate, block duplicates (same web address first, then same funder and title) and store as unverified."""
    return create_grant_record(repository, clean_grant(raw, origin))


def create_grant_record(repository, grant):
    """Store an already-cleaned Grant as unverified, blocking duplicates as create_grant does."""
    if grant.canonical_url:
        existing = repository.find_grant_by_url(grant.canonical_url)
        if existing:
            raise DuplicateGrantError("source_url", existing, "This web address is already saved")
    existing = repository.find_grant_by_dedupe_key(grant.dedupe_key)
    if existing:
        raise DuplicateGrantError("title", existing, "A grant with this funder and title is already saved")
    try:
        grant_id = repository.add_grant(grant)
    except sqlite3.IntegrityError:  # lost a race with another writer
        existing = (grant.canonical_url and repository.find_grant_by_url(grant.canonical_url)) or repository.find_grant_by_dedupe_key(grant.dedupe_key)
        if existing:
            raise DuplicateGrantError("title", existing, "This grant is already saved") from None
        raise
    return repository.get_grant(grant_id)


def mark_verified(repository, grant_id, verified_by=DEVELOPMENT_VERIFIER):
    """Record that the grant's details were checked against the funder's own page. Already-verified grants
    keep their original verifier and time."""
    grant = repository.get_grant(grant_id)
    if grant is None:
        raise LookupError(f"No grant with id {grant_id}")
    if grant["verification_status"] != "verified":
        repository.mark_grant_verified(grant_id, verified_by)
    return repository.get_grant(grant_id)


def days_until_deadline(grant, today=None):
    """Whole days from today to the deadline (negative once passed); None without a parsed deadline."""
    if not grant["deadline_date"]:
        return None
    return (date.fromisoformat(grant["deadline_date"]) - (today or date.today())).days


def deadline_label(grant, today=None):
    days = days_until_deadline(grant, today)
    shown = grant["deadline_text"] or grant["deadline_date"]
    if days is None:
        return shown or "No deadline recorded"
    when = "today" if days == 0 else f"in {days} day{'s' if days != 1 else ''}" if days > 0 else f"passed {-days} day{'s' if days != -1 else ''} ago"
    return f"{shown or grant['deadline_date']} ({when})"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dismiss_review(repository, grant_id, now=None):
    """A person has looked at a flagged grant and wants it left as it is."""
    if repository.get_grant(grant_id) is None:
        raise LookupError(f"No grant with id {grant_id}")
    if repository.clear_grant_review(grant_id):
        repository.add_grant_event(grant_id, "review_cleared", {"reason": "Reviewed by a person", "by": DEVELOPMENT_VERIFIER}, now or _now())
    return repository.get_grant(grant_id)


def close_grant(repository, grant_id, now=None):
    """A person confirms the grant is no longer open. This is the only way a flagged grant gets closed."""
    grant = repository.get_grant(grant_id)
    if grant is None:
        raise LookupError(f"No grant with id {grant_id}")
    now = now or _now()
    if grant["status"] != "closed":
        repository.set_grant_status(grant_id, "closed")
        repository.add_grant_event(grant_id, "closed_by_person", {"by": DEVELOPMENT_VERIFIER}, now)
    repository.clear_grant_review(grant_id)
    return repository.get_grant(grant_id)


def close_expired(repository, today=None, now=None):
    """Close grants whose recorded closing date has passed. Only a parsed closing date counts: grants with no date, or
    where the source says there is no fixed date, are left alone. Returns the ids closed."""
    today = today or date.today()
    now = now or _now()
    closed = []
    for grant in repository.list_grants_past_deadline(today.isoformat()):
        repository.set_grant_status(grant["id"], "closed")
        repository.add_grant_event(grant["id"], "closed_by_deadline", {"deadline_date": grant["deadline_date"], "source": "housekeeping"}, now)
        closed.append(grant["id"])
    return closed
