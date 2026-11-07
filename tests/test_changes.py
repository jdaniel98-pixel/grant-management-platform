"""What counts as a real change to a grant, and what is only formatting."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import unittest

from grant_platform.services import changes


class NormaliseTests(unittest.TestCase):
    def test_formatting_differences_disappear(self):
        base = "Registered charities that work with young people.\nMust be based in England."
        variants = (
            "Registered   charities that work with young people.\n\n\nMust be based in England.",
            "- Registered charities that work with young people.\n- Must be based in England.",
            "REGISTERED CHARITIES that work with young people. Must be based in England.",
            "Registered charities that work with young people. Must be based in England.",
            "• Registered charities that work with young people.\n• Must be based in England.",
        )
        for variant in variants:
            self.assertEqual(changes.normalise(base), changes.normalise(variant), variant)

    def test_curly_quotes_and_dash_types_are_equivalent(self):
        self.assertEqual(changes.normalise("It’s “youth” – not adults"), changes.normalise("It's \"youth\" - not adults"))

    def test_real_wording_and_number_changes_remain(self):
        self.assertNotEqual(changes.normalise("Up to £15,000"), changes.normalise("Up to £10,000"))
        self.assertNotEqual(changes.normalise("Charities in England"), changes.normalise("Charities in England and Wales"))
        self.assertNotEqual(changes.normalise("Not for individuals"), changes.normalise("For individuals"))

    def test_none_and_empty_are_the_same(self):
        self.assertEqual(changes.normalise(None), changes.normalise("   "))


class DifferencesTests(unittest.TestCase):
    STORED = {"title": "Youth Fund", "funder": "Example Trust", "purpose": "Skills.\nFor young people.", "eligibility": "Charities",
              "scope": "England", "funding": "Up to £10,000", "duration": None, "deadline_text": "Closes 31 March 2027",
              "deadline_date": "2027-03-31", "key_requirements": None, "exclusions": None, "status": "open"}

    def test_identical_has_no_differences(self):
        self.assertEqual(changes.differences(self.STORED, dict(self.STORED)), [])

    def test_formatting_only_difference_is_not_material(self):
        found = changes.differences(self.STORED, {**self.STORED, "purpose": "Skills. For young people."})
        self.assertEqual([(c["field"], c["material"]) for c in found], [("purpose", False)])
        self.assertFalse(changes.details_changed(found))

    def test_a_changed_amount_is_material(self):
        found = changes.differences(self.STORED, {**self.STORED, "funding": "Up to £15,000"})
        self.assertEqual([(c["field"], c["material"]) for c in found], [("funding", True)])
        self.assertTrue(changes.details_changed(found))

    def test_a_status_change_alone_is_not_a_change_to_verified_details(self):
        found = changes.differences(self.STORED, {**self.STORED, "status": "closed"})
        self.assertEqual([c["field"] for c in found], ["status"])
        self.assertFalse(changes.details_changed(found))

    def test_a_new_deadline_is_material_and_blank_equals_none(self):
        self.assertTrue(changes.details_changed(changes.differences(self.STORED, {**self.STORED, "deadline_date": "2027-06-30"})))
        self.assertEqual(changes.differences({**self.STORED, "duration": ""}, self.STORED), [])

    def test_hash_ignores_formatting_but_not_content(self):
        self.assertEqual(changes.content_hash(self.STORED), changes.content_hash({**self.STORED, "purpose": "skills. for young  people."}))
        self.assertNotEqual(changes.content_hash(self.STORED), changes.content_hash({**self.STORED, "scope": "Wales"}))
        self.assertEqual(changes.content_hash(self.STORED), changes.content_hash({**self.STORED, "status": "closed"}))


if __name__ == "__main__":
    unittest.main()
