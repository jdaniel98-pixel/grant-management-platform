"""Email delivery with simulated SMTP: settings, the wire message, the allow-list, retries, reminders to contacts and
the commands. Nothing leaves this computer: a fake SMTP class, or a tiny server on 127.0.0.1 (fictional data only)."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import contextlib
import io
import os
import smtplib
import socketserver
import tempfile
import threading
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from canned_ai import DIGITAL
from notify_helpers import LONG_AGO, T0, World
from grant_platform import db, jobs, seed
from grant_platform.repository import Repository
from grant_platform.services import applications, delivery, notifications, reminders
from grant_platform.services.delivery import DeliveryError, EmailSettings, Message

DEADLINE = date(2027, 3, 31)
FULL = {"EMAIL_DELIVERY": "smtp", "EMAIL_FROM": "platform@sender.example", "EMAIL_ALLOWLIST": "casey@brightpath.example,@testers.example",
        "SMTP_HOST": "smtp.invalid", "SMTP_PORT": "587", "SMTP_USERNAME": "user", "SMTP_PASSWORD": "s3cret-value"}


def settings(**changes):
    return EmailSettings.from_env({**FULL, **changes})


class FakeSMTP:
    """Records what would be sent. Class attributes are reset by each test."""
    sent, logins, started, fail_with = [], [], [], None

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        FakeSMTP.started.append(True)

    def login(self, user, password):
        FakeSMTP.logins.append((user, password))

    def send_message(self, email, from_addr, to_addrs):
        if FakeSMTP.fail_with:
            raise FakeSMTP.fail_with
        FakeSMTP.sent.append((email, from_addr, list(to_addrs)))

    @classmethod
    def reset(cls):
        cls.sent, cls.logins, cls.started, cls.fail_with = [], [], [], None


class SettingsTests(unittest.TestCase):
    def test_off_by_default_and_says_why_without_values(self):
        off = EmailSettings.from_env({})
        self.assertFalse(off.enabled)
        self.assertIn("OFF", off.describe())
        partial = settings(SMTP_HOST="")
        self.assertFalse(partial.enabled)
        self.assertIn("SMTP_HOST is not set", partial.describe())
        self.assertNotIn("s3cret-value", settings().describe() + partial.describe())

    def test_each_missing_setting_keeps_it_off(self):
        for name in ("EMAIL_FROM", "EMAIL_ALLOWLIST", "SMTP_HOST"):
            self.assertFalse(settings(**{name: ""}).enabled, name)
        self.assertFalse(settings(SMTP_PASSWORD="").enabled)
        self.assertFalse(settings(EMAIL_DELIVERY="outbox").enabled)
        self.assertFalse(settings(SMTP_PORT="abc").enabled)
        self.assertFalse(settings(EMAIL_FROM="bad sender@x.example").enabled)
        self.assertTrue(settings().enabled)

    def test_allow_list_matches_addresses_and_domains_only(self):
        s = settings()
        self.assertTrue(s.allows("Casey@BrightPath.example"))
        self.assertTrue(s.allows("anyone@testers.example"))
        self.assertFalse(s.allows("other@brightpath.example"))
        self.assertFalse(s.allows("x@evil-testers.example"))
        self.assertFalse(s.allows("x@testers.example.evil.org"))


class WireMessageTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.reset()

    def test_message_is_built_and_sent_with_login_and_starttls(self):
        delivery.send_smtp(settings(), Message("Casey Bright", "casey@brightpath.example", "Hello", "Body text"), FakeSMTP)
        email, sender, to = FakeSMTP.sent[0]
        self.assertEqual((sender, to), ("platform@sender.example", ["casey@brightpath.example"]))
        self.assertEqual(email["Subject"], "Hello")
        self.assertEqual(email["Auto-Submitted"], "auto-generated")
        self.assertIn("Body text", email.get_content())
        self.assertEqual((FakeSMTP.started, FakeSMTP.logins), ([True], [("user", "s3cret-value")]))

    def test_line_breaks_in_text_cannot_add_headers_or_recipients(self):
        for subject, name in (("Hi\r\nBcc: spy@evil.example", "Casey"), ("Hi", "Casey\r\nBcc: spy@evil.example")):
            with self.assertRaises(DeliveryError):
                delivery.send_smtp(settings(), Message(name, "casey@brightpath.example", subject, "x"), FakeSMTP)
        with self.assertRaises(DeliveryError):
            delivery.send_smtp(settings(), Message("Casey", "casey@brightpath.example\r\nBcc: spy@evil.example", "Hi", "x"), FakeSMTP)
        self.assertEqual(FakeSMTP.sent, [])

    def test_failures_become_delivery_errors_without_credentials(self):
        for failure in (smtplib.SMTPAuthenticationError(535, b"bad login s3cret-value"), smtplib.SMTPRecipientsRefused({}),
                        ConnectionRefusedError("no s3cret-value"), TimeoutError()):
            FakeSMTP.fail_with = failure
            with self.assertRaises(DeliveryError) as caught:
                delivery.send_smtp(settings(), Message("C", "casey@brightpath.example", "S", "B"), FakeSMTP)
            self.assertNotIn("s3cret-value", str(caught.exception))


class ChannelTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.reset()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.outbox = Path(folder.name) / "outbox"

    def test_not_enabled_only_writes_the_outbox(self):
        deliver = delivery.channel_for(EmailSettings.from_env({}), self.outbox, FakeSMTP)
        deliver(Message("Casey", "casey@brightpath.example", "S", "B"), "m.txt")
        self.assertIn("not emailed", (self.outbox / "m.txt").read_text())
        self.assertEqual(FakeSMTP.sent, [])

    def test_enabled_emails_allow_listed_and_refuses_others(self):
        deliver = delivery.channel_for(settings(), self.outbox, FakeSMTP)
        deliver(Message("Casey", "casey@brightpath.example", "S", "B"), "ok.txt")
        self.assertEqual(len(FakeSMTP.sent), 1)
        with self.assertRaises(DeliveryError):
            deliver(Message("Real", "real.person@charity.example", "S", "B"), "no.txt")
        self.assertEqual(len(FakeSMTP.sent), 1)
        self.assertFalse((self.outbox / "no.txt").exists())


class ReminderDeliveryTests(unittest.TestCase):
    def setUp(self):
        FakeSMTP.reset()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.outbox = Path(folder.name) / "outbox"
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repo = Repository(self.connection)
        self.org, _ = seed.seed_brightpath(self.repo)
        stored, _ = seed.seed_scenario_grants(self.repo)
        needs = {n["title"]: n["id"] for n in self.repo.list_funding_needs(self.org)}
        applications.decide(self.repo, self.org, stored[1], needs[DIGITAL], "pursue")
        self.day = DEADLINE - timedelta(days=7)

    def contact(self, name, email, **prefs):
        contact = notifications.add_contact(self.repo, self.org, name, email, consent_given=True, now=LONG_AGO)
        if prefs:
            contact = notifications.update_preferences(self.repo, self.org, contact["id"], now=T0, **prefs)
        return contact

    def run_on(self, s=None, failures=None, dry_run=False):
        channel = delivery.channel_for(s or settings(), self.outbox, FakeSMTP)
        return reminders.write_reminders(self.repo, self.outbox, self.day, deliver=channel, failures=failures, dry_run=dry_run)

    def test_no_contacts_keeps_the_unaddressed_outbox_file_and_sends_nothing(self):
        self.assertEqual(len(self.run_on()), 1)
        self.assertEqual(FakeSMTP.sent, [])
        self.assertIn("no notification contacts", next(self.outbox.glob("*.txt")).read_text())

    def test_each_eligible_contact_gets_one_email_once(self):
        self.contact("Casey Bright", "casey@brightpath.example")
        self.contact("Ola Tester", "ola@testers.example")
        self.assertEqual(len(self.run_on()), 2)
        self.assertEqual(sorted(to[0] for _, _, to in FakeSMTP.sent), ["casey@brightpath.example", "ola@testers.example"])
        self.assertIn("To stop these messages", FakeSMTP.sent[0][0].get_content())
        self.assertEqual(self.run_on(), [])
        self.assertEqual(len(FakeSMTP.sent), 2)

    def test_no_consent_unsubscribed_or_reminders_off_are_skipped(self):
        notifications.add_contact(self.repo, self.org, "No Consent", "casey@brightpath.example", consent_given=False, now=LONG_AGO)
        self.contact("Off", "ola@testers.example", reminders_enabled=False)
        self.assertEqual(self.run_on(), [])
        self.assertEqual(FakeSMTP.sent, [])

    def test_a_contact_off_the_allow_list_is_reported_and_retried_without_repeating_others(self):
        self.contact("Casey Bright", "casey@brightpath.example")
        self.contact("Real Person", "real@charity.example")
        failures = []
        self.assertEqual(len(self.run_on(failures=failures)), 1)
        self.assertEqual(len(failures), 1)
        self.assertIn("real@charity.example", failures[0][0])
        self.assertEqual(len(FakeSMTP.sent), 1)
        # Allow-list widened: only the one who missed out is sent to
        self.assertEqual(len(self.run_on(settings(EMAIL_ALLOWLIST="casey@brightpath.example,real@charity.example"))), 1)
        self.assertEqual([to[0] for _, _, to in FakeSMTP.sent], ["casey@brightpath.example", "real@charity.example"])
        self.assertEqual(self.run_on(), [])

    def test_a_server_failure_logs_nothing_and_the_next_run_sends(self):
        self.contact("Casey Bright", "casey@brightpath.example")
        FakeSMTP.fail_with = ConnectionRefusedError()
        failures = []
        self.assertEqual(self.run_on(failures=failures), [])
        self.assertEqual(len(failures), 1)
        self.assertEqual(self.repo.list_messages(self.org), [])
        FakeSMTP.fail_with = None
        self.assertEqual(len(self.run_on()), 1)
        self.assertEqual(len(FakeSMTP.sent), 1)

    def test_dry_run_sends_and_logs_nothing(self):
        self.contact("Casey Bright", "casey@brightpath.example")
        self.assertEqual(len(self.run_on(dry_run=True)), 1)
        self.assertEqual((FakeSMTP.sent, self.repo.list_messages(self.org)), ([], []))

    def test_a_reminder_never_reaches_another_charitys_contact(self):
        other = World()
        self.addCleanup(other.close)
        self.contact("Casey Bright", "casey@brightpath.example")
        self.run_on()
        recipients = [to[0] for _, _, to in FakeSMTP.sent]
        self.assertEqual(recipients, ["casey@brightpath.example"])
        self.assertEqual(other.repo.list_messages(other.a), [])


class LocalServerTests(unittest.TestCase):
    """A real SMTP conversation over a socket to a throwaway server on 127.0.0.1 (no TLS, no login)."""

    def setUp(self):
        received = self.received = []

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                send = lambda text: self.wfile.write((text + "\r\n").encode())
                send("220 test ready")
                data = False
                message = []
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        return
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    if data:
                        if line == ".":
                            data = False
                            received.append("\n".join(message))
                            send("250 queued")
                        else:
                            message.append(line)
                        continue
                    command = (line.split() or [""])[0].upper()
                    if command in ("EHLO", "HELO"):
                        send("250 test")
                    elif command in ("MAIL", "RCPT", "RSET"):
                        send("250 ok")
                    elif command == "DATA":
                        data = True
                        message = []
                        send("354 go")
                    elif command == "QUIT":
                        send("221 bye")
                        return
                    else:
                        send("502 no")

        self.server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def local(self):
        return settings(SMTP_HOST="127.0.0.1", SMTP_PORT=str(self.port), SMTP_USERNAME="", SMTP_PASSWORD="", SMTP_STARTTLS="0")

    def test_a_message_crosses_a_real_socket(self):
        delivery.send_smtp(self.local(), Message("Casey Bright", "casey@brightpath.example", "Deadline in 7 days: Fund", "Grant: Fund"))
        self.assertEqual(len(self.received), 1)
        self.assertIn("Subject: Deadline in 7 days: Fund", self.received[0])
        self.assertIn("To: Casey Bright <casey@brightpath.example>", self.received[0])

    def test_a_dead_server_is_a_clean_delivery_error(self):
        dead = settings(SMTP_HOST="127.0.0.1", SMTP_PORT="1", SMTP_USERNAME="", SMTP_PASSWORD="", SMTP_STARTTLS="0")
        with self.assertRaises(DeliveryError):
            delivery.send_smtp(dead, Message("C", "casey@brightpath.example", "S", "B"))


class CommandTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.db_path = str(self.folder / "test.sqlite3")
        connection = db.connect(self.db_path)
        self.addCleanup(connection.close)
        self.repo = Repository(connection)
        self.org, _ = seed.seed_brightpath(self.repo)
        stored, _ = seed.seed_scenario_grants(self.repo)
        needs = {n["title"]: n["id"] for n in self.repo.list_funding_needs(self.org)}
        applications.decide(self.repo, self.org, stored[1], needs[DIGITAL], "pursue")
        notifications.add_contact(self.repo, self.org, "Casey Bright", "casey@brightpath.example", consent_given=True, now=LONG_AGO)
        # Never read the real .env while testing
        for patcher in (patch.object(jobs.ai, "load_env"), patch.dict(os.environ, {}, clear=False)):
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in (*delivery.ENV_NAMES, "OPENAI_API_KEY", "OPENAI_MODEL"):  # run-daily adds a live match step if a key is set
            os.environ.pop(name, None)

    def command(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = jobs.main([*args, "--db", self.db_path])
        return code, out.getvalue(), err.getvalue()

    def test_status_reports_off_by_default_and_never_prints_secrets(self):
        code, out, _ = self.command("email-status")
        self.assertEqual(code, 0)
        self.assertIn("OFF", out)
        os.environ.update(FULL)
        _, out, _ = self.command("email-status")
        self.assertIn("ON", out)
        self.assertNotIn("s3cret-value", out)

    def test_send_test_needs_confirmation_setup_and_allow_listing(self):
        with patch.object(delivery.smtplib, "SMTP", FakeSMTP):
            FakeSMTP.reset()
            self.assertEqual(self.command("send-test", "--to", "casey@brightpath.example")[0], 1)
            self.assertEqual(self.command("send-test", "--to", "casey@brightpath.example", "--confirm-real-send")[0], 1)  # not set up
            os.environ.update(FULL)
            code, _, err = self.command("send-test", "--to", "stranger@elsewhere.example", "--confirm-real-send")
            self.assertEqual(code, 1)
            self.assertIn("EMAIL_ALLOWLIST", err)
            self.assertEqual(FakeSMTP.sent, [])

    def test_notify_writes_the_outbox_when_email_is_off(self):
        code, out, _ = self.command("notify", "--today", "2027-03-24")
        self.assertEqual(code, 0)
        self.assertIn("Email sending is OFF", out)
        self.assertEqual(len(list((self.folder / "outbox").glob("*.txt"))), 1)

    def test_notify_through_a_failing_server_exits_nonzero_and_retries(self):
        os.environ.update({**FULL, "SMTP_HOST": "127.0.0.1", "SMTP_PORT": "1", "SMTP_USERNAME": "", "SMTP_PASSWORD": "", "SMTP_STARTTLS": "0"})
        code, _, err = self.command("notify", "--today", "2027-03-24")
        self.assertEqual(code, 1)
        self.assertIn("tried again next run", err)
        self.assertNotIn("s3cret", err)
        self.assertEqual(self.repo.list_messages(self.org), [])

    def test_run_daily_runs_every_step_logs_and_is_repeatable(self):
        os.environ.pop("OPENAI_API_KEY", None)
        logs = self.folder / "logs"
        code, out, _ = self.command("run-daily", "--today", "2027-03-24")
        self.assertEqual(code, 0)
        for step in ("housekeep: ok", "digest: ok", "notify: ok", "Matching skipped", "no sources are enabled"):
            self.assertIn(step, out)
        self.assertEqual(len(list((self.folder / "outbox").glob("*.txt"))), 1)  # the one reminder (no matches, so no digest)
        self.assertIn("notify: ok", (logs / "daily-2027-03-24.log").read_text())
        self.command("run-daily", "--today", "2027-03-24")
        self.assertEqual(len(list((self.folder / "outbox").glob("*.txt"))), 1)

    def test_run_daily_reports_a_failing_step_and_still_runs_the_rest(self):
        os.environ.update({**FULL, "SMTP_HOST": "127.0.0.1", "SMTP_PORT": "1", "SMTP_USERNAME": "", "SMTP_PASSWORD": "", "SMTP_STARTTLS": "0"})
        code, out, _ = self.command("run-daily", "--today", "2027-03-24")
        self.assertEqual(code, 1)
        self.assertIn("notify: FAILED", out)
        self.assertIn("housekeep: ok", out)


if __name__ == "__main__":
    unittest.main()
