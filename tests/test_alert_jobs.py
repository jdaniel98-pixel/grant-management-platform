"""The match, digest and unsubscribe commands, automatic assessment, and the whole chain from discovery to digest (fictional data)."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fictional_source
from canned_ai import NO_CONFLICT, CannedGateway, scores
from fictional_source import BASE, SOURCE, FakeClock, FakeWeb, saved_pages
from notify_helpers import LONG_AGO, MONDAY, NEXT_MONDAY, T0, make_grant, result
from grant_platform import db, jobs, seed
from grant_platform.repository import Repository
from grant_platform.services import ai, delivery, discovery, matching, notifications
from grant_platform.services.matching import ModelAnalysis
from grant_platform.services.fetcher import PoliteFetcher


class AssessOutstandingTests(unittest.TestCase):
    def setUp(self):
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repo = Repository(self.connection)
        self.organisation_id, _ = seed.seed_brightpath(self.repo)
        self.scenarios, _ = seed.seed_scenario_grants(self.repo)
        self.needs = self.repo.list_funding_needs(self.organisation_id)

    def test_assesses_every_outstanding_pair_up_to_the_limit_and_reports_the_rest(self):
        gateway = CannedGateway()
        todo = matching.outstanding(self.repo, model="test-model")
        assessed, errors, remaining = matching.assess_outstanding(self.repo, gateway=gateway, model="test-model", limit=5)
        self.assertEqual((len(assessed), errors, remaining, len(gateway.calls)), (5, {}, len(todo) - 5, 5))

    def test_a_second_pass_does_nothing_and_a_changed_grant_is_assessed_again(self):
        gateway = CannedGateway()
        matching.assess_outstanding(self.repo, gateway=gateway, model="test-model", limit=1000)
        calls = len(gateway.calls)
        self.assertEqual(matching.outstanding(self.repo, model="test-model"), [])
        matching.assess_outstanding(self.repo, gateway=gateway, model="test-model", limit=1000)
        self.assertEqual(len(gateway.calls), calls)
        youth = self.repo.get_grant(self.scenarios[1])
        self.repo.update_grant_details(youth["id"], {"funding": "A different amount"}, T0)
        again = matching.outstanding(self.repo, model="test-model")
        self.assertEqual({grant["id"] for _, _, grant in again}, {youth["id"]})

    def test_closed_expired_and_rejected_grants_are_skipped(self):
        closed = self.repo.get_grant(self.scenarios[1])
        self.repo.set_grant_status(closed["id"], "closed")
        rejected = self.repo.get_grant(self.scenarios[2])
        need = self.needs[0]
        self.repo.create_application(self.organisation_id, need["id"], rejected["id"], "rejected")
        listed = {(need_["id"], grant["id"]) for _, need_, grant in matching.outstanding(self.repo, model="test-model")}
        self.assertNotIn((need["id"], closed["id"]), listed)
        self.assertNotIn((need["id"], rejected["id"]), listed)
        self.assertIn((self.needs[1]["id"], rejected["id"]), listed)

    def test_one_failure_does_not_stop_the_others(self):
        def flaky(payload):
            if payload["grant"]["title"] == "Youth Employment Fund":
                raise matching.GatewayError("temporary problem")
            from canned_ai import analysis_for
            return analysis_for(payload)

        assessed, errors, _ = matching.assess_outstanding(self.repo, gateway=CannedGateway(flaky), model="test-model", limit=1000)
        self.assertTrue(assessed)
        self.assertTrue(errors)
        self.assertTrue(all("did not return a usable assessment" in message for message in errors.values()))


class CommandTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.db_path = str(self.folder / "test.sqlite3")
        connection = db.connect(self.db_path)
        self.repo = Repository(connection)
        self.addCleanup(connection.close)
        self.organisation_id, _ = seed.seed_brightpath(self.repo)
        self.need_id = self.repo.list_funding_needs(self.organisation_id)[0]["id"]
        self.contact = notifications.add_contact(self.repo, self.organisation_id, "Casey Bright", "casey@brightpath.example", consent_given=True, now=LONG_AGO)
        self.grant = make_grant(self.repo, "Command Fund")
        self.repo.save_match(self.organisation_id, self.need_id, self.grant["id"], result())

    def command(self, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = jobs.main([*args, "--db", self.db_path], **kwargs)
        return code, out.getvalue(), err.getvalue()

    def test_digest_dry_run_then_real_then_once_only(self):
        code, out, _ = self.command("digest", "--today", "2026-10-26", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Would write 1 digest", out)
        self.assertEqual(list((self.folder / "outbox").glob("*")), [])
        code, out, _ = self.command("digest", "--today", "2026-10-26")
        self.assertIn("Wrote 1 digest", out)
        self.assertIn("casey@brightpath.example", out)
        files = list((self.folder / "outbox").glob("*.txt"))
        self.assertEqual(len(files), 1)
        self.assertIn("Command Fund", files[0].read_text(encoding="utf-8"))
        code, out, _ = self.command("digest", "--today", "2026-10-26")
        self.assertIn("Wrote 0 digests", out)

    def test_digest_reports_an_unwritable_outbox(self):
        blocker = self.folder / "blocked"
        blocker.write_text("a file, not a folder")
        code, _, err = self.command("digest", "--today", "2026-10-26", "--outbox", str(blocker / "inside"))
        self.assertEqual(code, 1)
        self.assertIn("Nothing was logged", err)
        self.assertEqual(self.repo.list_messages(self.organisation_id), [])

    def test_unsubscribe_command(self):
        code, out, _ = self.command("unsubscribe", self.contact["unsubscribe_token"])
        self.assertEqual(code, 0)
        self.assertIn("Casey Bright will not receive any more email", out)
        code, out, _ = self.command("digest", "--today", "2026-10-26")
        self.assertIn("Wrote 0 digests", out)
        code, _, err = self.command("unsubscribe", "not-a-code")
        self.assertEqual(code, 1)
        self.assertIn("does not match", err)

    def test_match_without_a_key_assesses_nothing_and_says_why(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(ai, "ENV_PATH", self.folder / "no.env"):
            code, _, err = self.command("match")
        self.assertEqual(code, 1)
        self.assertIn("no OPENAI_API_KEY", err)

    def test_match_dry_run_makes_no_ai_calls(self):
        seed.seed_scenario_grants(self.repo)
        code, out, _ = self.command("match", "--dry-run", "--max-assessments", "3")
        self.assertEqual(code, 0)
        self.assertIn("Would assess 3 of", out)

    def test_match_with_a_gateway_assesses_and_reports(self):
        seed.seed_scenario_grants(self.repo)
        gateway = CannedGateway()
        args = jobs.argparse.Namespace(db=self.db_path, max_assessments=4, dry_run=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = jobs.run_match(args, gateway=gateway)
        self.assertEqual(code, 0)
        self.assertIn("Assessed 4 grants; 0 failed;", out.getvalue())
        self.assertEqual(len(gateway.calls), 4)


class WholeChainTests(unittest.TestCase):
    """Discovery finds a grant, matching assesses it, the digest tells the right contact once, and a later change is not re-sent."""

    def test_discovery_to_match_to_digest(self):
        connection = db.connect(":memory:")
        self.addCleanup(connection.close)
        repo = Repository(connection)
        organisation_id, _ = seed.seed_brightpath(repo)
        notifications.add_contact(repo, organisation_id, "Casey Bright", "casey@brightpath.example", consent_given=True, now=LONG_AGO)
        source = discovery.register_sources(repo, {"fictional-funder": SOURCE})[0]
        repo.set_source_enabled(source["id"], True)
        clock = FakeClock()
        fetcher = PoliteFetcher(BASE, opener=FakeWeb(saved_pages()), clock=clock.clock, sleep=clock.sleep, agent="test-agent")
        discovery.run(repo, "fictional-funder", adapters=fictional_source.ADAPTERS, fetcher=fetcher, now="2026-10-26T06:00:00Z")

        def behaviour(payload):
            strong = payload["grant"]["title"] in ("Youth Skills Fund", "Digital Inclusion Grant")
            return ModelAnalysis.model_validate({
                "eligibility_items": [], "suitability": scores(*((5, 5, 4, 4, 4) if strong else (1, 1, 1, 1, 1))),
                "exclusion_conflict": NO_CONFLICT, "summary": "Canned match" if strong else "Weak", "uncertainties": [], "missing_information": []})

        assessed, errors, _ = matching.assess_outstanding(repo, gateway=CannedGateway(behaviour), model="test-model", limit=1000, today=MONDAY)
        self.assertEqual(errors, {})
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        deliver = delivery.outbox_channel(folder.name)
        first = notifications.send_digests(repo, deliver, MONDAY, now="2026-10-26T07:00:00Z")
        self.assertEqual(len(first), 1)
        text = (Path(folder.name) / first[0][0]).read_text(encoding="utf-8")
        self.assertIn("Youth Skills Fund", text)
        self.assertNotIn("Community Voices Grant", text)  # a weak fit is not an alert
        self.assertIn("not yet", text)  # found by a source, never verified automatically
        # Next week the funder changes an amount: the same grant is not offered again.
        pages = saved_pages()
        pages[f"{BASE}/grants/youth-skills-fund"] = pages[f"{BASE}/grants/youth-skills-fund"].replace("£5,000 to £40,000", "£5,000 to £25,000")
        fetcher = PoliteFetcher(BASE, opener=FakeWeb(pages), clock=clock.clock, sleep=clock.sleep, agent="test-agent")
        discovery.run(repo, "fictional-funder", adapters=fictional_source.ADAPTERS, fetcher=fetcher, now="2026-11-02T06:00:00Z")
        self.assertEqual(notifications.send_digests(repo, deliver, NEXT_MONDAY, now="2026-11-02T07:00:00Z"), [])


if __name__ == "__main__":
    unittest.main()
