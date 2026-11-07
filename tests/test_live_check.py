"""The live-check helper, tested offline with canned answers. It must never print or write a credential."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from canned_ai import CannedGateway, analysis_for, draft_for
from grant_platform import live_check
from grant_platform.services import ai

FAKE_KEY = "sk-test-FAKE9876543210"


def both(payload):
    return draft_for(payload) if "evidence_records" in payload else analysis_for(payload)


class LiveCheckTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        for patcher in (patch.dict(os.environ, {"OPENAI_API_KEY": FAKE_KEY}), patch.object(ai, "ENV_PATH", Path(self.folder.name) / "none.env")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def main(self, *arguments):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = live_check.main(list(arguments), report_dir=Path(self.folder.name) / "evaluations")
        return code, out.getvalue()

    def test_the_report_covers_one_match_and_one_draft_and_leaves_room_for_a_verdict(self):
        report = live_check.run(CannedGateway(both), "gpt-test", today="2026-10-28")
        for expected in ("# Live check, 2026-10-28", "Model: gpt-test", "## Match: Digital Skills for Young People for the need 'Digital Futures expansion'",
                         "Eligibility: Eligible on available information", "Suitability: Strong fit, 23 out of 25", "Suggested next step: Pursue",
                         "## Draft: Describe the impact of your work", "AI draft, not reviewed", "impact_2024_participants (approved record found)",
                         "all citations resolve, no unsupported figures", "## Human verdict", "Is every claim in the draft supported"):
            self.assertIn(expected, report)

    def test_nothing_runs_without_the_run_flag(self):
        with patch.object(ai, "default_gateway", side_effect=AssertionError("must not be called")):
            code, out = self.main()
        self.assertEqual(code, 2)
        self.assertIn("Add --run", out)

    def test_without_a_key_it_explains_and_stops(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            code, out = self.main("--run")
        self.assertEqual(code, 1)
        self.assertIn("No OpenAI key found", out)

    def test_a_run_writes_a_report_and_never_shows_the_key(self):
        gateway = CannedGateway(both)
        with patch.object(ai, "default_gateway", return_value=gateway):
            code, out = self.main("--run", "--grant", "6")
        self.assertEqual(code, 0)
        report_file = next((Path(self.folder.name) / "evaluations").glob("live_check_*_scenario6.md"))
        self.assertNotIn(FAKE_KEY, report_file.read_text(encoding="utf-8"))
        self.assertNotIn(FAKE_KEY, out)
        self.assertEqual(len(gateway.calls), 2)
        self.assertIn("Report written to", out)

    def test_a_failing_service_is_reported_without_details_that_could_hold_a_key(self):
        def failing(payload):
            raise ai.GatewayError("AuthenticationError, HTTP 401")
        with patch.object(ai, "default_gateway", return_value=CannedGateway(failing)):
            code, out = self.main("--run")
        self.assertEqual(code, 1)
        self.assertIn("HTTP 401", out)
        self.assertNotIn(FAKE_KEY, out)
        self.assertFalse((Path(self.folder.name) / "evaluations").exists())


if __name__ == "__main__":
    unittest.main()
