"""The whole core journey for the fictional Bright Path charity, through the real screens with canned model answers:
profile and need -> grants -> match -> pursue -> application -> evidence-cited draft -> deadline reminder."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from canned_ai import DIGITAL, IMPACT_ANSWER, CannedGateway, analysis_for, draft_for
from grant_platform import db, jobs, seed
from grant_platform.repository import Repository
from grant_platform.services import ai

SCRIPT = "from grant_platform.ui import app; app.main()"


def both(payload):
    return draft_for(payload) if "evidence_records" in payload else analysis_for(payload)


def screen_text(app_test):
    groups = (app_test.markdown, app_test.caption, app_test.title, app_test.subheader, app_test.info, app_test.success, app_test.warning, app_test.error)
    return "\n".join(element.value for group in groups for element in group)


class CoreJourneyTest(unittest.TestCase):
    def test_bright_path_from_profile_to_draft_and_reminder(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "instance" / "journey.sqlite3"
            gateway = CannedGateway(both)
            with patch.dict(os.environ, {"GRANT_PLATFORM_DB": str(path)}), patch.object(ai, "default_gateway", return_value=gateway):
                # Set-up exactly as the README says: seed Bright Path and the scenario grants.
                connection = db.connect(path)
                repository = Repository(connection)
                organisation_id, _ = seed.seed_brightpath(repository)
                seed.seed_scenario_grants(repository)
                need_id = next(need["id"] for need in repository.list_funding_needs(organisation_id) if need["title"] == DIGITAL)
                connection.close()

                app_test = AppTest.from_string(SCRIPT, default_timeout=60).run()
                self.assertFalse(app_test.exception)
                # 1. Profile and funding need exist and are shown.
                self.assertEqual(app_test.title[0].value, "BrightPath Youth Foundation")
                app_test.sidebar.radio(key="page").set_value("Funding needs").run()
                self.assertIn(DIGITAL, [subheader.value for subheader in app_test.subheader])
                # 2. Grants are listed, unverified.
                app_test.sidebar.radio(key="page").set_value("Grants").run()
                self.assertIn("Unverified", screen_text(app_test))
                # 3. Match for the Digital Futures need.
                app_test.sidebar.radio(key="page").set_value("Matches").run()
                app_test.selectbox(key="match_scope").select(need_id).run()
                app_test.button(key="assess_all").click().run()
                self.assertFalse(app_test.exception)
                self.assertIn("Assessed 7 grants", screen_text(app_test))
                self.assertIn("Ineligible (confirmed)", screen_text(app_test))
                titles = [subheader.value for subheader in app_test.subheader]
                self.assertEqual(titles[0], "Digital Skills for Young People")
                connection = db.connect(path)
                grant_id = next(g["id"] for g in Repository(connection).list_grants() if g["title"] == titles[0])
                connection.close()
                # 4. Pursue it.
                app_test.button(key=f"decide_{need_id}_{grant_id}_pursue").click().run()
                self.assertIn("Now pursuing Digital Skills for Young People", screen_text(app_test))
                # 5. It appears in Applications with deadline, requirements and (after adding) tasks.
                app_test.sidebar.radio(key="page").set_value("Applications").run()
                text = screen_text(app_test)
                self.assertIn("Digital Skills for Young People", text)
                self.assertIn("Deadline: 31 May 2027", text)
                self.assertIn("Requirements: 0 of 5 done", text)
                connection = db.connect(path)
                application_id = Repository(connection).list_applications(organisation_id)[0]["id"]
                connection.close()
                app_test.text_input(key=f"app_{application_id}_task_text").set_value("Ask trustees to approve the budget")
                app_test.text_input(key=f"app_{application_id}_task_due").set_value("30/04/2027")
                app_test.button(key=f"app_{application_id}_task_add").click().run()
                self.assertIn("Open tasks: 1", screen_text(app_test))
                # 6. Draft an answer to "Describe the impact of your work" with cited records.
                app_test.text_area(key=f"app_{application_id}_question").set_value("Describe the impact of your work")
                app_test.button(key=f"app_{application_id}_draft").click().run()
                self.assertFalse(app_test.exception)
                connection = db.connect(path)
                draft = Repository(connection).list_drafts(organisation_id, application_id)[0]
                connection.close()
                self.assertEqual(draft["ai_text"], IMPACT_ANSWER)
                self.assertEqual(draft["review_flags"], [])
                self.assertIn("Approved record (2024): In 2024, BrightPath supported 312 young people", screen_text(app_test))
                self.assertIn("AI draft, not reviewed", screen_text(app_test))
                app_test.button(key=f"draft_{draft['id']}_review").click().run()
                self.assertIn("Reviewed by you", screen_text(app_test))
                # 7. A deadline reminder lands in the outbox.
                with patch("sys.stdout"):
                    self.assertEqual(jobs.main(["notify", "--db", str(path), "--today", "2027-05-24"]), 0)
                reminder = next((path.parent / "outbox").glob("*.txt")).read_text(encoding="utf-8")
                self.assertIn("Subject: Deadline in 7 days: Digital Skills for Young People (BrightPath Youth Foundation)", reminder)
                self.assertIn("Ask trustees to approve the budget (due 2027-04-30)", reminder)
                self.assertIn("It was not emailed.", reminder)
        # The model saw matching and drafting payloads only: one matching call per grant plus one draft.
        self.assertEqual(sum("evidence_records" in call["payload"] for call in gateway.calls), 1)
        self.assertEqual(len(gateway.calls), 8)


if __name__ == "__main__":
    unittest.main()
