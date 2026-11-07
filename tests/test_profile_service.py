"""Profile rules: validation, provenance, completeness and isolation (fictional data only)."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import unittest
from datetime import date

from grant_platform import db, seed
from grant_platform.models import Organisation
from grant_platform.repository import Repository
from grant_platform.services import ValidationError, profile

MINIMUM = {"name": "Riverside Test Charity (fictional)", "mission": "To test onboarding."}


class ProfileServiceTests(unittest.TestCase):
    def setUp(self):
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repository = Repository(self.connection)

    def assertInvalid(self, raw, *fields):
        with self.assertRaises(ValidationError) as caught:
            profile.clean_profile(raw)
        self.assertEqual(sorted(caught.exception.errors), sorted(fields))

    def test_only_name_and_mission_are_required(self):
        cleaned = profile.clean_profile(MINIMUM)
        self.assertEqual(cleaned["regions"], [])
        self.assertIsNone(cleaned["registration_number"])
        self.assertInvalid({}, "name", "mission")
        self.assertInvalid({"name": "  ", "mission": "x"}, "name")

    def test_every_problem_is_reported_at_once(self):
        self.assertInvalid(
            {**MINIMUM, "founded_year": "soon", "registration_number": "!!", "legal_form": "Made up", "annual_income_band": "lots"},
            "founded_year", "registration_number", "legal_form", "annual_income_band",
        )

    def test_year_range_and_text_limits(self):
        self.assertInvalid({**MINIMUM, "founded_year": "1700"}, "founded_year")
        self.assertInvalid({**MINIMUM, "founded_year": str(date.today().year + 1)}, "founded_year")
        self.assertEqual(profile.clean_profile({**MINIMUM, "founded_year": " 2018 "})["founded_year"], 2018)
        self.assertInvalid({**MINIMUM, "mission": "x" * (profile.LONG_LIMIT + 1)}, "mission")
        self.assertInvalid({**MINIMUM, "name": "x" * (profile.SHORT_LIMIT + 1)}, "name")

    def test_regions_are_split_trimmed_and_deduplicated(self):
        cleaned = profile.clean_profile({**MINIMUM, "regions": "Northbridge,\n eastmere ;  Northbridge\n\n"})
        self.assertEqual(cleaned["regions"], ["Northbridge", "eastmere"])
        self.assertInvalid({**MINIMUM, "regions": [f"place {n}" for n in range(profile.MAX_REGIONS + 1)]}, "regions")

    def test_create_marks_answers_as_entered_by_the_user(self):
        organisation_id = profile.create_organisation(self.repository, {**MINIMUM, "registration_country": "United Kingdom", "regions": "Riverside"})
        saved = self.repository.get_organisation(organisation_id)
        self.assertEqual(saved["field_sources"], {"name": "user", "mission": "user", "registration_country": "user", "regions": "user"})
        self.assertEqual(saved["slug"], "riverside-test-charity-fictional")
        self.assertEqual(profile.source_note(saved, "mission"), "Entered by you")
        self.assertIsNone(profile.source_note(saved, "legal_form"))

    def test_slugs_stay_unique_for_same_name(self):
        first = profile.create_organisation(self.repository, MINIMUM)
        second = profile.create_organisation(self.repository, MINIMUM)
        self.assertNotEqual(self.repository.get_organisation(first)["slug"], self.repository.get_organisation(second)["slug"])
        self.assertEqual(profile.slugify("!!!"), "organisation")

    def test_failed_validation_creates_nothing(self):
        with self.assertRaises(ValidationError):
            profile.create_organisation(self.repository, {"name": "No mission"})
        self.assertEqual(self.repository.list_organisations(), [])

    def test_bright_path_shows_missing_key_facts_and_seeded_sources(self):
        organisation_id, _ = seed.seed_brightpath(self.repository)
        organisation = self.repository.get_organisation(organisation_id)
        missing = profile.missing_facts(organisation)
        self.assertEqual(missing, ["Legal form", "Registration number", "Annual income"])
        self.assertEqual(profile.source_note(organisation, "mission"), "Taken from evidence record org_mission")

    def test_edit_updates_changed_fields_and_their_sources_only(self):
        organisation_id, _ = seed.seed_brightpath(self.repository)
        raw = {field: self.repository.get_organisation(organisation_id)[field] for field in profile.EDITABLE_FIELDS}
        self.assertEqual(profile.update_profile(self.repository, organisation_id, raw), [])
        raw.update(registration_number="1234567", mission="A reworded fictional mission.")
        self.assertEqual(profile.update_profile(self.repository, organisation_id, raw), ["registration_number", "mission"])
        saved = self.repository.get_organisation(organisation_id)
        self.assertEqual(saved["registration_number"], "1234567")
        self.assertEqual(saved["field_sources"]["mission"], "user")
        self.assertEqual(saved["field_sources"]["registration_number"], "user")
        self.assertEqual(saved["field_sources"]["activities"], "org_approach")
        self.assertNotIn("Registration number", profile.missing_facts(saved))

    def test_clearing_a_field_removes_its_source_and_marks_it_missing(self):
        organisation_id, _ = seed.seed_brightpath(self.repository)
        raw = {field: self.repository.get_organisation(organisation_id)[field] for field in profile.EDITABLE_FIELDS}
        raw["founded_year"] = ""
        profile.update_profile(self.repository, organisation_id, raw)
        saved = self.repository.get_organisation(organisation_id)
        self.assertIsNone(saved["founded_year"])
        self.assertNotIn("founded_year", saved["field_sources"])
        self.assertIn("Year founded", profile.missing_facts(saved))

    def test_failed_edit_changes_nothing(self):
        organisation_id = profile.create_organisation(self.repository, MINIMUM)
        before = self.repository.get_organisation(organisation_id)
        with self.assertRaises(ValidationError):
            profile.update_profile(self.repository, organisation_id, {**MINIMUM, "mission": ""})
        self.assertEqual(self.repository.get_organisation(organisation_id), before)

    def test_editing_one_organisation_never_touches_another(self):
        brightpath, _ = seed.seed_brightpath(self.repository)
        other = profile.create_organisation(self.repository, MINIMUM)
        before = self.repository.get_organisation(brightpath)
        profile.update_profile(self.repository, other, {**MINIMUM, "mission": "Changed."})
        self.assertEqual(self.repository.get_organisation(brightpath), before)
        with self.assertRaises(LookupError):
            profile.update_profile(self.repository, 9999, MINIMUM)

    def test_slug_cannot_be_changed_by_an_edit(self):
        organisation_id = profile.create_organisation(self.repository, MINIMUM)
        slug = self.repository.get_organisation(organisation_id)["slug"]
        profile.update_profile(self.repository, organisation_id, {**MINIMUM, "name": "Renamed Fictional Charity"})
        self.assertEqual(self.repository.get_organisation(organisation_id)["slug"], slug)


if __name__ == "__main__":
    unittest.main()
