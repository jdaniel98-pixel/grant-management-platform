"""Application tracking: save, pursue or reject a grant, move it through statuses to an outcome, and keep its
requirements checklist and tasks. Adapted from the PI Tool's opportunity_workflow.py, using ids instead of titles.

Nothing here submits anything anywhere: statuses record what the people using the platform decided and did."""
import re
import sqlite3
from datetime import date

from grant_platform.services import ValidationError, grants

STATUSES = {
    "saved": "Saved", "pursuing": "Pursuing", "drafting": "Drafting", "submitted": "Submitted",
    "awarded": "Awarded", "unsuccessful": "Unsuccessful", "withdrawn": "Withdrawn", "rejected": "Rejected",
}
# Each status and the statuses it may move to. Awarded and Unsuccessful are outcomes and are final.
TRANSITIONS = {
    "saved": ("pursuing", "rejected"),
    "pursuing": ("drafting", "submitted", "withdrawn", "saved"),
    "drafting": ("pursuing", "submitted", "withdrawn"),
    "submitted": ("awarded", "unsuccessful", "withdrawn"),
    "awarded": (),
    "unsuccessful": (),
    "withdrawn": ("pursuing",),
    "rejected": ("saved",),
}
DECISIONS = {"save": "saved", "pursue": "pursuing", "reject": "rejected"}
OUTCOMES = ("awarded", "unsuccessful")
ACTIVE = ("saved", "pursuing", "drafting", "submitted")
TEXT_LIMIT, NOTE_LIMIT, MAX_AWARD = 300, 1000, 1_000_000_000


def _owned(repository, organisation_id, application_id):
    application = repository.get_application(organisation_id, application_id)
    if application is None:
        raise LookupError(f"No application {application_id} for organisation {organisation_id}")
    return application


def requirement_texts(grant):
    """The checklist a grant implies: each key requirement, plus a prompt to check eligibility and exclusions."""
    items = [" ".join(part.split()) for part in re.split(r"[;\n]+", grant["key_requirements"] or "")]
    texts = [item for item in items if item]
    if grant["eligibility"]:
        texts.append(f"Confirm you meet the eligibility rules: {' '.join(grant['eligibility'].split())}")
    if grant["exclusions"]:
        texts.append(f"Check the exclusions do not apply: {' '.join(grant['exclusions'].split())}")
    return [text[:TEXT_LIMIT] for text in texts]


def _record(repository, organisation_id, application_id, from_status, to_status, note=None):
    repository.add_application_event(organisation_id, application_id, from_status, to_status, note)


def _ensure_requirements(repository, organisation_id, application):
    if application["status"] != "rejected" and not repository.list_requirements(organisation_id, application["id"]):
        for text in requirement_texts(repository.get_grant(application["grant_id"])):
            repository.add_requirement(organisation_id, application["id"], text, "grant")


def decide(repository, organisation_id, grant_id, funding_need_id, decision):
    """Save, pursue or reject a grant for the whole organisation (funding_need_id None) or one need.
    Creates the application, or moves an existing one if that move is allowed."""
    if decision not in DECISIONS:
        raise ValueError(f"Decision must be one of {', '.join(DECISIONS)}")
    if repository.get_organisation(organisation_id) is None:
        raise LookupError(f"No organisation with id {organisation_id}")
    if repository.get_grant(grant_id) is None:
        raise LookupError(f"No grant with id {grant_id}")
    if funding_need_id is not None and repository.get_funding_need(organisation_id, funding_need_id) is None:
        raise LookupError(f"No funding need {funding_need_id} for organisation {organisation_id}")
    target = DECISIONS[decision]
    existing = repository.find_application(organisation_id, funding_need_id, grant_id)
    if existing:
        if existing["status"] == target:
            return existing
        return change_status(repository, organisation_id, existing["id"], target)
    try:
        application_id = repository.create_application(organisation_id, funding_need_id, grant_id, target)
    except sqlite3.IntegrityError:  # another writer created it first
        return decide(repository, organisation_id, grant_id, funding_need_id, decision)
    _record(repository, organisation_id, application_id, None, target)
    application = repository.get_application(organisation_id, application_id)
    _ensure_requirements(repository, organisation_id, application)
    return application


def change_status(repository, organisation_id, application_id, to_status, *, note=None, amount_awarded=None):
    """Move to an allowed next status and record the change. For Awarded, an amount may be given."""
    application = _owned(repository, organisation_id, application_id)
    current = application["status"]
    if to_status not in STATUSES:
        raise ValidationError({"status": "Choose a status from the list"})
    if to_status not in TRANSITIONS[current]:
        raise ValidationError({"status": f"An application that is {STATUSES[current].lower()} cannot move to {STATUSES[to_status].lower()}"})
    errors = {}
    note = " ".join(str(note or "").split()) or None
    if note and len(note) > NOTE_LIMIT:
        errors["note"] = f"Keep this under {NOTE_LIMIT} characters"
    amount = amount_awarded
    if isinstance(amount, str):
        text = re.sub(r"[£,\s]", "", amount)
        amount = int(text) if text.isdigit() else (None if not text else -1)
    if amount is not None and to_status != "awarded":
        errors["amount_awarded"] = "An amount can only be recorded when the application is awarded"
    elif amount is not None and not 0 < amount <= MAX_AWARD:
        errors["amount_awarded"] = "Enter a whole number of pounds above zero, for example 25000"
    if errors:
        raise ValidationError(errors)
    fields = {"status": to_status}
    if to_status in OUTCOMES:
        fields.update(outcome_note=note, amount_awarded=amount)
    if to_status == "submitted":
        fields["submitted_at"] = date.today().isoformat()
    repository.update_application(organisation_id, application_id, **fields)
    _record(repository, organisation_id, application_id, current, to_status, note)
    updated = repository.get_application(organisation_id, application_id)
    _ensure_requirements(repository, organisation_id, updated)
    return updated


def next_statuses(application):
    return TRANSITIONS[application["status"]]


def rejected_grant_ids(repository, organisation_id, funding_need_id=None):
    """Grants this organisation rejected for this scope: they no longer appear as suggestions."""
    return {
        application["grant_id"] for application in repository.list_applications(organisation_id)
        if application["status"] == "rejected" and application["funding_need_id"] == funding_need_id
    }


def add_task(repository, organisation_id, application_id, text, due_date=None):
    _owned(repository, organisation_id, application_id)
    errors = {}
    text = " ".join(str(text or "").split())
    if not text:
        errors["text"] = "This is required"
    elif len(text) > TEXT_LIMIT:
        errors["text"] = f"Keep this under {TEXT_LIMIT} characters"
    try:
        due = grants.parse_deadline(due_date)
    except ValueError as error:
        due = None
        errors["due_date"] = str(error)
    if errors:
        raise ValidationError(errors)
    repository.add_task(organisation_id, application_id, text, due)


def add_requirement(repository, organisation_id, application_id, text):
    _owned(repository, organisation_id, application_id)
    text = " ".join(str(text or "").split())
    if not text:
        raise ValidationError({"text": "This is required"})
    if len(text) > TEXT_LIMIT:
        raise ValidationError({"text": f"Keep this under {TEXT_LIMIT} characters"})
    repository.add_requirement(organisation_id, application_id, text, "manual")


def set_task_done(repository, organisation_id, application_id, task_id, done):
    if not repository.set_task_done(organisation_id, application_id, task_id, done):
        raise LookupError(f"No task {task_id} on application {application_id}")


def set_requirement_done(repository, organisation_id, application_id, requirement_id, done):
    if not repository.set_requirement_done(organisation_id, application_id, requirement_id, done):
        raise LookupError(f"No requirement {requirement_id} on application {application_id}")


def progress(repository, organisation_id, application_id):
    """(requirements done, requirements total, open tasks)."""
    requirements = repository.list_requirements(organisation_id, application_id)
    tasks = repository.list_tasks(organisation_id, application_id)
    return sum(item["done"] for item in requirements), len(requirements), sum(not task["done"] for task in tasks)


def list_applications(repository, organisation_id, *, include_rejected=False):
    """Active work first, nearest deadline first, then finished ones."""
    rows = [row for row in repository.list_applications(organisation_id) if include_rejected or row["status"] != "rejected"]
    for row in rows:
        row["days_left"] = grants.days_until_deadline(row)
    return sorted(rows, key=lambda row: (
        row["status"] not in ACTIVE, row["days_left"] is None, row["days_left"] if row["days_left"] is not None else 0, row["id"],
    ))


def scope_label(application):
    return application["need_title"] or "Whole organisation"
