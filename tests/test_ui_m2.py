"""Walkthroughs of the M2 screens with Streamlit's AppTest; fictional data, no network, no API key."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from grant_platform import db, seed
from grant_platform.repository import Repository

SCRIPT = "from grant_platform.ui import app; app.main()"


def screen_text(app_test):
    parts = [element.value for group in (app_test.markdown, app_test.caption, app_test.title, app_test.subheader, app_test.info, app_test.success) for element in group]
    return "\n".join(parts)


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "test.sqlite3"
        patcher = patch.dict(os.environ, {"GRANT_PLATFORM_DB": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def seeded(self):
        connection = db.connect(self.path)
        seed.seed_brightpath(Repository(connection))
        connection.close()

    def start(self):
        return AppTest.from_string(SCRIPT, default_timeout=30).run()

    def database(self):
        connection = db.connect(self.path)
        self.addCleanup(connection.close)
        return Repository(connection)

    def test_first_run_goes_straight_to_onboarding_and_a_new_organisation_can_be_created_by_hand(self):
        app_test = self.start()
        self.assertFalse(app_test.exception)
        self.assertIn("Tell us about your organisation", screen_text(app_test))
        app_test.text_input(key="onboard_name").set_value("Riverside Test Charity (fictional)")
        app_test.text_area(key="onboard_mission").set_value("To test the onboarding questionnaire.")
        app_test.text_input(key="onboard_country").set_value("United Kingdom")
        app_test.text_area(key="onboard_regions").set_value("Riverside\nNorthbridge")
        app_test.selectbox(key="onboard_income").select("£10,000 to £100,000")
        app_test.button(key="onboard_submit").click().run()
        self.assertFalse(app_test.exception)
        self.assertIn("Riverside Test Charity (fictional)", [title.value for title in app_test.title])
        saved = self.database().list_organisations()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["regions"], ["Riverside", "Northbridge"])
        self.assertEqual(saved[0]["annual_income_band"], "£10,000 to £100,000")
        self.assertIn("Profile created", screen_text(app_test))
        self.assertIn("Legal form", screen_text(app_test))

    def test_onboarding_shows_every_error_and_creates_nothing(self):
        app_test = self.start()
        app_test.text_input(key="onboard_year").set_value("soon")
        app_test.button(key="onboard_submit").click().run()
        errors = " ".join(error.value for error in app_test.error)
        for expected in ("Organisation name", "Mission", "Year founded"):
            self.assertIn(expected, errors)
        self.assertEqual(self.database().list_organisations(), [])

    def test_bright_path_profile_shows_seeded_facts_and_not_provided_for_missing_ones(self):
        self.seeded()
        app_test = self.start()
        self.assertFalse(app_test.exception)
        text = screen_text(app_test)
        self.assertIn("Registration number", text)
        self.assertIn("Not provided", text)
        self.assertIn("Facts we do not have yet", text)
        self.assertIn("Legal form, Registration number, Annual income", text)
        self.assertIn("Taken from evidence record org_mission", text)
        self.assertIn("help young people facing barriers", text)

    def test_profile_edit_persists_across_restarts(self):
        self.seeded()
        app_test = self.start()
        organisation_id = app_test.session_state["organisation"]
        app_test.text_input(key=f"profile_{organisation_id}_number").set_value("1234567")
        app_test.selectbox(key=f"profile_{organisation_id}_legal_form").select("Charitable incorporated organisation (CIO)")
        app_test.button(key=f"profile_{organisation_id}_submit").click().run()
        self.assertFalse(app_test.exception)
        self.assertIn("Profile saved", screen_text(app_test))
        restarted = self.start()
        text = screen_text(restarted)
        self.assertIn("1234567", text)
        self.assertIn("Facts we do not have yet", text)
        self.assertNotIn("Legal form, Registration number", text)
        self.assertEqual(self.database().get_organisation(organisation_id)["legal_form"], "Charitable incorporated organisation (CIO)")

    def test_funding_needs_list_add_edit_and_validation(self):
        self.seeded()
        app_test = self.start()
        organisation_id = app_test.session_state["organisation"]
        app_test.sidebar.radio(key="page").set_value("Funding needs").run()
        self.assertFalse(app_test.exception)
        titles = [subheader.value for subheader in app_test.subheader]
        for title in ("Digital Futures expansion", "BrightMentor volunteer mentoring", "Core operating costs"):
            self.assertIn(title, titles)

        prefix = f"newneed_{organisation_id}_3"
        app_test.text_input(key=f"{prefix}_title").set_value("core operating costs")
        app_test.text_area(key=f"{prefix}_description").set_value("Duplicate title test.")
        app_test.button(key=f"{prefix}_submit").click().run()
        self.assertTrue(any("already have a funding need" in error.value for error in app_test.error))
        self.assertEqual(len(self.database().list_funding_needs(organisation_id)), 3)

        app_test.text_input(key=f"{prefix}_title").set_value("Youth hub roof repair")
        app_test.text_input(key=f"{prefix}_amount").set_value("£40,000")
        app_test.button(key=f"{prefix}_submit").click().run()
        self.assertFalse(app_test.exception)
        self.assertIn("Added Youth hub roof repair", screen_text(app_test))
        needs = self.database().list_funding_needs(organisation_id)
        self.assertEqual([need["amount_sought"] for need in needs], [None, None, None, 40000])

        new_id = needs[-1]["id"]
        app_test.text_input(key=f"need_{organisation_id}_{new_id}_amount").set_value("55000")
        app_test.button(key=f"need_{organisation_id}_{new_id}_submit").click().run()
        self.assertEqual(self.database().get_funding_need(organisation_id, new_id)["amount_sought"], 55000)
        self.assertIn("£55,000", screen_text(self.start().sidebar.radio(key="page").set_value("Funding needs").run()))

    def test_switching_organisation_shows_only_its_own_needs(self):
        self.seeded()
        app_test = self.start()
        app_test.sidebar.radio(key="page").set_value("New organisation").run()
        app_test.text_input(key="onboard_name").set_value("Riverside Test Charity (fictional)")
        app_test.text_area(key="onboard_mission").set_value("Testing.")
        app_test.button(key="onboard_submit").click().run()
        app_test.sidebar.radio(key="page").set_value("Funding needs").run()
        self.assertIn("You have no funding needs yet", screen_text(app_test))
        self.assertNotIn("Digital Futures expansion", [subheader.value for subheader in app_test.subheader])

    def test_user_text_is_not_rendered_as_markup(self):
        app_test = self.start()
        app_test.text_input(key="onboard_name").set_value("Test **bold** <b>html</b>")
        app_test.text_area(key="onboard_mission").set_value("[link](http://example.invalid) _x_")
        app_test.button(key="onboard_submit").click().run()
        text = screen_text(app_test)
        self.assertIn(r"\[link\]\(http://example\.invalid\) \_x\_", text)


if __name__ == "__main__":
    unittest.main()
