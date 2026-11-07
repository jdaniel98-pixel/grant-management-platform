"""Change detection for monitored grants: what counts as a real change, and what only looks different.

A grant's details are *material* when the funder's words or numbers changed. Spacing, line breaks, list markers,
curly versus straight quotes and the different dash characters are formatting: they never reset verification, never
count as a change and never cause a grant to be re-assessed.
"""
import hashlib
import json
import re

# The details a person checks when verifying a grant against the funder's page.
MATERIAL_FIELDS = ("title", "funder", "purpose", "eligibility", "scope", "funding", "duration", "deadline_text",
                   "deadline_date", "key_requirements", "exclusions")
FIELD_LABELS = {
    "title": "Title", "funder": "Funder", "purpose": "Purpose", "eligibility": "Who can apply", "scope": "Where it applies",
    "funding": "Funding", "duration": "Duration", "deadline_text": "Deadline wording", "deadline_date": "Deadline",
    "key_requirements": "Key requirements", "exclusions": "Exclusions", "status": "Status",
}
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", "‑": "-", "−": "-", "\xa0": " "})
_LIST_MARKER = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)", re.MULTILINE)


def normalise(value):
    """The comparison form of a text: no formatting differences, case-insensitive."""
    if value is None:
        return ""
    text = _LIST_MARKER.sub("", str(value).translate(_QUOTES))
    return " ".join(text.split()).casefold()


def differences(stored, incoming):
    """Fields whose stored value differs from the incoming one, as [{field, old, new, material}] in a stable order.
    material is False when the texts differ only in formatting."""
    found = []
    for field in (*MATERIAL_FIELDS, "status"):
        old, new = stored.get(field), incoming.get(field)
        if (old or None) == (new or None):
            continue
        material = field == "status" or normalise(old) != normalise(new)
        found.append({"field": field, "old": old, "new": new, "material": material})
    return found


def content_hash(fields):
    """A hash of the normalised material details, so an unchanged page is recognised without comparing every field."""
    canonical = {name: normalise(fields.get(name)) for name in MATERIAL_FIELDS}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def details_changed(changes):
    """True when any detail a person verifies has really changed (status alone does not count)."""
    return any(change["material"] and change["field"] != "status" for change in changes)
