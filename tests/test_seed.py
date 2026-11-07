"""Bright Path seeding: counts, idempotency, profile sources, untouched sample file."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

from grant_platform import db, seed
from grant_platform.repository import Repository


def checksum(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repository = Repository(self.connection)

    def test_fresh_seed_creates_one_organisation_42_records_3_needs(self):
        organisation_id, added = seed.seed_brightpath(self.repository)
        self.assertEqual(added, {"organisation": 1, "evidence": 42, "funding_needs": 3})
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM organisation").fetchone()[0], 1)
        self.assertEqual(len(self.repository.list_evidence(organisation_id)), 42)
        self.assertEqual(
            [need["title"] for need in self.repository.list_funding_needs(organisation_id)],
            ["Digital Futures expansion", "BrightMentor volunteer mentoring", "Core operating costs"],
        )

    def test_second_seed_changes_nothing(self):
        organisation_id, _ = seed.seed_brightpath(self.repository)
        before = self.connection.execute("SELECT * FROM evidence_record ORDER BY id").fetchall()
        again_id, added = seed.seed_brightpath(self.repository)
        self.assertEqual(again_id, organisation_id)
        self.assertEqual(added, {"organisation": 0, "evidence": 0, "funding_needs": 0})
        after = self.connection.execute("SELECT * FROM evidence_record ORDER BY id").fetchall()
        self.assertEqual([tuple(row) for row in before], [tuple(row) for row in after])

    def test_seed_persists_in_a_database_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "platform.sqlite3"
            first = db.connect(path)
            seed.seed_brightpath(Repository(first))
            first.close()
            second = db.connect(path)
            self.addCleanup(second.close)
            _, added = seed.seed_brightpath(Repository(second))
            self.assertEqual(added["evidence"], 0)
            self.assertEqual(second.execute("PRAGMA user_version").fetchone()[0], len(db.MIGRATIONS))

    def test_sample_file_is_unchanged_and_never_imported(self):
        before = checksum(seed.BRIGHTPATH_PATH)
        seed.seed_brightpath(self.repository)
        self.assertEqual(checksum(seed.BRIGHTPATH_PATH), before)
        self.assertNotIn("brightpath_dummy_knowledge", sys.modules)

    def test_metadata_is_kept_with_each_record(self):
        organisation_id, _ = seed.seed_brightpath(self.repository)
        record = self.repository.get_evidence(organisation_id, "impact_2024_participants")
        self.assertEqual(record["knowledge_type"], "impact")
        self.assertEqual(record["approval_status"], "approved")
        self.assertEqual(record["external_use"], "safe_to_publish")
        self.assertEqual(record["reporting_period"], "2024")
        self.assertEqual(record["origin"], "seed")
        limit = self.repository.get_evidence(organisation_id, "knowledge_limit_2025")
        self.assertEqual(limit["knowledge_type"], "knowledge_limit")
        self.assertEqual(limit["extra"]["record_type"], "known_information_gap")

    def test_profile_values_trace_to_records_and_unknowns_stay_empty(self):
        organisation_id, _ = seed.seed_brightpath(self.repository)
        profile = self.repository.get_organisation(organisation_id)
        self.assertEqual(profile["name"], "BrightPath Youth Foundation")
        self.assertEqual(profile["founded_year"], 2018)
        mission = self.repository.get_evidence(organisation_id, "org_mission")["text"]
        self.assertEqual(profile["mission"], mission)
        for field, record_key in profile["field_sources"].items():
            self.assertIsNotNone(self.repository.get_evidence(organisation_id, record_key), field)
        for unknown in ("registration_number", "legal_form", "annual_income_band"):
            self.assertIsNone(profile[unknown])

    def test_wrong_record_count_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "short.py"
            path.write_text("dummy_records = [{'id': 'only_one'}]\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                seed.load_brightpath_records(path)
            self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM organisation").fetchone()[0], 0)

    def test_unknown_funding_need_kind_is_rejected_by_database(self):
        organisation_id, _ = seed.seed_brightpath(self.repository)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.connection:
                self.connection.execute(
                    "INSERT INTO funding_need (organisation_id, title, kind, description) VALUES (?, 'x', 'other', 'x')",
                    (organisation_id,),
                )


if __name__ == "__main__":
    unittest.main()
