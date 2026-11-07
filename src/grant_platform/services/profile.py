"""Organisation profile rules: cleaning, validation, provenance and completeness."""
import re
from datetime import date

from grant_platform.models import Organisation
from grant_platform.services import ValidationError

LEGAL_FORMS = (
    "Registered charity (England and Wales)",
    "Scottish registered charity (SCIO or other)",
    "Charity registered in Northern Ireland",
    "Charitable incorporated organisation (CIO)",
    "Charitable company limited by guarantee",
    "Community interest company (CIC)",
    "Unincorporated association",
    "Other",
)
INCOME_BANDS = ("Under £10,000", "£10,000 to £100,000", "£100,000 to £500,000", "£500,000 to £1 million", "£1 million to £5 million", "Over £5 million")
ORGANISATION_TYPES = ("Charity", "Non-governmental organisation", "Community group", "Social enterprise", "Other")

FIELD_LABELS = {
    "name": "Organisation name",
    "organisation_type": "Type of organisation",
    "registration_country": "Country of registration",
    "legal_form": "Legal form",
    "registration_number": "Registration number",
    "founded_year": "Year founded",
    "annual_income_band": "Annual income",
    "regions": "Where you work",
    "mission": "Mission",
    "activities": "Main activities",
    "beneficiaries": "Who you support",
}
EDITABLE_FIELDS = tuple(FIELD_LABELS)
REQUIRED_FIELDS = ("name", "mission")
# Facts that funders' eligibility rules commonly turn on. Missing ones are unknown, never a reason for ineligibility.
KEY_FACTS = ("registration_country", "legal_form", "registration_number", "founded_year", "annual_income_band", "regions", "mission", "activities", "beneficiaries")

SHORT_LIMIT, LONG_LIMIT, MAX_REGIONS = 200, 3000, 20
_REGISTRATION_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 /-]{2,19}$")


def _single_line(value):
    text = " ".join(str(value or "").split())
    return text or None


def _multi_line(value):
    text = "\n".join(line.rstrip() for line in str(value or "").strip().splitlines()).strip()
    return text or None


def _regions(value):
    items = value if isinstance(value, (list, tuple)) else re.split(r"[\n,;]+", str(value or ""))
    seen, regions = set(), []
    for item in items:
        region = " ".join(str(item).split())
        if region and region.casefold() not in seen:
            seen.add(region.casefold())
            regions.append(region)
    return regions


def clean_profile(raw):
    """Return a complete dict of editable fields, or raise ValidationError listing every problem."""
    errors, values = {}, {}
    for field in ("name", "organisation_type", "registration_country", "legal_form", "registration_number", "annual_income_band"):
        values[field] = _single_line(raw.get(field))
        if values[field] and len(values[field]) > SHORT_LIMIT:
            errors[field] = f"Keep this under {SHORT_LIMIT} characters"
    for field in ("mission", "activities", "beneficiaries"):
        values[field] = _multi_line(raw.get(field))
        if values[field] and len(values[field]) > LONG_LIMIT:
            errors[field] = f"Keep this under {LONG_LIMIT} characters"
    values["regions"] = _regions(raw.get("regions"))
    if len(values["regions"]) > MAX_REGIONS or any(len(r) > SHORT_LIMIT for r in values["regions"]):
        errors["regions"] = f"List up to {MAX_REGIONS} places, each under {SHORT_LIMIT} characters"

    if values["legal_form"] and values["legal_form"] not in LEGAL_FORMS:
        errors["legal_form"] = "Choose one of the listed legal forms"
    if values["annual_income_band"] and values["annual_income_band"] not in INCOME_BANDS:
        errors["annual_income_band"] = "Choose one of the listed income bands"
    if values["registration_number"] and not _REGISTRATION_NUMBER.match(values["registration_number"]):
        errors["registration_number"] = "Use 3 to 20 letters, numbers, spaces, hyphens or slashes"

    year = raw.get("founded_year")
    year = None if year in (None, "") else year
    if year is not None:
        try:
            year = int(str(year).strip())
        except ValueError:
            year = None
            errors["founded_year"] = "Enter a four-digit year"
        else:
            if not 1800 <= year <= date.today().year:
                errors["founded_year"] = f"Enter a year between 1800 and {date.today().year}"
    values["founded_year"] = year

    for field in REQUIRED_FIELDS:
        if not values[field]:
            errors.setdefault(field, "This is required")
    if errors:
        raise ValidationError(errors)
    return values


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:56] or "organisation"


def unique_slug(repository, name):
    base = slugify(name)
    slug, number = base, 1
    while repository.find_organisation_id(slug) is not None:
        number += 1
        slug = f"{base}-{number}"
    return slug


def _has_value(value):
    return value not in (None, "", [])


def create_organisation(repository, raw):
    """Create an organisation from questionnaire answers; every answer is marked as entered by the user."""
    values = clean_profile(raw)
    sources = {field: "user" for field, value in values.items() if _has_value(value)}
    organisation = Organisation(slug=unique_slug(repository, values["name"]), field_sources=sources, **values)
    return repository.create_organisation(organisation)


def update_profile(repository, organisation_id, raw):
    """Save edits. Returns the changed field names; unchanged fields keep their original source."""
    existing = repository.get_organisation(organisation_id)
    if existing is None:
        raise LookupError(f"No organisation with id {organisation_id}")
    values = clean_profile(raw)
    changed = [field for field in EDITABLE_FIELDS if values[field] != existing[field]]
    if not changed:
        return []
    sources = dict(existing["field_sources"])
    for field in changed:
        if _has_value(values[field]):
            sources[field] = "user"
        else:
            sources.pop(field, None)
    repository.update_organisation(
        organisation_id, Organisation(slug=existing["slug"], field_sources=sources, **values)
    )
    return changed


def missing_facts(organisation):
    """Labels of key facts that are not provided (unknown, which is different from ineligible)."""
    return [FIELD_LABELS[field] for field in KEY_FACTS if not _has_value(organisation.get(field))]


def source_note(organisation, field):
    source = organisation["field_sources"].get(field)
    if source is None:
        return None
    return "Entered by you" if source == "user" else f"Taken from evidence record {source}"
