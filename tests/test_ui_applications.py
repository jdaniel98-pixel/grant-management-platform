"""Walkthroughs of deciding on matches and tracking applications with AppTest; fictional data, canned model answers."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from canned_ai import CORE, DIGITAL, CannedGateway
from grant_platform import db, seed
from grant_platform.repository import Repository
from grant_platform.services import ai, applications, matching, profile

SCRIPT = "from grant_platform.ui import app; app.main()"


def screen_text(app_test):
    groups = (app_test.markdown, app_test.caption, app_test.title, app_test.subheader, app_test.info, app_test.success, app_test.warning, app_test.error)
    return "\n".join(element.value for group in groups for element in group)


class ApplicationScreenTests(unittest.TestCase):
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
        self.gateway = CannedGateway()
        matching.assess_all(repository, self.organisation_id, None, gateway=self.gateway, model=ai.model_name())
        connection.close()
        self.calls_before = len(self.gateway.calls)
        patcher = patch.object(ai, "default_gateway", return_value=self.gateway)
        patcher.start()
        self.addCleanup(patcher.stop)

    def database(self):
        connection = db.connect(self.path)
        self.addCleanup(connection.close)
        return Repository(connection)

    def open(self, page):
        app_test = AppTest.from_string(SCRIPT, default_timeout=60).run()
        app_test.sidebar.radio(key="page").set_value(page).run()
        self.assertFalse(app_test.exception)
        return app_test

    def decide(self, app_test, decision, scenario, need_id=0):
        app_test.button(key=f"decide_{need_id}_{self.scenarios[scenario]}_{decision}").click().run()
        self.assertFalse(app_test.exception)

    def test_pursue_from_a_match_creates_a_tracked_application(self):
        app_test = self.open("Matches")
        self.decide(app_test, "pursue", 1)
        self.assertIn("Now pursuing Youth Employment Fund", screen_text(app_test))
        self.assertIn("Application: Pursuing", screen_text(app_test))
        with self.assertRaises(KeyError):
            app_test.button(key=f"decide_0_{self.scenarios[1]}_pursue")
        saved = self.database().list_applications(self.organisation_id)
        self.assertEqual([(a["grant_title"], a["status"]) for a in saved], [("Youth Employment Fund", "pursuing")])

        page = self.open("Applications")
        text = screen_text(page)
        self.assertIn("Youth Employment Fund", text)
        self.assertIn("**Status:** Pursuing", text)
        self.assertIn("Deadline: 31 March 2027 (in ", text)
        self.assertIn("Requirements: 0 of 5 done", text)
        self.assertIn("Open tasks: 0", text)
        self.assertIn("Whole organisation", text)

    def test_reject_removes_the_grant_from_suggestions_and_it_is_not_assessed_again(self):
        app_test = self.open("Matches")
        self.decide(app_test, "reject", 3)
        titles = [subheader.value for subheader in app_test.subheader]
        self.assertNotIn("American Youth Initiative", titles)
        self.assertIn("Rejected for this scope (1)", " ".join(expander.label for expander in app_test.expander))
        app_test.button(key="assess_all").click().run()
        self.assertNotIn("American Youth Initiative", [call["payload"]["grant"]["title"] for call in self.gateway.calls[self.calls_before:]])
        self.assertIn("Rejected", [option for option in self.open("Applications").selectbox(key="application_filter").options])

    def test_rejection_is_per_need(self):
        app_test = self.open("Matches")
        self.decide(app_test, "reject", 3)
        app_test.selectbox(key="match_scope").select(self.needs[DIGITAL]).run()
        self.assertNotIn("Rejected for this scope", " ".join(expander.label for expander in app_test.expander))

    def test_reconsider_moves_a_rejected_grant_back_to_saved(self):
        app_test = self.open("Matches")
        self.decide(app_test, "reject", 3)
        app_test.button(key=f"reconsider_0_{self.scenarios[3]}").click().run()
        self.assertIn("Moved American Youth Initiative back to saved", screen_text(app_test))
        self.assertIn("Application: Saved", screen_text(app_test))
        self.assertEqual(self.database().list_applications(self.organisation_id)[0]["status"], "saved")

    def test_moving_through_statuses_to_an_awarded_outcome_with_history(self):
        self.database()
        app_test = self.open("Matches")
        self.decide(app_test, "save", 1)
        app_id = self.database().list_applications(self.organisation_id)[0]["id"]
        page = self.open("Applications")
        for step in ("pursuing", "drafting", "submitted"):
            page.selectbox(key=f"app_{app_id}_next").select(step)
            page.button(key=f"app_{app_id}_update").click().run()
            self.assertFalse(page.exception)
        self.assertIn("is now submitted", screen_text(page))
        page.selectbox(key=f"app_{app_id}_next").select("awarded")
        page.text_input(key=f"app_{app_id}_note").set_value("Panel decision 12 May")
        page.text_input(key=f"app_{app_id}_amount").set_value("£45,000")
        page.button(key=f"app_{app_id}_update").click().run()
        self.assertIn("Nothing under active right now", screen_text(page))  # finished work leaves the default view
        page.selectbox(key="application_filter").select("Finished").run()
        text = screen_text(page)
        self.assertIn("**Status:** Awarded", text)
        self.assertIn("Amount awarded: £45,000", text)
        self.assertIn("Panel decision 12 May", text)
        self.assertIn("This is a final outcome", text)
        for expected in ("Started as Saved", "Saved to Pursuing", "Pursuing to Drafting", "Drafting to Submitted", "Submitted to Awarded: Panel decision 12 May"):
            self.assertIn(expected, text)
        self.assertEqual(self.database().get_application(self.organisation_id, app_id)["amount_awarded"], 45000)

    def test_a_bad_amount_is_reported_and_nothing_changes(self):
        app_test = self.open("Matches")
        self.decide(app_test, "pursue", 1)
        app_id = self.database().list_applications(self.organisation_id)[0]["id"]
        page = self.open("Applications")
        page.selectbox(key=f"app_{app_id}_next").select("submitted")
        page.button(key=f"app_{app_id}_update").click().run()
        page.selectbox(key=f"app_{app_id}_next").select("awarded")
        page.text_input(key=f"app_{app_id}_amount").set_value("lots")
        page.button(key=f"app_{app_id}_update").click().run()
        self.assertTrue(any("Amount awarded" in error.value for error in page.error))
        self.assertEqual(self.database().get_application(self.organisation_id, app_id)["status"], "submitted")

    def test_requirements_and_tasks_persist(self):
        app_test = self.open("Matches")
        self.decide(app_test, "pursue", 1)
        app_id = self.database().list_applications(self.organisation_id)[0]["id"]
        requirement = self.database().list_requirements(self.organisation_id, app_id)[0]
        page = self.open("Applications")
        page.checkbox(key=f"req_{app_id}_{requirement['id']}").check().run()
        page.text_input(key=f"app_{app_id}_task_text").set_value("Ask trustees for approval")
        page.text_input(key=f"app_{app_id}_task_due").set_value("15/03/2027")
        page.button(key=f"app_{app_id}_task_add").click().run()
        page.text_input(key=f"app_{app_id}_requirement_text").set_value("Get a reference")
        page.button(key=f"app_{app_id}_requirement_add").click().run()
        self.assertFalse(page.exception)
        text = screen_text(self.open("Applications"))
        self.assertIn("Requirements: 1 of 6 done", text)
        self.assertIn("Open tasks: 1", text)
        self.assertTrue(self.database().list_requirements(self.organisation_id, app_id)[0]["done"])
        tasks = self.database().list_tasks(self.organisation_id, app_id)
        self.assertEqual((tasks[0]["text"], tasks[0]["due_date"]), ("Ask trustees for approval", "2027-03-15"))
        reopened = self.open("Applications")
        reopened.checkbox(key=f"task_{app_id}_{tasks[0]['id']}").check().run()
        self.assertTrue(self.database().list_tasks(self.organisation_id, app_id)[0]["done"])
        self.assertIn("Open tasks: 0", screen_text(reopened))

    def test_the_workspace_stays_open_after_adding_a_task(self):
        app_test = self.open("Matches")
        self.decide(app_test, "pursue", 1)
        app_id = self.database().list_applications(self.organisation_id)[0]["id"]
        page = self.open("Applications")
        self.assertFalse(next(e for e in page.expander if e.label == "Open workspace").proto.expanded)
        page.text_input(key=f"app_{app_id}_task_text").set_value("Task")
        page.button(key=f"app_{app_id}_task_add").click().run()
        self.assertTrue(next(e for e in page.expander if e.label == "Open workspace").proto.expanded)

    def test_a_task_with_a_bad_date_is_refused(self):
        app_test = self.open("Matches")
        self.decide(app_test, "pursue", 1)
        app_id = self.database().list_applications(self.organisation_id)[0]["id"]
        page = self.open("Applications")
        page.text_input(key=f"app_{app_id}_task_text").set_value("Task")
        page.text_input(key=f"app_{app_id}_task_due").set_value("someday")
        page.button(key=f"app_{app_id}_task_add").click().run()
        self.assertTrue(any("Due date" in error.value for error in page.error))
        self.assertEqual(self.database().list_tasks(self.organisation_id, app_id), [])

    def test_need_specific_applications_show_their_need(self):
        app_test = self.open("Matches")
        app_test.selectbox(key="match_scope").select(self.needs[CORE]).run()
        app_test.button(key=f"assess_{self.needs[CORE]}_{self.scenarios[6]}").click().run()
        self.decide(app_test, "reject", 6, self.needs[CORE])
        page = self.open("Applications")
        page.selectbox(key="application_filter").select("Rejected").run()
        text = screen_text(page)
        self.assertIn(f"For: {CORE}", text)

    def test_another_organisation_sees_no_applications(self):
        app_test = self.open("Matches")
        self.decide(app_test, "pursue", 1)
        other_id = profile.create_organisation(self.database(), {"name": "Riverside Test Charity (fictional)", "mission": "Testing."})
        page = self.open("Applications")
        page.sidebar.selectbox(key="organisation").select(other_id).run()
        self.assertIn("No applications here yet", screen_text(page))
        self.assertNotIn("Youth Employment Fund", [subheader.value for subheader in page.subheader])

    def test_the_applications_screen_is_empty_before_any_decision(self):
        self.assertIn("No applications here yet", screen_text(self.open("Applications")))

    def test_user_text_is_not_rendered_as_markup(self):
        app_test = self.open("Matches")
        self.decide(app_test, "pursue", 1)
        app_id = self.database().list_applications(self.organisation_id)[0]["id"]
        page = self.open("Applications")
        page.selectbox(key=f"app_{app_id}_next").select("withdrawn")
        page.text_input(key=f"app_{app_id}_note").set_value("**bold** [x](http://example.invalid)")
        page.button(key=f"app_{app_id}_update").click().run()
        page.selectbox(key="application_filter").select("Finished").run()
        self.assertIn(r"\*\*bold\*\* \[x\]\(http://example\.invalid\)", screen_text(page))


if __name__ == "__main__":
    unittest.main()
