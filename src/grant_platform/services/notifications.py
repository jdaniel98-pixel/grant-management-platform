"""Alerts about new relevant grants: contacts and consent, preferences, who is due a digest, and the digest itself.

Everything here is scoped to one organisation. A digest is built only from that organisation's own match results and
needs, and only for contacts of that organisation, so one charity can never see another's grants, needs or contacts.
Nothing is emailed: messages go to a delivery channel (the local outbox folder for now) and each grant is offered to
each contact at most once.
"""
import re
import secrets
import sqlite3
from datetime import date, datetime, timedelta, timezone

from grant_platform.services import ValidationError, grants, matching
from grant_platform.services.delivery import DeliveryError, Message

CONSENT_WORDING = (
    "The person named here has agreed to receive email alerts about funding opportunities, and deadline reminders, for "
    "this organisation. They can stop them at any time."
)
RECORDER = "Development-only record (no login)"
FREQUENCIES = {"weekly": "Once a week", "daily": "Every day there is something new", "off": "Never"}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
BANDS = ("Strong fit", "Potential fit")
MAX_ITEMS = 15
MAX_MISSING = 3
_EMAIL = re.compile(r"^[^@\s<>\",;:]+@[^@\s<>\",;:]+\.[^@\s<>\",;:]+$")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _line(text):
    """One line of plain text: no line breaks, so user text can never add a header to a message."""
    return " ".join(str(text or "").split())


# Contacts

def clean_contact(name, email):
    errors = {}
    name = _line(name)
    email = _line(email).lower()
    if not name:
        errors["name"] = "This is required"
    elif len(name) > 100 or re.search(r"[<>\"]", name):
        errors["name"] = "Use a plain name of up to 100 characters, without < > or quotation marks"
    if not email:
        errors["email"] = "This is required"
    elif len(email) > 200 or not _EMAIL.match(email):
        errors["email"] = "This does not look like an email address"
    if errors:
        raise ValidationError(errors)
    return name, email


def add_contact(repository, organisation_id, name, email, *, consent_given=False, now=None):
    """Add a notification contact. Without recorded consent the contact is stored but never emailed."""
    if repository.get_organisation(organisation_id) is None:
        raise LookupError(f"No organisation with id {organisation_id}")
    name, email = clean_contact(name, email)
    now = now or _now()
    if repository.find_contact_by_email(organisation_id, email):
        raise ValidationError({"email": "This address is already a contact for this organisation"})
    contact_id = repository.add_contact(organisation_id, {
        "name": name, "email": email, "unsubscribe_token": secrets.token_urlsafe(24), "created_at": now, "updated_at": now,
        "consent_given_at": now if consent_given else None, "consent_wording": CONSENT_WORDING if consent_given else None,
        "consent_recorded_by": RECORDER if consent_given else None, "alerts_since": now if consent_given else None,
    })
    return repository.get_contact(organisation_id, contact_id)


def record_consent(repository, organisation_id, contact_id, now=None):
    contact = _contact(repository, organisation_id, contact_id)
    now = now or _now()
    repository.update_contact(organisation_id, contact_id, {
        "consent_given_at": now, "consent_wording": CONSENT_WORDING, "consent_recorded_by": RECORDER, "active": 1, "unsubscribed_at": None,
        "alerts_since": now if not contact["alerts_since"] or contact["unsubscribed_at"] else contact["alerts_since"],
    }, now)
    return repository.get_contact(organisation_id, contact_id)


def update_preferences(repository, organisation_id, contact_id, now=None, **values):
    _contact(repository, organisation_id, contact_id)
    errors = {}
    if "alerts_frequency" in values and values["alerts_frequency"] not in FREQUENCIES:
        errors["alerts_frequency"] = "Choose weekly, daily or never"
    if "digest_weekday" in values and values["digest_weekday"] not in range(7):
        errors["digest_weekday"] = "Choose a day of the week"
    if "min_band" in values and values["min_band"] not in BANDS:
        errors["min_band"] = "Choose Strong fit or Potential fit"
    unknown = set(values) - set(repository.CONTACT_PREFERENCES)
    if unknown:
        errors.update({name: "This cannot be changed here" for name in unknown})
    if errors:
        raise ValidationError(errors)
    values = {name: int(value) if isinstance(value, bool) else value for name, value in values.items()}
    repository.update_contact(organisation_id, contact_id, values, now or _now())
    return repository.get_contact(organisation_id, contact_id)


def unsubscribe(repository, token, now=None):
    """Stop all email to the contact that owns this token. Returns the contact, or None if the token is unknown."""
    contact = repository.find_contact_by_token(str(token or "").strip())
    if contact is None:
        return None
    now = now or _now()
    repository.update_contact(contact["organisation_id"], contact["id"], {"active": 0, "unsubscribed_at": now}, now)
    return repository.get_contact(contact["organisation_id"], contact["id"])


def _contact(repository, organisation_id, contact_id):
    contact = repository.get_contact(organisation_id, contact_id)
    if contact is None:
        raise LookupError(f"No contact {contact_id} for organisation {organisation_id}")
    return contact


def emailable(contact):
    """Consent recorded, not paused or unsubscribed."""
    return bool(contact["consent_given_at"]) and contact["active"] and not contact["unsubscribed_at"]


# What is worth telling a contact about

def relevant_matches(repository, contact, today=None):
    """This contact's organisation's matches that are new to the contact and worth an alert, best first. One per grant."""
    today = today or date.today()
    organisation_id = contact["organisation_id"]
    bands = {"Strong fit"} | ({"Potential fit"} if contact["min_band"] == "Potential fit" else set())
    statuses = {"eligible"} | ({"unclear"} if contact["include_unclear"] else set())
    already = repository.alerted_grant_ids(contact["id"])
    handled = {a["grant_id"] for a in repository.list_applications(organisation_id)}
    needs = {need["id"]: need for need in repository.list_funding_needs(organisation_id)}
    best = {}
    for need_id in (None, *needs):
        for match in repository.list_matches(organisation_id, need_id):
            grant = repository.get_grant(match["grant_id"])
            days = grants.days_until_deadline(grant, today)
            if (match["band"] not in bands or match["eligibility_status"] not in statuses or match["recommendation"] == "Reject"
                    or grant["status"] == "closed" or (days is not None and days < 0)
                    or match["grant_id"] in already or match["grant_id"] in handled
                    or (contact["verified_only"] and grant["verification_status"] != "verified")
                    or (contact["alerts_since"] and grant["first_seen_at"] < contact["alerts_since"])):
                continue
            current = best.get(match["grant_id"])
            if current is None or matching.rank([current[0], match])[0] is match:
                best[match["grant_id"]] = (match, grant, needs.get(need_id))
    ordered = matching.rank([item[0] for item in best.values()])
    return [best[match["grant_id"]] for match in ordered]


def period_key(contact, today):
    """The weekly (or daily) slot a digest fills, or None when it is not that contact's turn yet."""
    if contact["alerts_frequency"] == "daily":
        return f"day:{today.isoformat()}"
    if contact["alerts_frequency"] == "weekly" and today.weekday() >= contact["digest_weekday"]:
        year, week, _ = today.isocalendar()
        return f"week:{year}-W{week:02d}"
    return None


def period_label(contact, today):
    if contact["alerts_frequency"] == "daily":
        return today.strftime("%d %B %Y").lstrip("0")
    return "week of " + (today - timedelta(days=today.weekday())).strftime("%d %B %Y").lstrip("0")


def render_digest(organisation, contact, items, today, shown):
    """items: [(match, grant, need)]. shown: how many to list in full. Returns a Message."""
    subject = f"{len(items)} new funding match{'es' if len(items) != 1 else ''} for {_line(organisation['name'])} ({period_label(contact, today)})"
    lines = [
        f"Hello {_line(contact['name'])},",
        "",
        f"These grants have been found since {contact['alerts_since'][:10] if contact['alerts_since'] else 'you signed up'} and look relevant to "
        f"{_line(organisation['name'])}. The fit is an AI assessment from your profile and funding needs: use it as a guide and read the "
        "funder's own page before you decide anything.",
        "",
    ]
    for number, (match, grant, need) in enumerate(items[:shown], start=1):
        verified = grant["verification_status"] == "verified"
        eligibility = "Looks eligible" if match["eligibility_status"] == "eligible" else "Eligibility unclear: check the points below"
        lines += [
            f"{number}. {_line(grant['title'])}",
            f"   Funder: {_line(grant['funder'])}",
            f"   For: {_line(need['title']) if need else 'your organisation as a whole'}",
            f"   Fit: {match['band']} ({match['suitability_total']} out of 25). {eligibility}",
            f"   Deadline: {grants.deadline_label(grant, today)}",
        ]
        if grant["funding"]:
            lines.append(f"   Funding: {_line(grant['funding'])}")
        if match["summary"]:
            lines.append(f"   Why: {_line(match['summary'])}")
        for gap in match["missing_information"][:MAX_MISSING]:
            lines.append(f"   To check: {_line(gap)}")
        lines.append("   Details checked against the funder's page by a person: " + (f"yes ({grant['verified_at'][:10]})" if verified else "not yet"))
        if grant["canonical_url"]:
            lines.append(f"   Source: {_line(grant['canonical_url'])}")
        lines.append("")
    if len(items) > shown:
        lines += [f"... and {len(items) - shown} more. They will be in your next message.", ""]
    lines += [
        "-" * 40,
        f"You are receiving this because {_line(organisation['name'])} added you for funding alerts and consent was recorded on "
        f"{contact['consent_given_at'][:10]}. Frequency: {FREQUENCIES[contact['alerts_frequency']].lower()}; "
        f"minimum fit: {contact['min_band']}.",
        f"To stop these messages, use the Notifications page, or give this code to whoever runs the platform: {contact['unsubscribe_token']}",
    ]
    return Message(contact["name"], contact["email"], subject, "\n".join(lines))


def footer(organisation, contact):
    return [
        "-" * 40,
        f"You are receiving this because {_line(organisation['name'])} added you for funding alerts and reminders, and consent was recorded on "
        f"{contact['consent_given_at'][:10]}.",
        f"To stop these messages, use the Notifications page, or give this code to whoever runs the platform: {contact['unsubscribe_token']}",
    ]


def with_footer(text, organisation, contact):
    return text.rstrip() + "\n\n" + "\n".join(footer(organisation, contact))


def digest_name(today, contact, key):
    """Built only from numbers and dates, never from user text."""
    return f"{today.isoformat()}_digest_contact-{contact['id']}_{key.replace(':', '-')}.txt"


def due_digests(repository, today):
    """[(organisation, contact, key, items)] for every digest that should go out today."""
    due = []
    for organisation in repository.list_organisations():
        for contact in repository.list_contacts(organisation["id"]):
            key = period_key(contact, today) if emailable(contact) else None
            if key is None or repository.has_message(contact["id"], "digest", key):
                continue
            items = relevant_matches(repository, contact, today)
            if items:
                due.append((organisation, contact, key, items))
    return due


def send_digests(repository, deliver, today=None, dry_run=False, now=None, failures=None):
    """Produce each due digest through the delivery channel and log it. Returns [(file name, subject, to)].
    A digest and the alerts in it are logged only if delivery succeeded, so a failure is retried on the next run.
    A message the channel refuses (DeliveryError) is added to `failures` as (address, reason) and the others carry on."""
    today = today or date.today()
    now = now or _now()
    done = []
    for organisation, contact, key, items in due_digests(repository, today):
        shown = min(len(items), MAX_ITEMS)
        message = render_digest(organisation, contact, items, today, shown)
        name = digest_name(today, contact, key)
        if not dry_run:
            try:
                with repository.connection:  # commits all the log rows with a successful delivery, or none of them
                    repository.log_message(organisation["id"], contact["id"], "digest", key, now, name)
                    for _, grant, _ in items[:shown]:
                        repository.log_message(organisation["id"], contact["id"], "grant_alert", f"grant:{grant['id']}", now)
                    deliver(message, name)
            except sqlite3.IntegrityError:
                continue  # already produced by another run
            except DeliveryError as error:
                if failures is not None:
                    failures.append((message.address(), str(error)))
                continue
        done.append((name, message.subject, message.address()))
    return done
