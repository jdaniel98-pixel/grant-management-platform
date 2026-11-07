"""Alert contacts, consent, relevance, once-only digests, delivery failures and isolation between charities."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from notify_helpers import LONG_AGO, MONDAY, NEXT_MONDAY, T0, TUESDAY, World, make_grant, result
from grant_platform.services import ValidationError, delivery, grants, notifications


class ContactTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.repo = self.world.repo

    def test_name_and_email_are_validated(self):
        for name, email, field in (("", "a@b.example", "name"), ("Al <x>", "a@b.example", "name"), ("Al", "", "email"), ("Al", "not-an-email", "email"),
                                   ("Al", "a@b.example\nBcc: boss@example.org", "email"), ("Al", "a b@c.example", "email"), ("Al", "a@b", "email")):
            with self.assertRaises(ValidationError, msg=email) as caught:
                notifications.add_contact(self.repo, self.world.a, name, email)
            self.assertIn(field, caught.exception.errors)

    def test_addresses_are_lower_cased_and_unique_per_organisation(self):
        contact = notifications.add_contact(self.repo, self.world.a, "  Sam   Lee ", "SAM@Example.org")
        self.assertEqual((contact["name"], contact["email"]), ("Sam Lee", "sam@example.org"))
        with self.assertRaises(ValidationError):
            notifications.add_contact(self.repo, self.world.a, "Sam Again", "sam@example.org")
        other = notifications.add_contact(self.repo, self.world.b, "Sam Lee", "sam@example.org")  # another charity may have the same person
        self.assertNotEqual(other["unsubscribe_token"], contact["unsubscribe_token"])

    def test_no_consent_means_not_emailable_until_recorded(self):
        contact = notifications.add_contact(self.repo, self.world.a, "Sam Lee", "sam@example.org", now=T0)
        self.assertFalse(notifications.emailable(contact))
        self.assertIsNone(contact["consent_given_at"])
        recorded = notifications.record_consent(self.repo, self.world.a, contact["id"], now="2026-10-05T09:00:00Z")
        self.assertTrue(notifications.emailable(recorded))
        self.assertEqual((recorded["consent_given_at"], recorded["alerts_since"], recorded["consent_wording"]),
                         ("2026-10-05T09:00:00Z", "2026-10-05T09:00:00Z", notifications.CONSENT_WORDING))

    def test_consent_is_recorded_with_who_when_and_wording(self):
        contact = self.world.contact_a
        self.assertEqual((contact["consent_given_at"], contact["consent_recorded_by"], contact["alerts_since"]), (T0, notifications.RECORDER, T0))

    def test_unsubscribe_by_code_stops_everything_and_unknown_codes_do_nothing(self):
        contact = self.world.contact_a
        self.assertIsNone(notifications.unsubscribe(self.repo, "wrong-code"))
        self.assertIsNone(notifications.unsubscribe(self.repo, ""))
        stopped = notifications.unsubscribe(self.repo, contact["unsubscribe_token"], now="2026-10-12T09:00:00Z")
        self.assertEqual((stopped["active"], stopped["unsubscribed_at"], notifications.emailable(stopped)), (False, "2026-10-12T09:00:00Z", False))
        self.assertTrue(notifications.emailable(self.world.fresh(self.world.contact_b)))

    def test_consent_again_after_unsubscribing_restarts_from_that_day(self):
        contact = self.world.contact_a
        notifications.unsubscribe(self.repo, contact["unsubscribe_token"], now="2026-10-12T09:00:00Z")
        back = notifications.record_consent(self.repo, self.world.a, contact["id"], now="2026-10-20T09:00:00Z")
        self.assertEqual((notifications.emailable(back), back["alerts_since"]), (True, "2026-10-20T09:00:00Z"))

    def test_preferences_are_validated(self):
        contact = self.world.contact_a
        for values in ({"alerts_frequency": "hourly"}, {"digest_weekday": 7}, {"min_band": "Poor fit"}, {"active": False}, {"consent_given_at": "x"}):
            with self.assertRaises(ValidationError, msg=values):
                notifications.update_preferences(self.repo, self.world.a, contact["id"], **values)
        changed = notifications.update_preferences(self.repo, self.world.a, contact["id"], alerts_frequency="daily", min_band="Strong fit", verified_only=True)
        self.assertEqual((changed["alerts_frequency"], changed["min_band"], changed["verified_only"]), ("daily", "Strong fit", True))


class RelevanceTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.repo = self.world.repo

    def titles(self, contact=None, today=MONDAY):
        contact = self.world.fresh(contact or self.world.contact_a)
        return [grant["title"] for _, grant, _ in notifications.relevant_matches(self.repo, contact, today)]

    def offer(self, title, **fields):
        grant = make_grant(self.repo, title)
        self.world.match(self.world.a, self.world.a_need, grant, **fields)
        return grant

    def test_strong_and_potential_fits_that_are_eligible_or_unclear(self):
        self.offer("Strong One")
        self.offer("Potential One", band="Potential fit", total=15)
        self.offer("Unclear One", eligibility="unclear")
        self.assertEqual(sorted(self.titles()), ["Potential One", "Strong One", "Unclear One"])

    def test_poor_fits_ineligible_and_rejected_recommendations_are_left_out(self):
        self.offer("Poor", band="Poor fit", total=5, recommendation="Reject")
        self.offer("Ineligible", eligibility="ineligible", recommendation="Reject")
        self.offer("Rejected But Strong", recommendation="Reject")
        self.assertEqual(self.titles(), [])

    def test_preferences_narrow_the_list(self):
        self.offer("Strong One")
        self.offer("Potential One", band="Potential fit", total=15)
        self.offer("Unclear One", eligibility="unclear")
        notifications.update_preferences(self.repo, self.world.a, self.world.contact_a["id"], min_band="Strong fit", include_unclear=False)
        self.assertEqual(self.titles(), ["Strong One"])

    def test_verified_only(self):
        unverified = self.offer("Unverified One")
        checked = self.offer("Checked One")
        grants.mark_verified(self.repo, checked["id"], "Test verifier")
        notifications.update_preferences(self.repo, self.world.a, self.world.contact_a["id"], verified_only=True)
        self.assertEqual(self.titles(), ["Checked One"])
        self.assertNotIn(unverified["title"], self.titles())

    def test_closed_and_expired_grants_are_left_out(self):
        closed = self.offer("Closed One")
        self.repo.set_grant_status(closed["id"], "closed")
        self.offer("Open One")
        old = self.offer("Expired One")
        self.repo.connection.execute("UPDATE grant_record SET deadline_date = '2026-01-31' WHERE id = ?", (old["id"],))
        self.assertEqual(self.titles(), ["Open One"])

    def test_grants_found_before_consent_are_not_a_backlog(self):
        self.offer("Found Before")
        late = notifications.update_preferences(self.repo, self.world.a, self.world.contact_a["id"], alerts_since="2026-10-20T00:00:00Z")
        self.assertEqual(self.titles(late), [])

    def test_grants_the_organisation_already_acted_on_are_left_out(self):
        saved = self.offer("Saved One")
        rejected = self.offer("Rejected One")
        self.offer("Fresh One")
        self.repo.create_application(self.world.a, self.world.a_need, saved["id"], "saved")
        self.repo.create_application(self.world.a, self.world.a_need, rejected["id"], "rejected")
        self.assertEqual(self.titles(), ["Fresh One"])

    def test_one_entry_per_grant_even_with_several_funding_needs(self):
        from grant_platform.models import FundingNeed
        self.repo.add_funding_need(self.world.a, FundingNeed(title="Second Need", kind="project", description="More"))
        second = self.repo.get_funding_need_by_title(self.world.a, "Second Need")["id"]
        grant = make_grant(self.repo, "Shared One")
        self.world.match(self.world.a, self.world.a_need, grant, band="Potential fit", total=14)
        self.world.match(self.world.a, second, grant, total=23)
        items = notifications.relevant_matches(self.repo, self.world.fresh(self.world.contact_a), MONDAY)
        self.assertEqual([(g["title"], n["title"], m["suitability_total"]) for m, g, n in items], [("Shared One", "Second Need", 23)])

    def test_best_first(self):
        self.offer("Low", band="Potential fit", total=12)
        self.offer("High", total=24)
        self.offer("Unclear High", eligibility="unclear", total=25)
        self.assertEqual(self.titles(), ["High", "Low", "Unclear High"])  # eligible before unclear, then higher suitability


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.repo = self.world.repo
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.outbox = Path(self.folder.name)
        self.deliver = delivery.outbox_channel(self.outbox)

    def offer(self, title, organisation=None, **fields):
        organisation = organisation or "a"
        organisation_id, need_id = (self.world.a, self.world.a_need) if organisation == "a" else (self.world.b, self.world.b_need)
        grant = make_grant(self.repo, title)
        self.world.match(organisation_id, need_id, grant, **fields)
        return grant

    def send(self, today=MONDAY, **options):
        return notifications.send_digests(self.repo, self.deliver, today, now=f"{today.isoformat()}T07:00:00Z", **options)

    def files(self):
        return sorted(path.name for path in self.outbox.glob("*.txt"))

    def test_weekly_digest_goes_out_once_on_the_chosen_day_and_not_before(self):
        self.offer("Alpha Fund")
        notifications.update_preferences(self.repo, self.world.a, self.world.contact_a["id"], digest_weekday=2)  # Wednesday
        self.assertEqual(self.send(MONDAY), [])
        self.assertEqual(self.send(TUESDAY), [])
        wednesday = date(2026, 10, 28)
        self.assertEqual(len(self.send(wednesday)), 1)
        self.assertEqual(self.send(wednesday), [])
        self.assertEqual(self.send(date(2026, 10, 30)), [])  # later the same week

    def test_a_missed_day_is_caught_up_within_the_week(self):
        self.offer("Alpha Fund")
        self.assertEqual(len(self.send(TUESDAY)), 1)  # Monday was missed

    def test_nothing_new_means_no_message_and_the_week_is_not_used_up(self):
        self.assertEqual(self.send(MONDAY), [])
        self.assertEqual(self.files(), [])
        self.offer("Late Fund")
        self.assertEqual(len(self.send(TUESDAY)), 1)

    def test_each_grant_is_offered_to_a_contact_only_once(self):
        self.offer("Alpha Fund")
        self.send(MONDAY)
        self.assertEqual(self.send(NEXT_MONDAY), [])
        self.offer("Beta Fund")
        done = self.send(NEXT_MONDAY)
        text = (self.outbox / done[0][0]).read_text(encoding="utf-8")
        self.assertIn("Beta Fund", text)
        self.assertNotIn("Alpha Fund", text)
        self.assertEqual(len(self.repo.list_messages(self.world.a, kind="grant_alert")), 2)

    def test_digest_is_capped_and_the_rest_wait_for_the_next_one(self):
        for number in range(17):
            self.offer(f"Fund {number:02d}", total=25 - number // 2)
        first = self.send(MONDAY)
        text = (self.outbox / first[0][0]).read_text(encoding="utf-8")
        self.assertIn("2 more", text)
        self.assertEqual(len(self.repo.list_messages(self.world.a, kind="grant_alert")), notifications.MAX_ITEMS)
        second = self.send(NEXT_MONDAY)
        self.assertEqual(len(second), 1)
        self.assertEqual(len(self.repo.list_messages(self.world.a, kind="grant_alert")), 17)

    def test_daily_frequency_sends_each_day_there_is_something_new(self):
        notifications.update_preferences(self.repo, self.world.a, self.world.contact_a["id"], alerts_frequency="daily")
        self.offer("Alpha Fund")
        self.assertEqual(len(self.send(MONDAY)), 1)
        self.assertEqual(self.send(MONDAY), [])
        self.assertEqual(self.send(TUESDAY), [])
        self.offer("Beta Fund")
        self.assertEqual(len(self.send(date(2026, 10, 28))), 1)

    def test_paused_unconsented_and_unsubscribed_contacts_get_nothing(self):
        self.offer("Alpha Fund")
        notifications.update_preferences(self.repo, self.world.a, self.world.contact_a["id"], alerts_frequency="off")
        self.assertEqual(self.send(), [])
        notifications.update_preferences(self.repo, self.world.a, self.world.contact_a["id"], alerts_frequency="weekly")
        notifications.add_contact(self.repo, self.world.a, "No Consent", "no@harbour.example")
        notifications.unsubscribe(self.repo, self.world.contact_a["unsubscribe_token"])
        self.assertEqual(self.send(), [])
        self.assertEqual(self.files(), [])

    def test_a_failed_delivery_logs_nothing_and_is_retried(self):
        self.offer("Alpha Fund")

        def broken(message, name):
            raise OSError("disk full")

        with self.assertRaises(OSError):
            notifications.send_digests(self.repo, broken, MONDAY, now=T0)
        self.assertEqual(self.repo.list_messages(self.world.a), [])
        self.assertEqual(len(self.send(MONDAY)), 1)
        self.assertEqual(len(self.repo.list_messages(self.world.a)), 2)  # the digest and its one grant

    def test_dry_run_reports_but_writes_and_logs_nothing(self):
        self.offer("Alpha Fund")
        done = self.send(MONDAY, dry_run=True)
        self.assertEqual(len(done), 1)
        self.assertEqual((self.files(), self.repo.list_messages(self.world.a)), ([], []))
        self.assertEqual(len(self.send(MONDAY)), 1)

    def test_the_message_is_plain_text_labelled_and_carries_the_unsubscribe_code(self):
        grant = self.offer("Alpha Fund", eligibility="unclear", total=17, summary="Fits the studio", missing=["Annual income", "Registered address", "Accounts", "Fourth gap"])
        done = self.send(MONDAY)
        text = (self.outbox / done[0][0]).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("To: Alex Harbour <alex@harbour.example>\nSubject: 1 new funding match for Harbour Youth Trust (fictional) (week of 26 October 2026)\n"))
        for expected in ("Alpha Fund", "Example Trust (fictional)", "For: Harbour Youth Studio", "Strong fit (17 out of 25)", "Eligibility unclear",
                         "Deadline: 2027-06-30", "Funding: Up to £20,000", "Why: Fits the studio", "To check: Annual income", "To check: Accounts",
                         "checked against the funder's page by a person: not yet", f"Source: {grant['canonical_url']}",
                         self.world.contact_a["unsubscribe_token"], "was not emailed"):
            self.assertIn(expected, text)
        self.assertNotIn("Fourth gap", text)
        self.assertNotIn("<", text.split("\n\n", 1)[1].replace("<alex@harbour.example>", ""))

    def test_a_verified_grant_says_so(self):
        grant = self.offer("Alpha Fund")
        grants.mark_verified(self.repo, grant["id"], "Test verifier")
        text = (self.outbox / self.send(MONDAY)[0][0]).read_text(encoding="utf-8")
        self.assertIn("by a person: yes (", text)

    def test_hostile_text_cannot_add_headers_or_files(self):
        grant = make_grant(self.repo, "Evil Fund", funder="Trust\nBcc: attacker@example.org")
        self.repo.connection.execute("UPDATE grant_record SET title = ? WHERE id = ?", ("Evil\nBcc: attacker@example.org\nFund", grant["id"]))
        self.repo.connection.commit()
        self.world.match(self.world.a, self.world.a_need, grant, summary="Nice\nBcc: attacker@example.org")
        done = self.send(MONDAY)
        text = (self.outbox / done[0][0]).read_text(encoding="utf-8")
        self.assertFalse(any(line.startswith("Bcc:") for line in text.splitlines()))
        self.assertRegex(done[0][0], r"^2026-10-26_digest_contact-\d+_week-2026-W44\.txt$")
        self.assertEqual(self.files(), [done[0][0]])


class IsolationTests(unittest.TestCase):
    """One charity must never see another's grants, needs, contacts or messages."""

    def setUp(self):
        self.world = World()
        self.addCleanup(self.world.close)
        self.repo = self.world.repo
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.outbox = Path(folder.name)
        self.only_a = make_grant(self.repo, "Harbour Only Fund")
        self.only_b = make_grant(self.repo, "Riverside Only Fund")
        self.both = make_grant(self.repo, "Shared Fund")
        self.world.match(self.world.a, self.world.a_need, self.only_a, summary="Perfect for the Harbour Youth Studio")
        self.world.match(self.world.b, self.world.b_need, self.only_b, summary="Perfect for the Riverside Mural Project")
        self.world.match(self.world.a, self.world.a_need, self.both, summary="Shared for Harbour")
        self.world.match(self.world.b, self.world.b_need, self.both, summary="Shared for Riverside")
        self.done = notifications.send_digests(self.repo, delivery.outbox_channel(self.outbox), MONDAY, now=T0)

    def text_for(self, address):
        name = next(name for name, _, to in self.done if address in to)
        return (self.outbox / name).read_text(encoding="utf-8")

    def test_each_digest_holds_only_its_own_organisations_material(self):
        self.assertEqual(len(self.done), 2)
        a, b = self.text_for("alex@harbour.example"), self.text_for("robin@riverside.example")
        for own, other in ((a, b), (b, a)):
            self.assertIn("Shared Fund", own)
        self.assertIn("Harbour Only Fund", a)
        self.assertIn("Shared for Harbour", a)
        self.assertIn("Harbour Youth Studio", a)
        for leaked in ("Riverside", "robin@", "Mural"):
            self.assertNotIn(leaked, a, leaked)
        self.assertIn("Riverside Only Fund", b)
        self.assertIn("Shared for Riverside", b)
        for leaked in ("Harbour", "alex@", "Youth Studio"):
            self.assertNotIn(leaked, b, leaked)

    def test_the_same_grant_is_offered_to_each_organisation_separately(self):
        for organisation_id, contact in ((self.world.a, self.world.contact_a), (self.world.b, self.world.contact_b)):
            self.assertEqual(self.repo.alerted_grant_ids(contact["id"]), {self.both["id"], self.only_a["id"] if organisation_id == self.world.a else self.only_b["id"]})

    def test_the_message_log_and_contacts_are_scoped_to_their_organisation(self):
        a_contacts = {c["email"] for c in self.repo.list_contacts(self.world.a)}
        self.assertEqual(a_contacts, {"alex@harbour.example"})
        self.assertIsNone(self.repo.get_contact(self.world.a, self.world.contact_b["id"]))
        self.assertTrue({m["contact_id"] for m in self.repo.list_messages(self.world.a)} == {self.world.contact_a["id"]})
        self.assertTrue({m["contact_id"] for m in self.repo.list_messages(self.world.b)} == {self.world.contact_b["id"]})

    def test_nothing_can_be_logged_or_changed_across_organisations(self):
        with self.assertRaises(LookupError):
            self.repo.log_message(self.world.a, self.world.contact_b["id"], "digest", "x", T0)
        self.assertFalse(self.repo.update_contact(self.world.a, self.world.contact_b["id"], {"name": "Hijacked"}, T0))
        self.assertEqual(self.world.fresh(self.world.contact_b)["name"], "Robin Riverside")
        with self.assertRaises(LookupError):
            notifications.update_preferences(self.repo, self.world.a, self.world.contact_b["id"], alerts_frequency="off")
        with self.assertRaises(LookupError):
            notifications.record_consent(self.repo, self.world.a, self.world.contact_b["id"])

    def test_relevance_uses_only_the_contacts_own_organisation(self):
        items = notifications.relevant_matches(self.repo, self.world.fresh(self.world.contact_a), TUESDAY)
        self.assertEqual(items, [])  # everything was already offered; and nothing from Riverside ever appears
        fresh = make_grant(self.repo, "Riverside Late Fund")
        self.world.match(self.world.b, self.world.b_need, fresh)
        self.assertEqual(notifications.relevant_matches(self.repo, self.world.fresh(self.world.contact_a), TUESDAY), [])
        self.assertEqual([g["title"] for _, g, _ in notifications.relevant_matches(self.repo, self.world.fresh(self.world.contact_b), TUESDAY)], ["Riverside Late Fund"])

    def test_unsubscribing_one_organisations_contact_leaves_the_other_alone(self):
        notifications.unsubscribe(self.repo, self.world.contact_a["unsubscribe_token"])
        later = make_grant(self.repo, "Another Fund")
        self.world.match(self.world.a, self.world.a_need, later)
        self.world.match(self.world.b, self.world.b_need, later)
        done = notifications.send_digests(self.repo, delivery.outbox_channel(self.outbox), NEXT_MONDAY, now=T0)
        self.assertEqual([to for _, _, to in done], ["Robin Riverside <robin@riverside.example>"])


if __name__ == "__main__":
    unittest.main()
