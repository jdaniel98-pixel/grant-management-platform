"""Load the fictional Bright Path Youth Foundation into a database.

The sample file is parsed with ast.literal_eval and never imported or executed.
Run: PYTHONPATH=src python -m grant_platform.seed [--db instance/grant_platform.sqlite3]
"""
import argparse
import ast
import json
from pathlib import Path

from grant_platform import db
from grant_platform.models import EvidenceRecord, FundingNeed, Organisation
from grant_platform.repository import Repository
from grant_platform.services import grants


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRIGHTPATH_PATH = PROJECT_ROOT / "sample_data" / "brightpath_youth_foundation" / "brightpath_dummy_knowledge.py"
SCENARIO_GRANTS_PATH = PROJECT_ROOT / "sample_data" / "scenario_grants" / "scenario_grants.json"
DEFAULT_DB_PATH = db.DEFAULT_DB_PATH
EXPECTED_RECORDS = 42
SLUG = "brightpath-youth-foundation"
_STORED_METADATA = {"knowledge_type", "topic", "source", "external_use", "approval_status", "date_range"}

# Profile values copied from the named records. Facts the records do not state
# (registration number, legal form, income band) stay empty so they show as missing.
PROFILE_FROM_RECORDS = {
    "name": ("org_identity", "BrightPath Youth Foundation"),
    "organisation_type": ("org_identity", "Charity"),
    "registration_country": ("org_identity", "United Kingdom"),
    "founded_year": ("org_history", 2018),
    "regions": ("geo_northbridge", ["Northbridge (fictional city)", "Eastmere (fictional district)"]),
}
PROFILE_TEXT_FROM_RECORDS = {
    "mission": "org_mission",
    "activities": "org_approach",
    "beneficiaries": "beneficiary_primary",
}

FUNDING_NEEDS = (
    FundingNeed(
        title="Digital Futures expansion",
        kind="programme",
        description="Grow the Digital Futures programme, which introduces young people to practical digital skills including spreadsheets, collaborative tools, basic data handling, digital communication and introductory no-code automation.",
        geography="Northbridge (fictional city)",
        beneficiaries="Young people aged 16–25 facing barriers to employment",
    ),
    FundingNeed(
        title="BrightMentor volunteer mentoring",
        kind="programme",
        description="Sustain the BrightMentor programme, which matches participants with trained volunteer mentors for up to six months, focusing on confidence, goal setting, accountability, professional communication and education or employment decisions.",
        geography="Northbridge (fictional city)",
        beneficiaries="Young people aged 16–25 facing barriers to employment",
    ),
    FundingNeed(
        title="Core operating costs",
        kind="core",
        description="Unrestricted funding towards the organisation's core running costs across its employability, mentoring, digital-skills and enterprise work.",
    ),
)


def load_brightpath_records(path=BRIGHTPATH_PATH):
    """Return the literal `dummy_records` list without running the file."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "dummy_records" for target in node.targets
        ):
            records = ast.literal_eval(node.value)
            break
    else:
        raise ValueError("No dummy_records assignment found in the Bright Path sample file.")
    if len(records) != EXPECTED_RECORDS:
        raise ValueError(f"Expected {EXPECTED_RECORDS} Bright Path records; found {len(records)}.")
    return records


def to_evidence(record):
    metadata = record["metadata"]
    return EvidenceRecord(
        record_key=record["id"],
        text=record["text"],
        knowledge_type=metadata["knowledge_type"],
        topic=metadata.get("topic"),
        source=metadata.get("source"),
        external_use=metadata["external_use"],
        approval_status=metadata["approval_status"],
        reporting_period=metadata.get("date_range"),
        origin="seed",
        extra={key: value for key, value in metadata.items() if key not in _STORED_METADATA},
    )


def build_profile(records):
    by_key = {record["id"]: record for record in records}
    values, sources = {"slug": SLUG}, {}
    for field, (record_key, value) in PROFILE_FROM_RECORDS.items():
        if record_key not in by_key:
            raise ValueError(f"Profile source record {record_key!r} is missing.")
        values[field] = value
        sources[field] = record_key
    for field, record_key in PROFILE_TEXT_FROM_RECORDS.items():
        values[field] = by_key[record_key]["text"]
        sources[field] = record_key
    return Organisation(**values, field_sources=sources)


def seed_brightpath(repository, path=BRIGHTPATH_PATH):
    """Create Bright Path once; re-running adds nothing. Returns (organisation_id, counts added)."""
    records = load_brightpath_records(path)
    organisation_id = repository.find_organisation_id(SLUG)
    added = {"organisation": 0, "evidence": 0, "funding_needs": 0}
    if organisation_id is None:
        organisation_id = repository.create_organisation(build_profile(records))
        added["organisation"] = 1
    for record in records:
        added["evidence"] += repository.add_evidence(organisation_id, to_evidence(record))
    for need in FUNDING_NEEDS:
        added["funding_needs"] += repository.add_funding_need(organisation_id, need)
    return organisation_id, added


def load_scenario_grants(path=SCENARIO_GRANTS_PATH):
    """The eight fictional scenario grants (plan section 7.2). Plain JSON; scenario 8 duplicates scenario 1."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def seed_scenario_grants(repository, path=SCENARIO_GRANTS_PATH):
    """Store the scenario grants through the normal grant rules, so duplicates are refused as they would be
    for a user. Returns {scenario number: grant id} for stored grants and {scenario number: id it duplicates}
    for refused ones. Re-running refuses every grant."""
    stored, refused = {}, {}
    for entry in load_scenario_grants(path):
        try:
            stored[entry["scenario"]] = grants.create_grant(repository, entry, origin=entry["origin"])["id"]
        except grants.DuplicateGrantError as error:
            refused[entry["scenario"]] = error.existing["id"]
    return stored, refused


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite database path")
    parser.add_argument("--scenario-grants", action="store_true", help="also load the fictional scenario grants")
    args = parser.parse_args()
    connection = db.connect(args.db)
    try:
        repository = Repository(connection)
        organisation_id, added = seed_brightpath(repository)
        if args.scenario_grants:
            stored, refused = seed_scenario_grants(repository)
            added["scenario_grants"] = len(stored)
    finally:
        connection.close()
    print(f"Bright Path organisation id {organisation_id}; added {added} to {args.db}")


if __name__ == "__main__":
    main()
