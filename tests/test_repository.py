"""Organisation isolation and the approval/external-use rule, using fictional fixtures."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import json
import unittest
from pathlib import Path

from grant_platform import db, seed
from grant_platform.models import EvidenceRecord, FundingNeed, Organisation
from grant_platform.repository import Repository


FIXTURES = Path(__file__).resolve().parent / "fixtures"


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repository = Repository(self.connection)
        self.brightpath, _ = seed.seed_brightpath(self.repository)
        self.other = self.repository.create_organisation(
            Organisation(slug="riverside-test-charity", name="Riverside Test Charity (fictional)")
        )
        self.repository.add_evidence(self.other, EvidenceRecord(
            record_key="org_mission", text="Riverside's fictional mission.",
            knowledge_type="organisation_fact", external_use="safe_to_publish",
            approval_status="approved", origin="manual",
        ))
        self.repository.add_funding_need(self.other, FundingNeed(
            title="Riverside core costs", kind="core", description="Fictional need.",
        ))

    def test_evidence_never_crosses_organisations(self):
        other_records = self.repository.list_evidence(self.other)
        self.assertEqual([record["record_key"] for record in other_records], ["org_mission"])
        self.assertEqual(len(self.repository.list_evidence(self.brightpath)), 42)
        self.assertIsNone(self.repository.get_evidence(self.other, "impact_2024_participants"))
        self.assertNotEqual(
            self.repository.get_evidence(self.other, "org_mission")["text"],
            self.repository.get_evidence(self.brightpath, "org_mission")["text"],
        )

    def test_funding_needs_never_cross_organisations(self):
        brightpath_need = self.repository.list_funding_needs(self.brightpath)[0]
        self.assertIsNone(self.repository.get_funding_need(self.other, brightpath_need["id"]))
        self.assertEqual(
            [need["title"] for need in self.repository.list_funding_needs(self.other)],
            ["Riverside core costs"],
        )

    def test_same_record_key_allowed_in_different_organisations_but_not_twice_in_one(self):
        duplicate = EvidenceRecord(
            record_key="org_mission", text="Changed text.", knowledge_type="organisation_fact",
            external_use="safe_to_publish", approval_status="approved", origin="manual",
        )
        self.assertFalse(self.repository.add_evidence(self.other, duplicate))
        self.assertEqual(self.repository.get_evidence(self.other, "org_mission")["text"], "Riverside's fictional mission.")

    def test_only_approved_publishable_records_are_usable_externally(self):
        for item in json.loads((FIXTURES / "extra_evidence.json").read_text(encoding="utf-8")):
            self.assertTrue(self.repository.add_evidence(self.brightpath, EvidenceRecord(**item)))
        usable = {record["record_key"] for record in self.repository.list_evidence(self.brightpath, usable_externally=True)}
        self.assertNotIn("fixture_draft_impact_2025", usable)
        self.assertNotIn("fixture_internal_board_note", usable)
        # Outdated but approved evidence stays usable; its reporting period travels with it.
        self.assertIn("fixture_outdated_participants_2019", usable)
        self.assertEqual(self.repository.get_evidence(self.brightpath, "fixture_outdated_participants_2019")["reporting_period"], "2019")
        self.assertEqual(len(usable), 43)

    def test_deleting_an_organisation_removes_its_rows(self):
        with self.connection:
            self.connection.execute("DELETE FROM organisation WHERE id = ?", (self.other,))
        self.assertEqual(self.repository.list_evidence(self.other), [])
        self.assertEqual(self.repository.list_funding_needs(self.other), [])
        self.assertEqual(len(self.repository.list_evidence(self.brightpath)), 42)


if __name__ == "__main__":
    unittest.main()
