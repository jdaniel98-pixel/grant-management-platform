"""Funding need rules: cleaning, validation and per-organisation title uniqueness."""
import re
import sqlite3

from grant_platform.models import FundingNeed
from grant_platform.services import ValidationError

KINDS = {"programme": "Programme", "project": "Project", "core": "Operating costs"}
TITLE_LIMIT, DESCRIPTION_LIMIT, SHORT_LIMIT, BENEFICIARIES_LIMIT = 120, 2000, 200, 500
MAX_AMOUNT = 1_000_000_000


def clean_need(raw):
    errors = {}
    title = " ".join(str(raw.get("title") or "").split())
    if not title:
        errors["title"] = "This is required"
    elif len(title) > TITLE_LIMIT:
        errors["title"] = f"Keep this under {TITLE_LIMIT} characters"

    kind = raw.get("kind")
    if kind not in KINDS:
        errors["kind"] = "Choose programme, project or operating costs"

    description = "\n".join(line.rstrip() for line in str(raw.get("description") or "").strip().splitlines()).strip()
    if not description:
        errors["description"] = "This is required"
    elif len(description) > DESCRIPTION_LIMIT:
        errors["description"] = f"Keep this under {DESCRIPTION_LIMIT} characters"

    amount = raw.get("amount_sought")
    if isinstance(amount, str):
        text = re.sub(r"[£,\s]", "", amount)
        if not text:
            amount = None
        elif text.isdigit():
            amount = int(text)
        else:
            amount = None
            errors["amount_sought"] = "Enter a whole number of pounds, for example 25000"
    if isinstance(amount, int) and not 0 < amount <= MAX_AMOUNT:
        errors["amount_sought"] = "Enter an amount above zero"

    optional = {}
    for field, limit in (("timeframe", SHORT_LIMIT), ("geography", SHORT_LIMIT), ("beneficiaries", BENEFICIARIES_LIMIT)):
        text = " ".join(str(raw.get(field) or "").split()) or None
        if text and len(text) > limit:
            errors[field] = f"Keep this under {limit} characters"
        optional[field] = text
    if errors:
        raise ValidationError(errors)
    return FundingNeed(title=title, kind=kind, description=description, amount_sought=amount, **optional)


def _check_title_free(repository, organisation_id, title, own_id=None):
    for other in repository.list_funding_needs(organisation_id):
        if other["id"] != own_id and other["title"].casefold() == title.casefold():
            raise ValidationError({"title": "You already have a funding need with this title"})


def create_need(repository, organisation_id, raw):
    if repository.get_organisation(organisation_id) is None:
        raise LookupError(f"No organisation with id {organisation_id}")
    need = clean_need(raw)
    _check_title_free(repository, organisation_id, need.title)
    repository.add_funding_need(organisation_id, need)
    return repository.get_funding_need_by_title(organisation_id, need.title)


def update_need(repository, organisation_id, need_id, raw):
    if repository.get_funding_need(organisation_id, need_id) is None:
        raise LookupError(f"No funding need {need_id} for organisation {organisation_id}")
    need = clean_need(raw)
    _check_title_free(repository, organisation_id, need.title, own_id=need_id)
    try:
        repository.update_funding_need(organisation_id, need_id, need)
    except sqlite3.IntegrityError as error:
        raise ValidationError({"title": "You already have a funding need with this title"}) from error
    return repository.get_funding_need(organisation_id, need_id)


def format_amount(amount):
    return "Not provided" if amount is None else f"£{amount:,}"
