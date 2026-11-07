"""Walkthroughs of the Grants screens with Streamlit's AppTest; fictional data, no network, no API key."""
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
    groups = (app_test.markdown, app_test.caption, app_test.title, app_test.subheader, app_test.info, app_test.success, app_test.warning, app_test.error)
    return "\n".join(element.value for group in groups for element in group)


class GrantScreenTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "test.sqlite3"
        patcher = patch.dict(os.environ, {"GRANT_PLATFORM_DB": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        connection = db.connect(self.path)
        repository = Repository(connection)
        seed.seed_brightpath(repository)
        self.scenarios, _ = seed.seed_scenario_grants(repository)
        connection.close()

    def database(self):
        connection = db.connect(self.path)
        self.addCleanup(connection.close)
        return Repository(connection)

    def grants_page(self):
        app_test = AppTest.from_string(SCRIPT, default_timeout=30).run()
        app_test.sidebar.radio(key="page").set_value("Grants").run()
        self.assertFalse(app_test.exception)
        return app_test

    def test_list_shows_provenance_verification_and_dates_for_every_grant(self):
        app_test = self.grants_page()
        titles = [subheader.value for subheader in app_test.subheader]
        self.assertEqual(sum(1 for title in titles if title != "Add a grant"), 7)
        text = screen_text(app_test)
        self.assertIn("https://example.org/funds/youth-employment-fund", [element.value for element in app_test.text])
        self.assertIn("Added by web address", text)
        self.assertIn("Entered by hand", text)
        self.assertIn("Unverified", text)
        self.assertIn("First saved", text)
        self.assertIn("Deadline: 31 March 2027 (", text)
        for grant_id in self.scenarios.values():
            self.assertIsNotNone(app_test.button(key=f"verify_{grant_id}"))

    def test_development_only_notice_is_shown_above_the_verify_buttons(self):
        warning = " ".join(w.value for w in self.grants_page().warning)
        self.assertIn("Development only", warning)
        self.assertIn("not secure administrator access", warning)

    def test_mark_verified_persists_and_shows_who_and_when(self):
        app_test = self.grants_page()
        grant_id = self.scenarios[1]
        app_test.button(key=f"verify_{grant_id}").click().run()
        self.assertFalse(app_test.exception)
        saved = self.database().get_grant(grant_id)
        self.assertEqual(saved["verification_status"], "verified")
        text = screen_text(self.grants_page())
        self.assertIn("Verified 20", text)
        self.assertIn("Development\\-only verification", text)
        with self.assertRaises(KeyError):
            self.grants_page().button(key=f"verify_{grant_id}")

    def test_add_by_hand_without_an_address(self):
        app_test = self.grants_page()
        prefix = "newgrant_7"
        app_test.text_input(key=f"{prefix}_title").set_value("Hub Roof Fund")
        app_test.text_input(key=f"{prefix}_funder").set_value("Example Trust (fictional)")
        app_test.text_input(key=f"{prefix}_deadline_date").set_value("30/11/2027")
        app_test.button(key=f"{prefix}_submit").click().run()
        self.assertFalse(app_test.exception)
        self.assertIn("Added Hub Roof Fund", screen_text(app_test))
        saved = [g for g in self.database().list_grants() if g["title"] == "Hub Roof Fund"][0]
        self.assertEqual((saved["origin"], saved["verification_status"], saved["deadline_date"]), ("manual", "unverified", "2027-11-30"))
        self.assertIn("No web address recorded", screen_text(self.grants_page()))

    def test_add_by_address_blocks_the_scenario_eight_duplicate_and_points_to_the_existing_grant(self):
        app_test = self.grants_page()
        prefix = "urlgrant_7"
        app_test.text_input(key=f"{prefix}_title").set_value("Something else entirely")
        app_test.text_input(key=f"{prefix}_funder").set_value("Another funder")
        app_test.text_input(key=f"{prefix}_url").set_value("http://example.org/funds/youth-employment-fund/?utm_source=newsletter#apply")
        app_test.button(key=f"{prefix}_submit").click().run()
        self.assertFalse(app_test.exception)
        errors = " ".join(error.value for error in app_test.error)
        self.assertIn("already saved", errors)
        self.assertIn(f"#{self.scenarios[1]}", errors)
        self.assertIn("Youth Employment Fund", errors)
        self.assertEqual(len(self.database().list_grants()), 7)

    def test_add_by_address_requires_the_address_and_shows_every_error(self):
        app_test = self.grants_page()
        prefix = "urlgrant_7"
        app_test.text_input(key=f"{prefix}_deadline_date").set_value("soon")
        app_test.button(key=f"{prefix}_submit").click().run()
        errors = " ".join(error.value for error in app_test.error)
        for expected in ("Title", "Funder", "Web address", "Deadline date"):
            self.assertIn(expected, errors)
        self.assertEqual(len(self.database().list_grants()), 7)

    def test_nothing_is_fetched_from_an_address(self):
        refuse = AssertionError("network used")
        with patch("socket.getaddrinfo", side_effect=refuse), patch("socket.create_connection", side_effect=refuse), \
                patch("socket.socket.connect", side_effect=refuse):
            app_test = self.grants_page()
            prefix = "urlgrant_7"
            app_test.text_input(key=f"{prefix}_title").set_value("Offline Fund")
            app_test.text_input(key=f"{prefix}_funder").set_value("Offline Trust (fictional)")
            app_test.text_input(key=f"{prefix}_url").set_value("https://offline.example.org/fund")
            app_test.button(key=f"{prefix}_submit").click().run()
        self.assertFalse(app_test.exception)
        self.assertIn("Added Offline Fund", screen_text(app_test))

    def test_user_text_is_not_rendered_as_markup(self):
        app_test = self.grants_page()
        prefix = "newgrant_7"
        app_test.text_input(key=f"{prefix}_title").set_value("Markup Fund")
        app_test.text_input(key=f"{prefix}_funder").set_value("**Bold** <b>Trust</b>")
        app_test.text_area(key=f"{prefix}_purpose").set_value("[click](http://example.invalid) _x_")
        app_test.button(key=f"{prefix}_submit").click().run()
        text = screen_text(self.grants_page())
        self.assertIn(r"\[click\]\(http://example\.invalid\) \_x\_", text)
        self.assertIn(r"\*\*Bold\*\* \<b\>Trust\<\/b\>".replace("\\/", "/"), text.replace("\\/", "/"))


if __name__ == "__main__":
    unittest.main()
