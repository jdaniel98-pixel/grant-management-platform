"""Grants screen for monitored grants: source, last checked, review flags, reset notes and change history (fictional data)."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import fictional_source
from fictional_source import BASE, SOURCE, FakeClock, FakeWeb, saved_pages
from grant_platform import db, seed
from grant_platform.repository import Repository
from grant_platform.services import discovery, grants
from grant_platform.services.fetcher import PoliteFetcher

SCRIPT = "from grant_platform.ui import app; app.main()"
FUNDER = "Meadowbank Community Trust (fictional)"


def screen_text(app_test):
    groups = (app_test.markdown, app_test.caption, app_test.title, app_test.subheader, app_test.info, app_test.success, app_test.warning, app_test.error)
    return "\n".join(element.value for group in groups for element in group)


def discover(repository, pages, now):
    clock = FakeClock()
    fetcher = PoliteFetcher(BASE, opener=FakeWeb(pages), clock=clock.clock, sleep=clock.sleep, agent="test-agent")
    return discovery.run(repository, "fictional-funder", adapters=fictional_source.ADAPTERS, fetcher=fetcher, now=now)


class MonitoredGrantScreenTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "test.sqlite3"
        patcher = patch.dict(os.environ, {"GRANT_PLATFORM_DB": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        connection = db.connect(self.path)
        self.repo = Repository(connection)
        self.addCleanup(connection.close)
        seed.seed_brightpath(self.repo)
        source = discovery.register_sources(self.repo, {"fictional-funder": SOURCE})[0]
        self.repo.set_source_enabled(source["id"], True)
        discover(self.repo, saved_pages(), "2026-10-29T08:00:00Z")
        self.digital = self.repo.find_grant_by_dedupe_key(grants.dedupe_key(FUNDER, "Digital Inclusion Grant"))
        self.youth = self.repo.find_grant_by_dedupe_key(grants.dedupe_key(FUNDER, "Youth Skills Fund"))
        grants.mark_verified(self.repo, self.youth["id"], "Test verifier")
        pages = saved_pages()
        pages[f"{BASE}/grants/youth-skills-fund"] = pages[f"{BASE}/grants/youth-skills-fund"].replace("£5,000 to £40,000", "£5,000 to £25,000")
        pages[f"{BASE}/grants"] = pages[f"{BASE}/grants"].replace('<li><a class="grant" href="/grants/digital-inclusion-grant">', '<li hidden><a class="x" href="#">')
        discover(self.repo, pages, "2026-10-30T08:00:00Z")

    def page(self):
        app_test = AppTest.from_string(SCRIPT, default_timeout=30).run()
        app_test.sidebar.radio(key="page").set_value("Grants").run()
        self.assertFalse(app_test.exception)
        return app_test

    def test_card_shows_source_checks_reset_note_and_history(self):
        text = screen_text(self.page())
        self.assertIn("Monitored source: Meadowbank open grants \\(fictional\\)", text)
        self.assertIn("Fictional test data", text)
        self.assertIn("Last checked 2026-10-30", text)
        self.assertIn("Details last changed 2026-10-30", text)
        self.assertIn("Verification was reset", text)
        self.assertIn("Funding", text)
        self.assertIn("Details changed", text)
        self.assertIn("First found", text)
        self.assertIn("Sent back to unverified", text)

    def test_review_flag_is_shown_with_both_choices(self):
        app_test = self.page()
        warnings = " ".join(w.value for w in app_test.warning)
        self.assertIn("Needs review: This grant is no longer in the source's list", warnings)
        self.assertIsNotNone(app_test.button(key=f"keep_{self.digital['id']}"))
        self.assertIsNotNone(app_test.button(key=f"close_{self.digital['id']}"))
        with self.assertRaises(KeyError):
            app_test.button(key=f"keep_{self.youth['id']}")

    def test_keep_as_it_is_clears_the_flag_and_leaves_the_grant_open(self):
        app_test = self.page()
        app_test.button(key=f"keep_{self.digital['id']}").click().run()
        self.assertFalse(app_test.exception)
        saved = self.repo.get_grant(self.digital["id"])
        self.assertEqual((saved["review_reason"], saved["status"]), (None, "open"))
        self.assertIn("Kept Digital Inclusion Grant as it is", screen_text(app_test))
        self.assertNotIn("Needs review", " ".join(w.value for w in self.page().warning))

    def test_mark_as_closed_closes_it(self):
        app_test = self.page()
        app_test.button(key=f"close_{self.digital['id']}").click().run()
        saved = self.repo.get_grant(self.digital["id"])
        self.assertEqual((saved["status"], saved["review_reason"]), ("closed", None))
        self.assertIn("Closed by a person", screen_text(self.page()))


if __name__ == "__main__":
    unittest.main()
