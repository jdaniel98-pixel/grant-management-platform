"""Grant rules: URL normalisation, duplicates, verification and the eight scenario grants (fictional data only)."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import unittest
from datetime import date

from grant_platform import db, seed
from grant_platform.repository import Repository
from grant_platform.services import ValidationError, grants

VALID = {"title": "Test Youth Fund", "funder": "Example Trust (fictional)"}
YOUTH_URL = "https://www.example.org/funds/youth-employment-fund"


class NormaliseUrlTests(unittest.TestCase):
    def test_different_spellings_of_one_page_match(self):
        variants = (
            YOUTH_URL, YOUTH_URL + "/", "http://example.org/funds/youth-employment-fund",
            "HTTPS://WWW.Example.ORG/funds/youth-employment-fund/?utm_source=newsletter&utm_medium=email#apply",
            "example.org/funds/youth-employment-fund", "  https://example.org:443/funds//youth-employment-fund/index.html  ",
        )
        self.assertEqual({grants.normalise_url(value) for value in variants}, {"https://example.org/funds/youth-employment-fund"})

    def test_different_pages_stay_different(self):
        self.assertNotEqual(grants.normalise_url("https://example.org/funds/a"), grants.normalise_url("https://example.org/funds/b"))
        self.assertNotEqual(grants.normalise_url("https://example.org/call?id=1"), grants.normalise_url("https://example.org/call?id=2"))
        self.assertNotEqual(grants.normalise_url("https://example.org/Funds"), grants.normalise_url("https://example.org/funds"))
        self.assertEqual(grants.normalise_url("https://example.org/call?b=2&a=1"), grants.normalise_url("https://example.org/call?a=1&b=2"))

    def test_unusable_addresses_are_refused(self):
        for bad in ("", "   ", "javascript:alert(1)", "ftp://example.org/x", "file:///etc/passwd", "https://localhost/x",
                    "not a url", "https://", "https://example.org:notaport/x", "mailto:a@example.org"):
            with self.assertRaises(ValueError, msg=bad):
                grants.normalise_url(bad)

    def test_dedupe_key_ignores_case_punctuation_and_spacing(self):
        self.assertEqual(grants.dedupe_key("Example Trust", "Youth  Fund!"), grants.dedupe_key("example trust.", "youth fund"))
        self.assertNotEqual(grants.dedupe_key("Example Trust", "Youth Fund"), grants.dedupe_key("Other Trust", "Youth Fund"))

    def test_deadline_parsing(self):
        self.assertEqual(grants.parse_deadline("31/03/2027"), "2027-03-31")
        self.assertEqual(grants.parse_deadline("2027-03-31"), "2027-03-31")
        self.assertIsNone(grants.parse_deadline("  "))
        for bad in ("31 March", "31/02/2027", "2027-13-01", "soon"):
            with self.assertRaises(ValueError, msg=bad):
                grants.parse_deadline(bad)


class CleanGrantTests(unittest.TestCase):
    def assertInvalid(self, raw, origin, *fields):
        with self.assertRaises(ValidationError) as caught:
            grants.clean_grant(raw, origin)
        self.assertEqual(sorted(caught.exception.errors), sorted(fields))

    def test_title_and_funder_are_required(self):
        self.assertInvalid({}, "manual", "title", "funder")
        self.assertInvalid({"title": "  ", "funder": "x"}, "manual", "title")

    def test_web_address_is_required_only_when_adding_by_address(self):
        self.assertInvalid(VALID, "url", "source_url")
        self.assertIsNone(grants.clean_grant(VALID, "manual").canonical_url)
        self.assertInvalid({**VALID, "source_url": "javascript:alert(1)"}, "manual", "source_url")

    def test_every_problem_is_reported_at_once(self):
        self.assertInvalid(
            {"status": "maybe", "deadline_date": "soon", "source_url": "nope nope", "title": "x" * 500},
            "url", "title", "funder", "source_url", "status", "deadline_date",
        )

    def test_text_limits_and_cleaning(self):
        self.assertInvalid({**VALID, "eligibility": "x" * (grants.LONG_LIMIT + 1)}, "manual", "eligibility")
        self.assertInvalid({**VALID, "scope": "x" * (grants.SHORT_LIMIT + 1)}, "manual", "scope")
        grant = grants.clean_grant({"title": "  Spaced   title ", "funder": "F", "purpose": "  line one  \n\n line two  "}, "manual")
        self.assertEqual((grant.title, grant.purpose, grant.status), ("Spaced title", "line one\n\n line two", "unknown"))

    def test_unsupported_origin(self):
        with self.assertRaises(ValueError):
            grants.clean_grant(VALID, "ai")

    def test_monitored_grants_need_their_source_page(self):
        self.assertInvalid(VALID, "monitored", "source_url")
        grant = grants.clean_grant({**VALID, "source_url": YOUTH_URL}, "monitored")
        self.assertEqual((grant.origin, grant.canonical_url), ("monitored", "https://example.org/funds/youth-employment-fund"))


class GrantStorageTests(unittest.TestCase):
    def setUp(self):
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repository = Repository(self.connection)

    def test_new_grant_is_unverified_with_provenance_and_dates(self):
        grant = grants.create_grant(self.repository, {**VALID, "source_url": YOUTH_URL + "?utm_campaign=x", "deadline_date": "31/03/2027"}, origin="url")
        self.assertEqual(grant["origin"], "url")
        self.assertEqual(grant["source_url"], YOUTH_URL + "?utm_campaign=x")
        self.assertEqual(grant["canonical_url"], "https://example.org/funds/youth-employment-fund")
        self.assertEqual(grant["verification_status"], "unverified")
        self.assertIsNone(grant["verified_by"])
        self.assertIsNone(grant["verified_at"])
        self.assertEqual(grant["deadline_date"], "2027-03-31")
        self.assertTrue(grant["first_seen_at"] and grant["last_seen_at"])

    def test_same_address_in_another_format_is_blocked_and_points_to_the_first(self):
        first = grants.create_grant(self.repository, {**VALID, "source_url": YOUTH_URL}, origin="url")
        with self.assertRaises(grants.DuplicateGrantError) as caught:
            grants.create_grant(self.repository, {"title": "Other title", "funder": "Other funder", "source_url": "http://example.org/funds/youth-employment-fund/?utm_source=x#top"}, origin="url")
        self.assertEqual(caught.exception.existing["id"], first["id"])
        self.assertIn(f"#{first['id']}", caught.exception.errors["source_url"])
        self.assertIn("Test Youth Fund", caught.exception.errors["source_url"])
        self.assertEqual(len(self.repository.list_grants()), 1)

    def test_same_funder_and_title_is_blocked_even_with_a_different_address_or_none(self):
        first = grants.create_grant(self.repository, {**VALID, "source_url": YOUTH_URL})
        for source_url in ("https://example.org/somewhere/else", ""):
            with self.assertRaises(grants.DuplicateGrantError) as caught:
                grants.create_grant(self.repository, {"title": "TEST youth fund.", "funder": "example trust (fictional)", "source_url": source_url})
            self.assertEqual(caught.exception.existing["id"], first["id"])
            self.assertIn("title", caught.exception.errors)

    def test_grants_without_an_address_can_coexist(self):
        grants.create_grant(self.repository, {**VALID, "title": "One"})
        grants.create_grant(self.repository, {**VALID, "title": "Two"})
        self.assertEqual(len(self.repository.list_grants()), 2)

    def test_mark_verified_records_who_and_when_and_keeps_the_first_verifier(self):
        grant = grants.create_grant(self.repository, VALID)
        verified = grants.mark_verified(self.repository, grant["id"])
        self.assertEqual(verified["verification_status"], "verified")
        self.assertEqual(verified["verified_by"], grants.DEVELOPMENT_VERIFIER)
        self.assertRegex(verified["verified_at"], r"^\d{4}-\d{2}-\d{2}T")
        again = grants.mark_verified(self.repository, grant["id"], verified_by="Someone else")
        self.assertEqual((again["verified_by"], again["verified_at"]), (verified["verified_by"], verified["verified_at"]))

    def test_mark_verified_unknown_grant(self):
        with self.assertRaises(LookupError):
            grants.mark_verified(self.repository, 999)

    def test_list_orders_by_deadline_then_undated_newest_first(self):
        late = grants.create_grant(self.repository, {**VALID, "title": "Late", "deadline_date": "2027-09-01"})
        early = grants.create_grant(self.repository, {**VALID, "title": "Early", "deadline_date": "2027-01-01"})
        none_old = grants.create_grant(self.repository, {**VALID, "title": "Undated old"})
        none_new = grants.create_grant(self.repository, {**VALID, "title": "Undated new"})
        self.assertEqual([g["id"] for g in self.repository.list_grants()], [early["id"], late["id"], none_new["id"], none_old["id"]])

    def test_deadline_label(self):
        grant = grants.create_grant(self.repository, {**VALID, "deadline_date": "2027-03-31", "deadline_text": "31 March 2027"})
        self.assertEqual(grants.deadline_label(grant, date(2027, 3, 24)), "31 March 2027 (in 7 days)")
        self.assertEqual(grants.deadline_label(grant, date(2027, 3, 31)), "31 March 2027 (today)")
        self.assertEqual(grants.deadline_label(grant, date(2027, 4, 1)), "31 March 2027 (passed 1 day ago)")
        self.assertEqual(grants.days_until_deadline(grant, date(2027, 3, 30)), 1)
        undated = grants.create_grant(self.repository, {**VALID, "title": "Undated"})
        self.assertEqual(grants.deadline_label(undated), "No deadline recorded")
        self.assertIsNone(grants.days_until_deadline(undated))


class ScenarioGrantTests(unittest.TestCase):
    def setUp(self):
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repository = Repository(self.connection)

    def test_eight_scenarios_exist_and_are_fictional(self):
        entries = seed.load_scenario_grants()
        self.assertEqual([entry["scenario"] for entry in entries], list(range(1, 9)))
        for entry in entries:
            self.assertIn("(fictional)", entry["funder"])
            self.assertIn("example.org", entry["source_url"])

    def test_seven_are_stored_and_scenario_eight_is_refused_as_a_duplicate_of_one(self):
        stored, refused = seed.seed_scenario_grants(self.repository)
        self.assertEqual(sorted(stored), [1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(refused, {8: stored[1]})
        self.assertEqual(len(self.repository.list_grants()), 7)
        for grant in self.repository.list_grants():
            self.assertEqual(grant["verification_status"], "unverified")

    def test_loading_twice_adds_nothing(self):
        seed.seed_scenario_grants(self.repository)
        stored, refused = seed.seed_scenario_grants(self.repository)
        self.assertEqual(stored, {})
        self.assertEqual(sorted(refused), list(range(1, 9)))
        self.assertEqual(len(self.repository.list_grants()), 7)

    def test_scenario_facts_later_steps_rely_on(self):
        seed.seed_scenario_grants(self.repository)
        by_title = {grant["title"]: grant for grant in self.repository.list_grants()}
        self.assertIn("Scotland", by_title["Scottish Youth Pathways"]["eligibility"])
        self.assertIn("501(c)(3)", by_title["American Youth Initiative"]["eligibility"])
        self.assertIn("£250,000", by_title["Small Charities Boost"]["eligibility"])
        self.assertIn("Core costs", by_title["Digital Skills for Young People"]["exclusions"])
        self.assertIn("audited accounts", by_title["Established Charities Grant"]["eligibility"])

    def test_grants_are_shared_not_owned_by_an_organisation(self):
        seed.seed_brightpath(self.repository)
        seed.seed_scenario_grants(self.repository)
        columns = [row["name"] for row in self.connection.execute("PRAGMA table_info(grant_record)")]
        self.assertNotIn("organisation_id", columns)


if __name__ == "__main__":
    unittest.main()
