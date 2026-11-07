"""Notifications screen walkthroughs with Streamlit's AppTest; fictional data, nothing is emailed."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from notify_helpers import MONDAY, T0, World, make_grant
from grant_platform import db
from grant_platform.repository import Repository
from grant_platform.services import delivery, notifications

SCRIPT = "from grant_platform.ui import app; app.main()"


def screen_text(app_test):
    groups = (app_test.markdown, app_test.caption, app_test.title, app_test.subheader, app_test.info, app_test.success, app_test.warning, app_test.error)
    return "\n".join(element.value for group in groups for element in group)


class NotificationScreenTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path = self.folder / "test.sqlite3"
        patcher = patch.dict(os.environ, {"GRANT_PLATFORM_DB": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        connection = db.connect(self.path)
        self.addCleanup(connection.close)
        self.repo = Repository(connection)
        from notify_helpers import make_organisation
        self.a, self.a_need = make_organisation(self.repo, "harbour-youth", "Harbour Youth Trust (fictional)", "Harbour Youth Studio")
        self.b, self.b_need = make_organisation(self.repo, "riverside-arts", "Riverside Arts Collective (fictional)", "Riverside Mural Project")
        self.contact_b = notifications.add_contact(self.repo, self.b, "Robin Riverside", "robin@riverside.example", consent_given=True, now=T0)

    def page(self, organisation_id=None):
        app_test = AppTest.from_string(SCRIPT, default_timeout=30).run()
        if organisation_id:
            app_test.sidebar.selectbox(key="organisation").set_value(organisation_id).run()
        app_test.sidebar.radio(key="page").set_value("Notifications").run()
        self.assertFalse(app_test.exception)
        return app_test

    def test_development_notice_says_nothing_is_emailed(self):
        warning = " ".join(w.value for w in self.page(self.a).warning)
        self.assertIn("Development only", warning)
        self.assertIn("nothing is emailed", warning)

    def test_each_organisation_sees_only_its_own_contacts(self):
        self.assertNotIn("Robin Riverside", screen_text(self.page(self.a)))
        self.assertIn("Robin Riverside", screen_text(self.page(self.b)))
        self.assertNotIn("Harbour", " ".join(s.value for s in self.page(self.b).subheader))

    def test_adding_a_contact_with_consent(self):
        app_test = self.page(self.a)
        app_test.text_input(key="new_contact_name").set_value("Alex Harbour")
        app_test.text_input(key="new_contact_email").set_value("Alex@Harbour.example")
        app_test.checkbox(key="new_contact_consent").check()
        app_test.button(key="new_contact_submit").click().run()
        self.assertFalse(app_test.exception)
        saved = self.repo.list_contacts(self.a)
        self.assertEqual([(c["name"], c["email"], bool(c["consent_given_at"])) for c in saved], [("Alex Harbour", "alex@harbour.example", True)])
        self.assertIn("Added Alex Harbour", screen_text(app_test))
        self.assertIn("Receives alerts", screen_text(self.page(self.a)))

    def test_without_consent_the_contact_is_stored_but_clearly_not_emailed(self):
        app_test = self.page(self.a)
        app_test.text_input(key="new_contact_name").set_value("Sam Lee")
        app_test.text_input(key="new_contact_email").set_value("sam@harbour.example")
        app_test.button(key="new_contact_submit").click().run()
        contact = self.repo.list_contacts(self.a)[0]
        self.assertIsNone(contact["consent_given_at"])
        text = screen_text(self.page(self.a))
        self.assertIn("No consent recorded: no email will be sent", text)
        page = self.page(self.a)
        page.button(key=f"contact_{contact['id']}_consent").click().run()
        self.assertTrue(self.repo.get_contact(self.a, contact["id"])["consent_given_at"])

    def test_bad_input_is_explained_and_nothing_is_saved(self):
        app_test = self.page(self.a)
        app_test.text_input(key="new_contact_name").set_value("Alex")
        app_test.text_input(key="new_contact_email").set_value("not-an-email")
        app_test.button(key="new_contact_submit").click().run()
        self.assertIn("does not look like an email address", " ".join(e.value for e in app_test.error))
        self.assertEqual(self.repo.list_contacts(self.a), [])

    def test_saving_preferences_and_unsubscribing(self):
        contact = notifications.add_contact(self.repo, self.a, "Alex Harbour", "alex@harbour.example", consent_given=True, now=T0)
        key = f"contact_{contact['id']}"
        app_test = self.page(self.a)
        app_test.selectbox(key=f"{key}_frequency").set_value("daily")
        app_test.selectbox(key=f"{key}_band").set_value("Strong fit")
        app_test.checkbox(key=f"{key}_verified").check()
        app_test.button(key=f"{key}_save").click().run()
        saved = self.repo.get_contact(self.a, contact["id"])
        self.assertEqual((saved["alerts_frequency"], saved["min_band"], saved["verified_only"]), ("daily", "Strong fit", True))
        self.assertIn("Preferences saved", screen_text(app_test))
        page = self.page(self.a)
        page.button(key=f"{key}_unsubscribe").click().run()
        self.assertIsNotNone(self.repo.get_contact(self.a, contact["id"])["unsubscribed_at"])
        self.assertIn("no email will be sent", screen_text(self.page(self.a)))
        with self.assertRaises(KeyError):
            self.page(self.a).button(key=f"{key}_unsubscribe")
        self.assertIsNone(self.repo.get_contact(self.b, self.contact_b["id"])["unsubscribed_at"])

    def test_messages_written_are_listed_for_that_organisation_only(self):
        contact = notifications.add_contact(self.repo, self.a, "Alex Harbour", "alex@harbour.example", consent_given=True, now=T0)
        grant = make_grant(self.repo, "Listed Fund")
        from notify_helpers import result
        self.repo.save_match(self.a, self.a_need, grant["id"], result())
        notifications.send_digests(self.repo, delivery.outbox_channel(self.folder / "outbox"), MONDAY, now=T0)
        text = screen_text(self.page(self.a))
        self.assertIn("Messages written", text)
        self.assertIn("Digest", text)
        self.assertNotIn("Messages written", screen_text(self.page(self.b)))


if __name__ == "__main__":
    unittest.main()
