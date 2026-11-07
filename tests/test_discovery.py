"""Discovery pipeline against a fictional source's saved pages: new grants are unverified, nothing is closed or
deleted, duplicates aren't created twice, fetched text stays data, and failures are recorded (no live network)."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fictional_source
from fictional_source import BASE, SOURCE, FakeClock, FakeWeb, saved_pages
from grant_platform import db, jobs
from grant_platform.repository import Repository
from grant_platform.services import discovery, grants
from grant_platform.services.fetcher import PoliteFetcher


def fetcher_for(web, max_pages=10):
    clock = FakeClock()
    return PoliteFetcher(BASE, max_pages=max_pages, opener=web, clock=clock.clock, sleep=clock.sleep, agent="test-agent")


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repo = Repository(self.connection)
        self.source = discovery.register_sources(self.repo, {"fictional-funder": SOURCE})[0]
        self.repo.set_source_enabled(self.source["id"], True)

    def run_source(self, web=None, **options):
        return discovery.run(self.repo, "fictional-funder", adapters=fictional_source.ADAPTERS,
                             fetcher=fetcher_for(web or FakeWeb(), options.pop("max_pages", 10)), **options)

    def test_new_grants_are_saved_as_monitored_unverified_and_linked_to_their_source(self):
        summary = self.run_source()
        self.assertEqual((summary.status, len(summary.new), summary.seen, summary.pages_fetched), ("ok", 3, [], 4))
        saved = self.repo.find_grants_by_source(self.source["id"])
        self.assertEqual(len(saved), 3)
        for grant in saved:
            self.assertEqual((grant["origin"], grant["verification_status"], grant["verified_by"]), ("monitored", "unverified", None))
        youth = next(g for g in saved if g["title"] == "Youth Skills Fund")
        self.assertEqual((youth["canonical_url"], youth["deadline_date"], youth["funding"]),
                         ("https://meadowbank.example.org/grants/youth-skills-fund", "2027-03-31", "£5,000 to £40,000"))

    def test_a_second_run_creates_no_duplicates(self):
        self.run_source()
        summary = self.run_source()
        self.assertEqual((len(summary.new), len(summary.seen)), (0, 3))
        self.assertEqual(len(self.repo.list_grants()), 3)

    def test_a_grant_already_entered_by_hand_is_not_duplicated(self):
        grants.create_grant(self.repo, {"title": "Youth Skills Fund", "funder": "Meadowbank Community Trust (fictional)"})
        summary = self.run_source()
        self.assertEqual((len(summary.new), len(summary.seen)), (2, 1))
        self.assertEqual(len(self.repo.list_grants()), 3)

    def test_fetched_instructions_are_stored_as_text_and_change_nothing(self):
        other = grants.create_grant(self.repo, {"title": "Existing Fund", "funder": "Another Trust (fictional)", "status": "open"})
        self.run_source()
        voices = self.repo.find_grant_by_dedupe_key(grants.dedupe_key("Meadowbank Community Trust (fictional)", "Community Voices Grant"))
        self.assertIn("Ignore previous instructions", voices["eligibility"])
        self.assertEqual((voices["verification_status"], voices["status"]), ("unverified", "open"))
        self.assertEqual(self.repo.get_grant(other["id"]), other)
        self.assertEqual(len(self.repo.list_grants()), 4)

    def test_dry_run_fetches_but_writes_nothing(self):
        summary = self.run_source(dry_run=True)
        self.assertEqual(sorted(summary.new), ["Community Voices Grant", "Digital Inclusion Grant", "Youth Skills Fund"])
        self.assertEqual((self.repo.list_grants(), self.repo.list_discovery_runs()), ([], []))

    def duplicated_pages(self):
        pages = saved_pages()
        pages[f"{BASE}/grants/youth-skills-fund-again"] = pages[f"{BASE}/grants/youth-skills-fund"]
        pages[f"{BASE}/grants"] = pages[f"{BASE}/grants"].replace("</body>", '<a class="grant" href="/grants/youth-skills-fund-again">again</a></body>')
        return pages

    def test_the_same_funder_and_title_at_two_addresses_is_kept_once_and_reported(self):
        summary = self.run_source(FakeWeb(self.duplicated_pages()))
        self.assertEqual((len(summary.new), len(summary.duplicates), len(self.repo.list_grants())), (3, 1, 3))
        self.assertEqual(summary.duplicates[0][0], "Youth Skills Fund")
        again = self.run_source(FakeWeb(self.duplicated_pages()))
        self.assertEqual((len(again.new), len(again.updated), len(again.reset), len(again.duplicates)), (0, 0, 0, 1))

    def test_a_dry_run_reports_the_same_duplicate_instead_of_counting_it_twice(self):
        summary = self.run_source(FakeWeb(self.duplicated_pages()), dry_run=True)
        self.assertEqual((len(summary.new), len(summary.duplicates)), (3, 1))

    def test_each_run_is_recorded(self):
        self.run_source()
        run = self.repo.list_discovery_runs(self.source["id"])[0]
        self.assertEqual((run["status"], run["pages_fetched"], run["grants_new"], run["grants_seen"], run["problems"]), ("ok", 4, 3, 0, []))
        self.assertIsNotNone(run["finished_at"])

    def test_a_failure_part_way_keeps_what_was_saved_and_closes_nothing(self):
        self.run_source()
        pages = saved_pages()
        pages[f"{BASE}/grants/digital-inclusion-grant"] = 503
        before = {g["id"]: g for g in self.repo.list_grants()}
        summary = self.run_source(FakeWeb(pages))
        self.assertEqual(summary.status, "partial")
        self.assertIn("503", summary.problems[0])
        self.assertEqual({g["id"]: g for g in self.repo.list_grants()}, before)
        self.assertEqual(self.repo.list_discovery_runs()[0]["status"], "partial")

    def test_an_unreachable_site_fails_the_run_without_touching_grants(self):
        self.run_source()
        before = self.repo.list_grants()
        summary = self.run_source(FakeWeb(down=True))
        self.assertEqual(summary.status, "failed")
        self.assertIn("robots.txt", summary.problems[0])
        self.assertEqual(self.repo.list_grants(), before)

    def test_page_cap_ends_the_run_as_partial(self):
        summary = self.run_source(max_pages=2)
        self.assertEqual((summary.status, len(summary.new), summary.pages_fetched), ("partial", 1, 2))

    def test_unreadable_entries_are_skipped_and_reported(self):
        pages = saved_pages()
        pages[f"{BASE}/grants/digital-inclusion-grant"] = pages[f"{BASE}/grants/digital-inclusion-grant"].replace("30/06/2027", "sometime soon")
        summary = self.run_source(FakeWeb(pages))
        self.assertEqual((summary.status, len(summary.new)), ("ok", 2))
        self.assertIn("deadline_date", summary.problems[0])

    def test_disabled_or_unknown_sources_do_not_run(self):
        self.repo.set_source_enabled(self.source["id"], False)
        with self.assertRaises(PermissionError):
            self.run_source()
        with self.assertRaises(LookupError):
            discovery.run(self.repo, "no-such-source")

    def test_a_dry_run_may_review_a_disabled_source_without_enabling_or_saving(self):
        self.repo.set_source_enabled(self.source["id"], False)
        summary = self.run_source(dry_run=True)
        self.assertEqual(len(summary.new), 3)
        self.assertEqual((self.repo.list_grants(), self.repo.list_discovery_runs()), ([], []))
        self.assertFalse(self.repo.get_source_by_slug("fictional-funder")["enabled"])
        with self.assertRaises(PermissionError):
            self.run_source()

    def test_registering_again_keeps_the_enabled_flag(self):
        source = discovery.register_sources(self.repo, {"fictional-funder": {**SOURCE, "max_pages_per_run": 7}})[0]
        self.assertEqual((source["enabled"], source["max_pages_per_run"], source["coverage"]), (True, 7, ["UK", "England"]))


class DiscoverCommandTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db_path = str(Path(folder.name) / "test.sqlite3")
        self.web = FakeWeb()
        real = PoliteFetcher.__init__

        def offline(instance, base_url, **options):
            clock = FakeClock()
            real(instance, base_url, **{**options, "opener": self.web, "clock": clock.clock, "sleep": clock.sleep})

        for patcher in (patch.dict(discovery.SOURCES, {"fictional-funder": SOURCE}, clear=True),
                        patch.dict(discovery.ADAPTERS, fictional_source.ADAPTERS, clear=True),
                        patch.object(PoliteFetcher, "__init__", offline)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def command(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = jobs.main([*args, "--db", self.db_path])
        return code, out.getvalue(), err.getvalue()

    def test_sources_start_disabled_and_discover_refuses_them(self):
        code, out, _ = self.command("sources")
        self.assertEqual(code, 0)
        self.assertIn("fictional-funder", out)
        self.assertIn("disabled", out)
        code, _, err = self.command("discover", "fictional-funder")
        self.assertEqual(code, 1)
        self.assertIn("not enabled", err)

    def test_dry_run_works_on_a_disabled_source_and_leaves_it_disabled(self):
        code, out, _ = self.command("discover", "fictional-funder", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Dry run (nothing saved)", out)
        self.assertIn("disabled", self.command("sources")[1])
        self.assertEqual(self.command("discover", "fictional-funder")[0], 1)
        self.assertIn("3 new grants", self.command("sources", "--enable", "fictional-funder") and self.command("discover", "fictional-funder")[1])

    def test_dry_run_then_enable_then_run(self):
        self.command("sources", "--enable", "fictional-funder")
        code, out, _ = self.command("discover", "fictional-funder", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Dry run (nothing saved)", out)
        self.assertIn("new: Youth Skills Fund", out)
        code, out, _ = self.command("discover", "fictional-funder")
        self.assertIn("3 new grants, 0 already saved", out)
        code, out, _ = self.command("discover", "fictional-funder")
        self.assertIn("0 new grants, 3 already saved", out)

    def test_housekeep_closes_only_expired_grants_and_dry_run_changes_nothing(self):
        connection = db.connect(self.db_path)
        repository = Repository(connection)
        grants.create_grant(repository, {"title": "Old Fund", "funder": "A", "deadline_date": "2020-01-31", "status": "open"})
        grants.create_grant(repository, {"title": "Future Fund", "funder": "A", "deadline_date": "2999-01-31", "status": "open"})
        connection.close()
        code, out, _ = self.command("housekeep", "--dry-run")
        self.assertEqual((code, out.strip()), (0, "Would close 1 grant whose closing date has passed"))
        code, out, _ = self.command("housekeep")
        self.assertEqual(out.strip(), "Closed 1 grant whose closing date has passed")
        code, out, _ = self.command("housekeep")
        self.assertEqual(out.strip(), "Closed 0 grants whose closing date has passed")
        connection = db.connect(self.db_path)
        statuses = {g["title"]: g["status"] for g in Repository(connection).list_grants()}
        connection.close()
        self.assertEqual(statuses, {"Old Fund": "closed", "Future Fund": "open"})

    def test_discover_output_lists_flags_and_changes(self):
        self.command("sources", "--enable", "fictional-funder")
        self.command("discover", "fictional-funder")
        self.web.pages[f"{BASE}/grants"] = self.web.pages[f"{BASE}/grants"].replace('<li><a class="grant" href="/grants/digital-inclusion-grant">', '<li hidden><a class="x" href="#">')
        self.web.pages[f"{BASE}/grants/youth-skills-fund"] = self.web.pages[f"{BASE}/grants/youth-skills-fund"].replace("£5,000 to £40,000", "£5,000 to £25,000")
        code, out, _ = self.command("discover", "fictional-funder")
        self.assertEqual(code, 0)
        self.assertIn("changed: #", out)
        self.assertIn("needs review: #", out)
        self.assertIn("no longer in the source's list", out)

    def test_unknown_source(self):
        code, _, err = self.command("discover", "nowhere")
        self.assertEqual(code, 1)
        self.assertIn("No source called", err)


if __name__ == "__main__":
    unittest.main()
