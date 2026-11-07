"""Funding need rules: validation, uniqueness per organisation, isolation (fictional data only)."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import unittest

from grant_platform import db, seed
from grant_platform.repository import Repository
from grant_platform.services import ValidationError, needs, profile

VALID = {"title": "Youth hub roof repair", "kind": "project", "description": "Repair the fictional hub roof."}


class NeedsServiceTests(unittest.TestCase):
    def setUp(self):
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repository = Repository(self.connection)
        self.brightpath, _ = seed.seed_brightpath(self.repository)
        self.other = profile.create_organisation(self.repository, {"name": "Riverside Test Charity (fictional)", "mission": "Testing."})

    def assertInvalid(self, raw, *fields):
        with self.assertRaises(ValidationError) as caught:
            needs.clean_need(raw)
        self.assertEqual(sorted(caught.exception.errors), sorted(fields))

    def test_required_fields_and_kind(self):
        self.assertInvalid({}, "title", "kind", "description")
        self.assertInvalid({**VALID, "kind": "other"}, "kind")
        self.assertInvalid({**VALID, "title": "x" * (needs.TITLE_LIMIT + 1)}, "title")
        self.assertInvalid({**VALID, "description": "x" * (needs.DESCRIPTION_LIMIT + 1)}, "description")

    def test_amount_parsing(self):
        self.assertEqual(needs.clean_need({**VALID, "amount_sought": "£25,000"}).amount_sought, 25000)
        self.assertEqual(needs.clean_need({**VALID, "amount_sought": "  "}).amount_sought, None)
        self.assertIsNone(needs.clean_need(VALID).amount_sought)
        self.assertInvalid({**VALID, "amount_sought": "about 20k"}, "amount_sought")
        self.assertInvalid({**VALID, "amount_sought": "0"}, "amount_sought")
        self.assertInvalid({**VALID, "amount_sought": "1.5"}, "amount_sought")
        self.assertEqual(needs.format_amount(25000), "£25,000")
        self.assertEqual(needs.format_amount(None), "Not provided")

    def test_create_adds_a_need_for_that_organisation_only(self):
        created = needs.create_need(self.repository, self.brightpath, {**VALID, "amount_sought": "40000"})
        self.assertEqual(created["amount_sought"], 40000)
        self.assertEqual(len(self.repository.list_funding_needs(self.brightpath)), 4)
        self.assertEqual(self.repository.list_funding_needs(self.other), [])

    def test_duplicate_titles_rejected_ignoring_case_within_an_organisation_only(self):
        with self.assertRaises(ValidationError) as caught:
            needs.create_need(self.repository, self.brightpath, {**VALID, "title": "core OPERATING costs"})
        self.assertEqual(list(caught.exception.errors), ["title"])
        needs.create_need(self.repository, self.other, {**VALID, "title": "Core operating costs"})

    def test_failed_create_adds_nothing(self):
        with self.assertRaises(ValidationError):
            needs.create_need(self.repository, self.brightpath, {**VALID, "description": ""})
        self.assertEqual(len(self.repository.list_funding_needs(self.brightpath)), 3)

    def test_update_changes_the_need_and_may_keep_its_own_title(self):
        need = self.repository.list_funding_needs(self.brightpath)[0]
        updated = needs.update_need(self.repository, self.brightpath, need["id"], {**VALID, "title": need["title"], "amount_sought": "10000"})
        self.assertEqual(updated["amount_sought"], 10000)
        self.assertEqual(updated["kind"], "project")
        self.assertEqual(updated["id"], need["id"])

    def test_update_cannot_take_another_needs_title(self):
        first, second = self.repository.list_funding_needs(self.brightpath)[:2]
        with self.assertRaises(ValidationError):
            needs.update_need(self.repository, self.brightpath, second["id"], {**VALID, "title": first["title"]})
        self.assertEqual(self.repository.get_funding_need(self.brightpath, second["id"])["title"], second["title"])

    def test_cannot_update_another_organisations_need(self):
        need = self.repository.list_funding_needs(self.brightpath)[0]
        with self.assertRaises(LookupError):
            needs.update_need(self.repository, self.other, need["id"], VALID)
        self.assertEqual(self.repository.get_funding_need(self.brightpath, need["id"])["title"], need["title"])
        with self.assertRaises(LookupError):
            needs.create_need(self.repository, 9999, VALID)


if __name__ == "__main__":
    unittest.main()
