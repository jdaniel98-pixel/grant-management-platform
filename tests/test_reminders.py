"""Deadline reminders written to a local outbox: schedule, no repeats, content, safety, CLI (fictional data only)."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import contextlib
import io
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from canned_ai import DIGITAL
from grant_platform import db, jobs, seed
from grant_platform.repository import Repository
from grant_platform.services import applications, profile, reminders

DEADLINE = date(2027, 3, 31)  # Youth Employment Fund, scenario 1


class ReminderTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.outbox = Path(self.folder.name) / "outbox"
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repository = Repository(self.connection)
        self.organisation_id, _ = seed.seed_brightpath(self.repository)
        self.stored, _ = seed.seed_scenario_grants(self.repository)
        self.needs = {need["title"]: need["id"] for need in self.repository.list_funding_needs(self.organisation_id)}
        self.application = applications.decide(self.repository, self.organisation_id, self.stored[1], self.needs[DIGITAL], "pursue")

    def run_on(self, day, **kwargs):
        return reminders.write_reminders(self.repository, self.outbox, day, **kwargs)

    def files(self):
        return sorted(path.name for path in self.outbox.glob("*")) if self.outbox.exists() else []


class ScheduleTests(ReminderTestCase):
    def test_a_reminder_is_written_on_each_threshold_day_only(self):
        for days_before, expected in ((40, 0), (29, 0), (28, 1), (27, 0), (21, 0), (14, 1), (10, 0), (7, 1), (3, 0), (2, 1), (1, 0), (0, 0)):
            written = self.run_on(DEADLINE - timedelta(days=days_before))
            self.assertEqual(len(written), expected, f"{days_before} days before")
        self.assertEqual(len(self.files()), 4)

    def test_rerunning_the_same_day_writes_nothing_more(self):
        day = DEADLINE - timedelta(days=7)
        self.assertEqual(len(self.run_on(day)), 1)
        self.assertEqual(self.run_on(day), [])
        self.assertEqual(self.run_on(day + timedelta(hours=0)), [])
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(len(self.repository.list_notifications(self.organisation_id, self.application["id"])), 3)  # 28 and 14 were already passed, 7 sent

    def test_a_missed_day_sends_the_nearest_passed_threshold_once_and_not_the_earlier_ones(self):
        written = self.run_on(DEADLINE - timedelta(days=3))
        self.assertEqual(len(written), 1)
        self.assertIn("in 3 days", written[0][1])
        self.assertIn("7-days", written[0][0])
        self.assertEqual(self.run_on(DEADLINE - timedelta(days=3)), [])
        self.assertEqual(self.run_on(DEADLINE - timedelta(days=2))[0][0].split("_")[-1], "2-days.txt")

    def test_the_deadline_day_itself_gets_a_reminder_if_the_two_day_one_was_missed(self):
        written = self.run_on(DEADLINE)
        self.assertEqual(len(written), 1)
        self.assertIn("Deadline today", written[0][1])
        self.assertEqual(self.run_on(DEADLINE + timedelta(days=1)), [])

    def test_no_reminders_after_the_deadline_or_without_one(self):
        self.assertEqual(self.run_on(DEADLINE + timedelta(days=5)), [])
        undated = applications.decide(self.repository, self.organisation_id, self.stored[2], None, "pursue")
        self.connection.execute("UPDATE grant_record SET deadline_date = NULL WHERE id = ?", (self.stored[2],))
        self.assertEqual([name for name, _ in self.run_on(DEADLINE - timedelta(days=7)) if f"application-{undated['id']}_" in name], [])

    def test_only_work_in_progress_gets_reminders(self):
        day = DEADLINE - timedelta(days=7)
        for status in ("submitted", "awarded", "unsuccessful", "withdrawn"):
            self.connection.execute("UPDATE application SET status = ? WHERE id = ?", (status, self.application["id"]))
            self.assertEqual(self.run_on(day), [], status)
        for status in ("saved", "drafting", "pursuing"):
            self.connection.execute("DELETE FROM notification_log")
            self.connection.execute("UPDATE application SET status = ? WHERE id = ?", (status, self.application["id"]))
            self.assertEqual(len(self.run_on(day)), 1, status)
        applications.decide(self.repository, self.organisation_id, self.stored[3], None, "reject")
        self.assertEqual(self.run_on(date(2027, 2, 8)), [])  # 7 days before the rejected grant's 15 February deadline

    def test_custom_days(self):
        self.assertEqual(len(self.run_on(DEADLINE - timedelta(days=5), days=(5,))), 1)
        self.assertEqual(self.run_on(DEADLINE - timedelta(days=7), days=(5,)), [])

    def test_two_organisations_get_separate_reminders(self):
        other_id = profile.create_organisation(self.repository, {"name": "Riverside Test Charity (fictional)", "mission": "Testing."})
        other = applications.decide(self.repository, other_id, self.stored[1], None, "pursue")
        written = self.run_on(DEADLINE - timedelta(days=7))
        self.assertEqual(len(written), 2)
        texts = {name: (self.outbox / name).read_text(encoding="utf-8") for name, _ in written}
        riverside = texts[reminders.outbox_name(DEADLINE - timedelta(days=7), other, 7)]
        brightpath = texts[reminders.outbox_name(DEADLINE - timedelta(days=7), self.application, 7)]
        self.assertIn("Riverside Test Charity", riverside)
        self.assertNotIn("BrightPath", riverside)
        self.assertNotIn("Riverside", brightpath)
        self.assertEqual(len(self.repository.list_notifications(other_id)), 3)
        self.assertEqual(len(self.repository.list_notifications(self.organisation_id)), 3)


class ContentAndSafetyTests(ReminderTestCase):
    def test_the_reminder_describes_the_application_and_says_it_was_not_emailed(self):
        applications.add_task(self.repository, self.organisation_id, self.application["id"], "Ask trustees for approval", "15/03/2027")
        applications.add_task(self.repository, self.organisation_id, self.application["id"], "Done already")
        done = self.repository.list_tasks(self.organisation_id, self.application["id"])
        finished = next(task for task in done if task["text"] == "Done already")
        applications.set_task_done(self.repository, self.organisation_id, self.application["id"], finished["id"], True)
        first = self.repository.list_requirements(self.organisation_id, self.application["id"])[0]
        applications.set_requirement_done(self.repository, self.organisation_id, self.application["id"], first["id"], True)
        name, subject = self.run_on(DEADLINE - timedelta(days=7))[0]
        text = (self.outbox / name).read_text(encoding="utf-8")
        self.assertEqual(subject, "Deadline in 7 days: Youth Employment Fund (BrightPath Youth Foundation)")
        for expected in (f"Subject: {subject}", "To: (no recipient", "Funder: Northern Futures Trust (fictional)", f"For: {DIGITAL}",
                         "Deadline: 31 March 2027 (in 7 days)", "Status: Pursuing", "Requirements ticked: 1 of 5", "Open tasks: 1",
                         "  - Ask trustees for approval (due 2027-03-15)", "It was not emailed."):
            self.assertIn(expected, text)
        self.assertNotIn("Done already", text)

    def test_a_title_with_line_breaks_cannot_add_headers(self):
        application = {**self.application, "grant_title": "Fund\nBcc: someone@example.org", "deadline_text": None}
        organisation = self.repository.get_organisation(self.organisation_id)
        subject, body = reminders.render(organisation, application, [], [], 7)
        self.assertNotIn("\n", subject)
        self.assertEqual([line for line in body.splitlines() if line.startswith("Bcc:")], [])

    def test_file_names_are_built_from_numbers_not_user_text(self):
        self.connection.execute("UPDATE grant_record SET title = '../../etc/passwd', funder = 'x/y' WHERE id = ?", (self.stored[1],))
        name, _ = self.run_on(DEADLINE - timedelta(days=7))[0]
        self.assertRegex(name, r"^2027-03-24_application-\d+_7-days\.txt$")
        self.assertEqual(self.files(), [name])

    def test_no_email_is_sent_and_no_network_is_used(self):
        import smtplib
        refuse = AssertionError("network used")
        with patch.object(smtplib.SMTP, "__init__", side_effect=refuse), patch("socket.getaddrinfo", side_effect=refuse), \
                patch("socket.create_connection", side_effect=refuse):
            self.assertEqual(len(self.run_on(DEADLINE - timedelta(days=7))), 1)
        self.assertNotIn("smtplib", Path(reminders.__file__).read_text(encoding="utf-8"))

    def test_dry_run_writes_and_logs_nothing(self):
        written = self.run_on(DEADLINE - timedelta(days=7), dry_run=True)
        self.assertEqual(len(written), 1)
        self.assertEqual(self.files(), [])
        self.assertEqual(self.repository.list_notifications(self.organisation_id), [])
        self.assertEqual(len(self.run_on(DEADLINE - timedelta(days=7))), 1)  # a real run afterwards still sends it

    def test_a_failed_write_logs_nothing_so_it_is_retried(self):
        with patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_on(DEADLINE - timedelta(days=7))
        self.assertEqual(self.repository.list_notifications(self.organisation_id), [])
        self.assertEqual([name for name in self.files() if not name.startswith(".")], [])
        self.assertEqual(len(self.run_on(DEADLINE - timedelta(days=7))), 1)

    def test_reminders_are_organisation_scoped_in_the_log(self):
        other_id = profile.create_organisation(self.repository, {"name": "Riverside Test Charity (fictional)", "mission": "Testing."})
        self.run_on(DEADLINE - timedelta(days=7))
        self.assertEqual(self.repository.list_notifications(other_id), [])
        self.assertEqual(self.repository.list_notifications(other_id, self.application["id"]), [])


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.database = Path(self.folder.name) / "instance" / "test.sqlite3"
        connection = db.connect(self.database)
        repository = Repository(connection)
        organisation_id, _ = seed.seed_brightpath(repository)
        stored, _ = seed.seed_scenario_grants(repository)
        applications.decide(repository, organisation_id, stored[1], None, "pursue")
        connection.close()

    def run_job(self, *arguments):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = jobs.main(["notify", "--db", str(self.database), *arguments])
            except SystemExit as exit_:
                code = exit_.code
        return code, out.getvalue(), err.getvalue()

    def test_notify_writes_to_an_outbox_next_to_the_database_and_is_safe_to_repeat(self):
        code, out, _ = self.run_job("--today", "2027-03-24")
        self.assertEqual(code, 0)
        self.assertIn("Wrote 1 reminder to", out)
        self.assertIn("Deadline in 7 days: Youth Employment Fund", out)
        outbox = self.database.parent / "outbox"
        self.assertEqual(len(list(outbox.glob("*.txt"))), 1)
        code, out, _ = self.run_job("--today", "2027-03-24")
        self.assertEqual((code, out.strip().splitlines()[-1]), (0, "Wrote 0 reminders"))
        self.assertEqual(len(list(outbox.glob("*.txt"))), 1)

    def test_explicit_outbox_dry_run_and_days(self):
        outbox = Path(self.folder.name) / "elsewhere"
        code, out, _ = self.run_job("--today", "2027-03-24", "--dry-run", "--outbox", str(outbox))
        self.assertIn("Would write 1 reminder", out)
        self.assertFalse(outbox.exists())
        code, out, _ = self.run_job("--today", "2027-03-26", "--days", "5,1", "--outbox", str(outbox))
        self.assertIn("Wrote 1 reminder", out)
        self.assertEqual(len(list(outbox.glob("*.txt"))), 1)

    def test_bad_arguments_are_refused_before_anything_runs(self):
        for arguments in (("--today", "tomorrow"), ("--days", "soon"), ("--days", "-3"), ("--days", "")):
            code, _, err = self.run_job(*arguments)
            self.assertEqual(code, 2, arguments)
        self.assertFalse((self.database.parent / "outbox").exists())

    def test_the_default_outbox_lives_inside_the_git_ignored_instance_folder(self):
        # resolve() so macOS /var/... and /private/var/... compare equal
        self.assertEqual(reminders.default_outbox(self.database).resolve(), (self.database.parent / "outbox").resolve())
        ignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("instance/", [line.strip() for line in ignore])


if __name__ == "__main__":
    unittest.main()
