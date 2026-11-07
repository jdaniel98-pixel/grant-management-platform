"""Walkthroughs of the Matches screen with Streamlit's AppTest and canned model answers; fictional data only."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from canned_ai import CORE, CannedGateway, analysis_for
from grant_platform import db, seed
from grant_platform.repository import Repository
from grant_platform.services import ai, matching, profile
from grant_platform.services.matching import GatewayError

SCRIPT = "from grant_platform.ui import app; app.main()"


def screen_text(app_test):
    groups = (app_test.markdown, app_test.caption, app_test.title, app_test.subheader, app_test.info, app_test.success, app_test.warning, app_test.error)
    return "\n".join(element.value for group in groups for element in group)


class MatchScreenTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / "test.sqlite3"
        patcher = patch.dict(os.environ, {"GRANT_PLATFORM_DB": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        connection = db.connect(self.path)
        repository = Repository(connection)
        self.organisation_id, _ = seed.seed_brightpath(repository)
        self.scenarios, _ = seed.seed_scenario_grants(repository)
        self.needs = {need["title"]: need["id"] for need in repository.list_funding_needs(self.organisation_id)}
        connection.close()
        self.gateway = CannedGateway()

    def database(self):
        connection = db.connect(self.path)
        self.addCleanup(connection.close)
        return Repository(connection)

    def page(self, gateway="canned"):
        chosen = self.gateway if gateway == "canned" else gateway
        patcher = patch.object(ai, "default_gateway", return_value=chosen)
        patcher.start()
        self.addCleanup(patcher.stop)
        app_test = AppTest.from_string(SCRIPT, default_timeout=60).run()
        app_test.sidebar.radio(key="page").set_value("Matches").run()
        self.assertFalse(app_test.exception)
        return app_test

    def test_without_ai_configured_the_screen_explains_and_offers_no_assess_buttons(self):
        app_test = self.page(gateway=None)
        text = screen_text(app_test)
        self.assertIn("AI assessment is not set up on this computer", text)
        self.assertIn("OPENAI_API_KEY", text)  # the setting's name, never a value
        self.assertEqual(len(app_test.button), 0)
        self.assertIn("Not assessed yet", text)

    def test_saved_results_still_show_without_ai(self):
        matching.assess(self.database(), self.organisation_id, self.scenarios[1], None, gateway=self.gateway)
        text = screen_text(self.page(gateway=None))
        self.assertIn("Eligible on available information", text)
        self.assertIn("Youth Employment Fund", text)

    def test_assess_all_shows_every_scenario_with_eligibility_apart_from_suitability(self):
        app_test = self.page()
        app_test.button(key="assess_all").click().run()
        self.assertFalse(app_test.exception)
        text = screen_text(app_test)
        self.assertIn("Assessed 7 grants", text)
        for label in ("Eligible on available information", "Eligibility unclear", "Ineligible (confirmed)"):
            self.assertIn(label, text)
        self.assertIn("Based on unverified grant information", text)
        self.assertIn("Suitability is shown for information only", text)
        self.assertIn("Poor fit, 7 out of 25", text)
        self.assertIn("Suggested next step:** Pursue", text)
        titles = [subheader.value for subheader in app_test.subheader]
        self.assertEqual(titles[0], "Digital Skills for Young People")  # eligible, and the highest suitability (23)
        self.assertEqual(titles[-1], "American Youth Initiative")  # confirmed ineligible last
        self.assertEqual(len(self.database().list_matches(self.organisation_id)), 7)

    def test_reopening_the_screen_does_not_call_the_model_again(self):
        app_test = self.page()
        app_test.button(key="assess_all").click().run()
        calls = len(self.gateway.calls)
        app_test.button(key="assess_all").click().run()
        self.assertEqual(len(self.gateway.calls), calls)

    def test_need_specific_assessment_shows_the_core_cost_conflict(self):
        app_test = self.page()
        app_test.selectbox(key="match_scope").select(self.needs[CORE]).run()
        app_test.button(key=f"assess_{self.needs[CORE]}_{self.scenarios[6]}").click().run()
        self.assertFalse(app_test.exception)
        text = screen_text(app_test)
        self.assertIn("Conflict with the grant's exclusions", text)
        self.assertIn("Poor fit", text)
        self.assertIn("Suggested next step:** Reject", text)
        self.assertEqual(self.database().list_matches(self.organisation_id, None), [])

    def test_a_failing_grant_is_named_and_the_others_are_kept(self):
        def behaviour(payload):
            if payload["grant"]["title"] == "Small Charities Boost":
                raise GatewayError("APIConnectionError")
            return analysis_for(payload)
        self.gateway = CannedGateway(behaviour)
        app_test = self.page()
        app_test.button(key="assess_all").click().run()
        errors = " ".join(error.value for error in app_test.error)
        self.assertIn("Small Charities Boost", errors)
        self.assertIn("Nothing was saved", errors)
        self.assertEqual(len(self.database().list_matches(self.organisation_id)), 6)
        self.assertIn("Not assessed yet", screen_text(app_test))

    def test_a_changed_profile_marks_the_result_as_out_of_date(self):
        app_test = self.page()
        app_test.button(key="assess_all").click().run()
        self.assertNotIn("has changed since this was assessed", screen_text(app_test))
        profile.update_profile(self.database(), self.organisation_id, {"name": "BrightPath Youth Foundation", "mission": "A changed mission."})
        self.assertIn("has changed since this was assessed", screen_text(self.page()))

    def test_closed_grants_show_a_warning(self):
        connection = db.connect(self.path)
        with connection:
            connection.execute("UPDATE grant_record SET status = 'closed' WHERE id = ?", (self.scenarios[1],))
        connection.close()
        app_test = self.page()
        app_test.button(key=f"assess_0_{self.scenarios[1]}").click().run()
        self.assertIn("This grant is marked closed", " ".join(warning.value for warning in app_test.warning))

    def test_model_text_is_not_rendered_as_markup(self):
        def behaviour(payload):
            data = analysis_for(payload).model_dump()
            data["summary"] = "**bold** [click](http://example.invalid)"
            return data
        self.gateway = CannedGateway(behaviour)
        app_test = self.page()
        app_test.button(key=f"assess_0_{self.scenarios[1]}").click().run()
        self.assertIn(r"\*\*bold\*\* \[click\]\(http://example\.invalid\)", screen_text(app_test))

    def test_profile_gaps_are_listed_when_eligibility_is_unclear(self):
        app_test = self.page()
        app_test.button(key=f"assess_0_{self.scenarios[4]}").click().run()
        # the gaps sit inside an expander but are still part of the rendered tree
        self.assertIn("Your profile does not say: Legal form, Registration number, Annual income", screen_text(app_test))


if __name__ == "__main__":
    unittest.main()
