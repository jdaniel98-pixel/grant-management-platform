"""Change detection, source tracking and the review safeguards, run against the fictional source's saved pages."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import unittest
from datetime import date

import fictional_source
from fictional_source import BASE, SOURCE, FakeClock, FakeWeb, saved_pages
from grant_platform import db
from grant_platform.repository import Repository
from grant_platform.services import discovery, grants
from grant_platform.services.fetcher import PoliteFetcher

T1, T2, T3, T4 = ("2026-10-29T08:00:00Z", "2026-10-30T08:00:00Z", "2026-10-31T08:00:00Z", "2026-11-01T08:00:00Z")
YOUTH, DIGITAL, VOICES = f"{BASE}/grants/youth-skills-fund", f"{BASE}/grants/digital-inclusion-grant", f"{BASE}/grants/community-voices-grant"
FUNDER = "Meadowbank Community Trust (fictional)"


def fetcher_for(web):
    clock = FakeClock()
    return PoliteFetcher(BASE, opener=web, clock=clock.clock, sleep=clock.sleep, agent="test-agent")


class Harness(unittest.TestCase):
    def setUp(self):
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repo = Repository(self.connection)
        self.source = discovery.register_sources(self.repo, {"fictional-funder": SOURCE})[0]
        self.repo.set_source_enabled(self.source["id"], True)

    def run_source(self, now, pages=None, web=None, **options):
        return discovery.run(self.repo, "fictional-funder", adapters=fictional_source.ADAPTERS,
                             fetcher=fetcher_for(web or FakeWeb(pages if pages is not None else saved_pages())), now=now, **options)

    def grant(self, title):
        return self.repo.find_grant_by_dedupe_key(grants.dedupe_key(FUNDER, title))

    def kinds(self, title):
        return [event["kind"] for event in reversed(self.repo.list_grant_events(self.grant(title)["id"]))]

    @staticmethod
    def edited(url, old, new):
        pages = saved_pages()
        assert old in pages[url], old
        pages[url] = pages[url].replace(old, new)
        return pages


class TrackingTests(Harness):
    def test_a_new_grant_records_where_and_when_it_was_found(self):
        self.run_source(T1)
        youth = self.grant("Youth Skills Fund")
        self.assertEqual((youth["source_id"], youth["last_checked_at"], youth["last_changed_at"]), (self.source["id"], T1, T1))
        self.assertEqual(self.kinds("Youth Skills Fund"), ["found"])
        snapshots = self.repo.list_grant_snapshots(youth["id"])
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["fields"]["funding"], "£5,000 to £40,000")

    def test_an_unchanged_page_only_moves_the_last_checked_time(self):
        self.run_source(T1)
        before = self.grant("Youth Skills Fund")
        self.run_source(T2)
        after = self.grant("Youth Skills Fund")
        self.assertEqual((after["last_checked_at"], after["last_changed_at"]), (T2, T1))
        self.assertEqual({k: v for k, v in after.items() if k not in ("last_checked_at", "last_seen_at")},
                         {k: v for k, v in before.items() if k not in ("last_checked_at", "last_seen_at")})
        self.assertEqual(self.kinds("Youth Skills Fund"), ["found"])
        self.assertEqual(len(self.repo.list_grant_snapshots(after["id"])), 1)


class VerificationTests(Harness):
    def verified(self, title):
        grant = self.grant(title)
        grants.mark_verified(self.repo, grant["id"], "Test verifier")
        return grant["id"]

    def test_a_real_change_updates_the_grant_and_sends_a_verified_grant_back_to_unverified(self):
        self.run_source(T1)
        grant_id = self.verified("Youth Skills Fund")
        summary = self.run_source(T2, self.edited(YOUTH, "£5,000 to £40,000", "£5,000 to £25,000"))
        grant = self.repo.get_grant(grant_id)
        self.assertEqual((summary.updated, summary.reset), ([grant_id], [grant_id]))
        self.assertEqual(grant["funding"], "£5,000 to £25,000")
        self.assertEqual((grant["verification_status"], grant["verified_by"], grant["verified_at"]), ("unverified", None, None))
        self.assertEqual(grant["verification_reset_at"], T2)
        self.assertIn("Funding", grant["verification_reset_reason"])
        self.assertEqual((grant["last_changed_at"], grant["last_checked_at"]), (T2, T2))
        self.assertEqual(self.kinds("Youth Skills Fund"), ["found", "updated", "verification_reset"])
        reset = next(e for e in self.repo.list_grant_events(grant_id) if e["kind"] == "verification_reset")
        self.assertEqual(reset["detail"]["previously_verified_by"], "Test verifier")
        change = next(e for e in self.repo.list_grant_events(grant_id) if e["kind"] == "updated")["detail"]["changes"][0]
        self.assertEqual((change["field"], change["old"], change["new"]), ("funding", "£5,000 to £40,000", "£5,000 to £25,000"))
        self.assertEqual(len(self.repo.list_grant_snapshots(grant_id)), 2)

    def test_formatting_changes_alone_leave_verification_and_text_untouched(self):
        self.run_source(T1)
        grant_id = self.verified("Youth Skills Fund")
        stored = self.repo.get_grant(grant_id)
        pages = self.edited(YOUTH, "Registered charities working with young people aged 16 to 24 in England.",
                            "\u2022 REGISTERED charities working with young people aged 16 to 24 in England.")
        summary = self.run_source(T2, pages)
        grant = self.repo.get_grant(grant_id)
        self.assertEqual((summary.updated, summary.reset, summary.reformatted), ([], [], [grant_id]))
        self.assertEqual(grant["verification_status"], "verified")
        self.assertEqual(grant["eligibility"], stored["eligibility"])
        self.assertEqual((grant["last_changed_at"], grant["last_checked_at"]), (T1, T2))
        self.assertEqual(len(self.repo.list_grant_snapshots(grant_id)), 1)
        self.assertEqual(self.kinds("Youth Skills Fund"), ["found", "reformatted"])

    def test_a_real_change_to_an_unverified_grant_is_saved_without_a_reset(self):
        self.run_source(T1)
        summary = self.run_source(T2, self.edited(DIGITAL, "Up to £15,000", "Up to £12,000"))
        self.assertEqual((len(summary.updated), summary.reset), (1, []))
        self.assertEqual(self.grant("Digital Inclusion Grant")["funding"], "Up to £12,000")
        self.assertEqual(self.kinds("Digital Inclusion Grant"), ["found", "updated"])

    def test_a_new_deadline_is_a_real_change(self):
        self.run_source(T1)
        grant_id = self.verified("Digital Inclusion Grant")
        self.run_source(T2, self.edited(DIGITAL, "30/06/2027", "30/09/2027"))
        grant = self.repo.get_grant(grant_id)
        self.assertEqual((grant["deadline_date"], grant["verification_status"]), ("2027-09-30", "unverified"))

    def test_a_person_can_verify_again_after_a_reset(self):
        self.run_source(T1)
        grant_id = self.verified("Youth Skills Fund")
        self.run_source(T2, self.edited(YOUTH, "£5,000 to £40,000", "£5,000 to £25,000"))
        grants.mark_verified(self.repo, grant_id, "Second check")
        self.assertEqual(self.repo.get_grant(grant_id)["verified_by"], "Second check")

    def test_hand_entered_grants_are_never_changed_by_discovery(self):
        mine = grants.create_grant(self.repo, {"title": "Youth Skills Fund", "funder": FUNDER, "funding": "My own note"})
        mine = grants.mark_verified(self.repo, mine["id"], "Me")
        summary = self.run_source(T1)
        self.assertIn(mine["id"], summary.seen)
        self.assertEqual(self.repo.get_grant(mine["id"]), mine)
        self.assertEqual(self.repo.list_grant_events(mine["id"]), [])


class NeverCloseOnAbsenceTests(Harness):
    def setUp(self):
        super().setUp()
        self.run_source(T1)

    def without(self, url):
        pages = saved_pages()
        pages[f"{BASE}/grants"] = pages[f"{BASE}/grants"].replace(f'<li><a class="grant" href="{url[len(BASE):]}">', '<li hidden><a class="x" href="#">')
        return pages

    def test_a_grant_missing_from_a_complete_listing_is_flagged_not_closed(self):
        summary = self.run_source(T2, self.without(DIGITAL))
        grant = self.grant("Digital Inclusion Grant")
        self.assertEqual([item[0] for item in summary.flagged], [grant["id"]])
        self.assertEqual((grant["status"], grant["review_reason"], grant["review_flagged_at"]), ("open", "This grant is no longer in the source's list. It may have closed or moved; check the source", T2))
        self.assertIn("flagged_for_review", self.kinds("Digital Inclusion Grant"))

    def test_a_flag_is_raised_once_and_cleared_when_the_grant_returns(self):
        self.run_source(T2, self.without(DIGITAL))
        repeat = self.run_source(T3, self.without(DIGITAL))
        self.assertEqual(repeat.flagged, [])
        self.assertEqual(self.kinds("Digital Inclusion Grant").count("flagged_for_review"), 1)
        back = self.run_source(T4)
        grant = self.grant("Digital Inclusion Grant")
        self.assertEqual((back.cleared, grant["review_reason"], grant["status"]), ([grant["id"]], None, "open"))
        self.assertEqual(self.kinds("Digital Inclusion Grant")[-1], "review_cleared")

    def test_a_grant_page_that_has_gone_is_flagged_not_closed(self):
        pages = saved_pages()
        pages[DIGITAL] = 404
        self.run_source(T2, pages)
        grant = self.grant("Digital Inclusion Grant")
        self.assertEqual(grant["status"], "open")
        self.assertIn("page for this grant has gone", grant["review_reason"])

    def test_a_failed_fetch_changes_nothing_and_flags_nothing(self):
        def state():
            return [(g["id"], g["status"], g["verification_status"], g["review_reason"], g["funding"]) for g in self.repo.list_grants()]
        before = state()
        for web in (FakeWeb(down=True), FakeWeb({**saved_pages(), DIGITAL: 503})):
            summary = self.run_source(T2, web=web)
            self.assertIn(summary.status, ("failed", "partial"))
            self.assertEqual(summary.flagged, [])
        self.assertEqual(state(), before)
        self.assertEqual(self.repo.list_grants_to_review(), [])

    def test_a_partial_run_cut_short_by_the_page_cap_flags_nothing(self):
        capped = PoliteFetcher(BASE, max_pages=2, opener=FakeWeb(self.without(DIGITAL)), clock=FakeClock().clock, sleep=lambda seconds: None, agent="test-agent")
        summary = discovery.run(self.repo, "fictional-funder", adapters=fictional_source.ADAPTERS, fetcher=capped, now=T2)
        self.assertEqual((summary.status, summary.flagged), ("partial", []))
        self.assertEqual(self.repo.list_grants_to_review(), [])

    def test_a_dry_run_reports_flags_but_writes_none(self):
        summary = self.run_source(T2, self.without(DIGITAL), dry_run=True)
        self.assertEqual(len(summary.flagged), 1)
        self.assertEqual(self.repo.list_grants_to_review(), [])

    def test_a_person_can_dismiss_a_flag_or_close_the_grant(self):
        self.run_source(T2, self.without(DIGITAL))
        self.run_source(T2, self.without(VOICES))
        digital, voices = self.grant("Digital Inclusion Grant"), self.grant("Community Voices Grant")
        kept = grants.dismiss_review(self.repo, digital["id"], T3)
        self.assertEqual((kept["review_reason"], kept["status"]), (None, "open"))
        closed = grants.close_grant(self.repo, voices["id"], T3)
        self.assertEqual((closed["status"], closed["review_reason"]), ("closed", None))
        self.assertEqual(self.kinds("Community Voices Grant")[-1], "closed_by_person")
        with self.assertRaises(LookupError):
            grants.close_grant(self.repo, 9999)


class ClosingTests(Harness):
    def test_a_grant_the_source_shows_as_closed_is_closed_with_a_logged_reason(self):
        self.run_source(T1)
        pages = self.edited(YOUTH, "</dl>", '<dt>Status</dt><dd class="status">closed</dd></dl>')
        summary = self.run_source(T2, pages)
        grant = self.grant("Youth Skills Fund")
        self.assertEqual((grant["status"], summary.closed), ("closed", [grant["id"]]))
        self.assertEqual(self.kinds("Youth Skills Fund")[-1], "closed_by_deadline")
        self.assertEqual(grant["verification_status"], "unverified")

    def test_a_closed_grant_is_never_reopened_automatically(self):
        self.run_source(T1)
        grant_id = self.grant("Digital Inclusion Grant")["id"]
        grants.close_grant(self.repo, grant_id, T2)
        summary = self.run_source(T3, self.edited(DIGITAL, "30/06/2027", "31/12/2027"))
        grant = self.repo.get_grant(grant_id)
        self.assertEqual((grant["status"], grant["deadline_date"]), ("closed", "2027-12-31"))
        self.assertIn("different closing date", grant["review_reason"])
        self.assertEqual([item[0] for item in summary.flagged], [grant_id])

    def test_a_closed_grant_the_source_still_lists_unchanged_stays_quiet(self):
        self.run_source(T1)
        grant_id = self.grant("Digital Inclusion Grant")["id"]
        grants.close_grant(self.repo, grant_id, T2)
        summary = self.run_source(T3)
        self.assertEqual((summary.flagged, self.repo.get_grant(grant_id)["status"]), ([], "closed"))


class HousekeepingTests(Harness):
    def test_closes_only_grants_with_a_passed_closing_date(self):
        past = grants.create_grant(self.repo, {"title": "Old Fund", "funder": "A", "deadline_date": "2026-01-31", "status": "open"})
        today = grants.create_grant(self.repo, {"title": "Today Fund", "funder": "A", "deadline_date": "2026-10-29", "status": "open"})
        future = grants.create_grant(self.repo, {"title": "Future Fund", "funder": "A", "deadline_date": "2027-01-31", "status": "open"})
        undated = grants.create_grant(self.repo, {"title": "Rolling Fund", "funder": "A", "deadline_text": "Open all year", "status": "open"})
        closed = grants.close_expired(self.repo, date(2026, 10, 29), T1)
        self.assertEqual(closed, [past["id"]])
        statuses = {g["title"]: g["status"] for g in self.repo.list_grants()}
        self.assertEqual(statuses, {"Old Fund": "closed", "Today Fund": "open", "Future Fund": "open", "Rolling Fund": "open"})
        self.assertEqual(self.repo.list_grant_events(past["id"])[0]["kind"], "closed_by_deadline")
        self.assertEqual(grants.close_expired(self.repo, date(2026, 10, 29), T2), [])
        self.assertEqual(grants.close_expired(self.repo, date(2026, 10, 30), T2), [today["id"]])
        for grant in (future, undated):
            self.assertEqual(self.repo.get_grant(grant["id"])["status"], "open")


class DryRunTests(Harness):
    def test_a_dry_run_reports_changes_and_writes_nothing(self):
        self.run_source(T1)
        grants.mark_verified(self.repo, self.grant("Youth Skills Fund")["id"], "Test verifier")
        before = (self.repo.list_grants(), self.repo.list_discovery_runs())
        summary = self.run_source(T2, self.edited(YOUTH, "£5,000 to £40,000", "£5,000 to £25,000"), dry_run=True)
        self.assertEqual(len(summary.updated), 1)
        self.assertEqual((self.repo.list_grants(), self.repo.list_discovery_runs()), before)
        self.assertEqual(self.kinds("Youth Skills Fund"), ["found"])


if __name__ == "__main__":
    unittest.main()
