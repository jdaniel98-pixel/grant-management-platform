"""Walkthroughs of drafting inside an application with AppTest and canned model answers; fictional data only."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from canned_ai import DIGITAL, IMPACT_ANSWER, CannedDraftGateway
from grant_platform import db, seed
from grant_platform.models import EvidenceRecord
from grant_platform.repository import Repository
from grant_platform.services import ai, applications
from grant_platform.services.matching import GatewayError

SCRIPT = "from grant_platform.ui import app; app.main()"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
IMPACT = "Describe the impact of your work"


def screen_text(app_test):
    groups = (app_test.markdown, app_test.caption, app_test.title, app_test.subheader, app_test.info, app_test.success, app_test.warning, app_test.error, app_test.text)
    return "\n".join(element.value for group in groups for element in group)


class DraftingScreenTests(unittest.TestCase):
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
        for entry in json.loads((FIXTURES / "extra_evidence.json").read_text(encoding="utf-8")):
            repository.add_evidence(self.organisation_id, EvidenceRecord(**entry))
        self.scenarios, _ = seed.seed_scenario_grants(repository)
        needs = {need["title"]: need["id"] for need in repository.list_funding_needs(self.organisation_id)}
        self.application_id = applications.decide(repository, self.organisation_id, self.scenarios[6], needs[DIGITAL], "pursue")["id"]
        connection.close()
        self.gateway = CannedDraftGateway()

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
        app_test.sidebar.radio(key="page").set_value("Applications").run()
        self.assertFalse(app_test.exception)
        return app_test

    def ask(self, app_test, question, word_limit="", button="draft"):
        app_test.text_area(key=f"app_{self.application_id}_question").set_value(question)
        app_test.text_input(key=f"app_{self.application_id}_word_limit").set_value(word_limit)
        app_test.button(key=f"app_{self.application_id}_{button}").click().run()
        self.assertFalse(app_test.exception)

    def drafts(self):
        return self.database().list_drafts(self.organisation_id, self.application_id)

    def test_drafting_shows_an_ai_draft_with_its_cited_evidence(self):
        app_test = self.page()
        self.ask(app_test, IMPACT)
        self.assertIn("Draft created", screen_text(app_test))
        draft = self.drafts()[0]
        self.assertEqual(draft["ai_text"], IMPACT_ANSWER)
        self.assertEqual(app_test.text_area(key=f"draft_{draft['id']}_text").value, IMPACT_ANSWER)
        text = screen_text(app_test)
        self.assertIn("AI draft, not reviewed", text)
        self.assertIn("Approved record (2024): In 2024, BrightPath supported 312 young people", text)
        self.assertIn("Approved record (2024): Of the 214 participants", text)
        self.assertEqual([warning.value for warning in app_test.warning], [])
        self.assertNotIn("Missing evidence", text)

    def test_the_workspace_stays_open_after_drafting_so_the_draft_is_visible(self):
        app_test = self.page()
        workspace = lambda: next(e for e in app_test.expander if e.label == "Open workspace")
        self.assertFalse(workspace().proto.expanded)
        self.ask(app_test, IMPACT)
        self.assertTrue(workspace().proto.expanded)

    def test_a_question_the_evidence_cannot_answer_shows_missing_evidence_and_no_figures(self):
        app_test = self.page()
        self.ask(app_test, "Provide your 2025 outcomes")
        draft = self.drafts()[0]
        self.assertEqual(draft["ai_text"], "")
        text = screen_text(app_test)
        self.assertIn("Missing evidence", text)
        self.assertIn("validated 2025 annual impact totals", text)
        self.assertIn("evidence record knowledge\\_limit\\_2025", text)

    def test_edits_are_saved_beside_the_original_and_the_person_can_mark_the_draft_reviewed(self):
        app_test = self.page()
        self.ask(app_test, IMPACT)
        draft_id = self.drafts()[0]["id"]
        app_test.text_area(key=f"draft_{draft_id}_text").set_value("My edited impact statement.")
        app_test.button(key=f"draft_{draft_id}_save").click().run()
        self.assertIn("The original AI text is kept", screen_text(app_test))
        saved = self.database().get_draft(self.organisation_id, draft_id)
        self.assertEqual((saved["ai_text"], saved["edited_text"]), (IMPACT_ANSWER, "My edited impact statement."))
        self.assertIn(IMPACT_ANSWER[:40], " ".join(element.value for element in app_test.text))
        app_test.button(key=f"draft_{draft_id}_review").click().run()
        self.assertIn("Reviewed by you", screen_text(app_test))
        reviewed = self.database().get_draft(self.organisation_id, draft_id)
        self.assertEqual((reviewed["reviewed_by_user"], reviewed["edited_text"]), (True, "My edited impact statement."))
        with self.assertRaises(KeyError):
            app_test.button(key=f"draft_{draft_id}_review")

    def test_an_invented_figure_is_shown_as_a_warning(self):
        def invented(payload):
            return {"answer": "We supported 400 young people.", "citations": [{"record_id": "impact_2024_participants", "claim": "x"}], "missing_evidence": []}
        self.gateway = CannedDraftGateway(invented)
        app_test = self.page()
        self.ask(app_test, IMPACT)
        self.assertIn("Figures not found in the supplied evidence: 400", " ".join(warning.value for warning in app_test.warning))

    def test_a_citation_that_cannot_be_checked_is_labelled(self):
        def odd(payload):
            return {"answer": "Something.", "citations": [{"record_id": "fixture_internal_board_note", "claim": "board"}], "missing_evidence": []}
        self.gateway = CannedDraftGateway(odd)
        app_test = self.page()
        self.ask(app_test, IMPACT)
        text = screen_text(app_test)
        self.assertIn("cannot be checked", text)
        self.assertNotIn("internal board discussion", text)

    def test_without_ai_the_screen_still_previews_the_evidence_and_gaps(self):
        app_test = self.page(gateway=None)
        self.assertIn("AI drafting is not set up on this computer", screen_text(app_test))
        with self.assertRaises(KeyError):
            app_test.button(key=f"app_{self.application_id}_draft")
        self.ask(app_test, "Provide staff salary costs", button="preview")
        text = screen_text(app_test)
        self.assertIn("43 approved records would be used", text)
        self.assertIn("Known gap:", text)
        self.assertIn("salary scales", text)
        self.assertEqual(self.drafts(), [])

    def test_the_preview_lists_only_approved_publishable_records(self):
        app_test = self.page(gateway=None)
        self.ask(app_test, IMPACT, button="preview")
        text = screen_text(app_test)
        self.assertIn("impact\\_2024\\_participants", text)
        self.assertNotIn("fixture\\_internal\\_board\\_note", text)
        self.assertNotIn("fixture\\_draft\\_impact\\_2025", text)

    def test_validation_errors_are_shown_and_nothing_is_drafted(self):
        app_test = self.page()
        self.ask(app_test, "", word_limit="lots")
        errors = " ".join(error.value for error in app_test.error)
        self.assertIn("Question", errors)
        self.assertEqual(self.drafts(), [])
        self.assertEqual(self.gateway.calls, [])

    def test_a_service_failure_says_so_and_saves_nothing(self):
        def failing(payload):
            raise GatewayError("RateLimitError, HTTP 429")
        self.gateway = CannedDraftGateway(failing)
        app_test = self.page()
        self.ask(app_test, IMPACT)
        self.assertIn("Nothing was saved", " ".join(error.value for error in app_test.error))
        self.assertEqual(self.drafts(), [])

    def test_word_limit_is_stored_and_shown(self):
        app_test = self.page()
        self.ask(app_test, IMPACT, word_limit="40")
        self.assertEqual(self.drafts()[0]["word_limit"], 40)
        self.assertIn("Over the word limit by", " ".join(warning.value for warning in app_test.warning))
        self.assertIn("Word limit: 40", screen_text(app_test))

    def test_text_from_the_question_and_the_model_is_not_rendered_as_markup(self):
        def markup(payload):
            return {"answer": "", "citations": [], "missing_evidence": ["**bold** [x](http://example.invalid)"]}
        self.gateway = CannedDraftGateway(markup)
        app_test = self.page()
        self.ask(app_test, "What about **this** question?")
        text = screen_text(app_test)
        self.assertIn(r"\*\*this\*\*", text)
        self.assertIn(r"\*\*bold\*\* \[x\]\(http://example\.invalid\)", text)

    def test_a_rejected_grant_cannot_be_drafted(self):
        connection = db.connect(self.path)
        repository = Repository(connection)
        rejected = applications.decide(repository, self.organisation_id, self.scenarios[3], None, "reject")
        connection.close()
        app_test = self.page()
        app_test.selectbox(key="application_filter").select("Rejected").run()
        self.assertIn("Reconsider it from the Matches page to draft", screen_text(app_test))
        with self.assertRaises(KeyError):
            app_test.button(key=f"app_{rejected['id']}_draft")


if __name__ == "__main__":
    unittest.main()
