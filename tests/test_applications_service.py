"""Application tracking rules: decisions, status changes to outcomes, checklist, tasks, isolation (fictional data)."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import sqlite3
import unittest
from datetime import date

from canned_ai import CORE, DIGITAL
from grant_platform import db, seed
from grant_platform.repository import Repository
from grant_platform.services import ValidationError, applications, matching, profile


class ApplicationTestCase(unittest.TestCase):
    def setUp(self):
        self.connection = db.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.repository = Repository(self.connection)
        self.organisation_id, _ = seed.seed_brightpath(self.repository)
        self.stored, _ = seed.seed_scenario_grants(self.repository)
        self.needs = {need["title"]: need["id"] for need in self.repository.list_funding_needs(self.organisation_id)}
        self.other_id = profile.create_organisation(self.repository, {"name": "Riverside Test Charity (fictional)", "mission": "Testing."})

    def decide(self, scenario, decision, need=None):
        return applications.decide(self.repository, self.organisation_id, self.stored[scenario], self.needs[need] if need else None, decision)

    def move(self, application_id, status, **kwargs):
        return applications.change_status(self.repository, self.organisation_id, application_id, status, **kwargs)

    def statuses(self, application_id):
        return [(event["from_status"], event["to_status"]) for event in self.repository.list_application_events(self.organisation_id, application_id)]


class DecisionTests(ApplicationTestCase):
    def test_pursue_creates_an_application_with_the_grants_deadline_and_a_checklist(self):
        application = self.decide(1, "pursue", DIGITAL)
        self.assertEqual((application["status"], application["grant_title"], application["need_title"]), ("pursuing", "Youth Employment Fund", DIGITAL))
        self.assertEqual(application["deadline_date"], "2027-03-31")
        self.assertEqual(applications.scope_label(application), DIGITAL)
        texts = [item["text"] for item in self.repository.list_requirements(self.organisation_id, application["id"])]
        self.assertEqual(texts[:3], ["Outline of the programme", "evidence of outcomes for young people", "budget"])
        self.assertTrue(texts[3].startswith("Confirm you meet the eligibility rules: Open to registered charities"))
        self.assertTrue(texts[4].startswith("Check the exclusions do not apply: Statutory services"))
        self.assertTrue(all(not item["done"] and item["source"] == "grant" for item in self.repository.list_requirements(self.organisation_id, application["id"])))
        self.assertEqual(self.statuses(application["id"]), [(None, "pursuing")])

    def test_save_and_whole_organisation_scope(self):
        application = self.decide(2, "save")
        self.assertEqual((application["status"], applications.scope_label(application)), ("saved", "Whole organisation"))

    def test_deciding_twice_does_not_duplicate(self):
        first = self.decide(1, "pursue")
        second = self.decide(1, "pursue")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.repository.list_applications(self.organisation_id)), 1)
        self.assertEqual(len(self.statuses(first["id"])), 1)

    def test_a_saved_grant_can_later_be_pursued_and_each_step_is_listed(self):
        saved = self.decide(1, "save")
        pursued = self.decide(1, "pursue")
        self.assertEqual((pursued["id"], pursued["status"]), (saved["id"], "pursuing"))
        self.assertEqual(self.statuses(saved["id"]), [(None, "saved"), ("saved", "pursuing")])

    def test_the_same_grant_can_be_tracked_separately_for_different_needs(self):
        a, b, whole = self.decide(6, "pursue", DIGITAL), self.decide(6, "reject", CORE), self.decide(6, "save")
        self.assertEqual(len({a["id"], b["id"], whole["id"]}), 3)

    def test_unknown_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            applications.decide(self.repository, self.organisation_id, self.stored[1], None, "maybe")
        with self.assertRaises(LookupError):
            applications.decide(self.repository, self.organisation_id, 9999, None, "save")
        with self.assertRaises(LookupError):
            applications.decide(self.repository, 9999, self.stored[1], None, "save")
        with self.assertRaises(LookupError):
            applications.decide(self.repository, self.other_id, self.stored[1], self.needs[DIGITAL], "save")  # another organisation's need


class RejectionTests(ApplicationTestCase):
    def test_a_rejected_grant_drops_out_of_that_needs_suggestions_only(self):
        self.decide(3, "reject", DIGITAL)
        self.assertEqual(applications.rejected_grant_ids(self.repository, self.organisation_id, self.needs[DIGITAL]), {self.stored[3]})
        self.assertEqual(applications.rejected_grant_ids(self.repository, self.organisation_id, self.needs[CORE]), set())
        self.assertEqual(applications.rejected_grant_ids(self.repository, self.organisation_id, None), set())
        self.assertEqual(applications.rejected_grant_ids(self.repository, self.other_id, self.needs[DIGITAL]), set())

    def test_assess_all_skips_rejected_grants(self):
        from canned_ai import CannedGateway
        self.decide(3, "reject", DIGITAL)
        gateway = CannedGateway()
        skip = applications.rejected_grant_ids(self.repository, self.organisation_id, self.needs[DIGITAL])
        results, errors = matching.assess_all(self.repository, self.organisation_id, self.needs[DIGITAL], gateway=gateway, skip_grant_ids=skip)
        self.assertEqual((len(results), errors), (6, {}))
        self.assertNotIn("American Youth Initiative", [call["payload"]["grant"]["title"] for call in gateway.calls])

    def test_rejected_applications_are_hidden_from_the_list_unless_asked_for_and_can_be_reconsidered(self):
        rejected = self.decide(3, "reject")
        self.assertEqual(applications.list_applications(self.repository, self.organisation_id), [])
        self.assertEqual(len(applications.list_applications(self.repository, self.organisation_id, include_rejected=True)), 1)
        self.assertEqual(self.decide(3, "save")["status"], "saved")  # reconsider
        self.assertEqual(applications.rejected_grant_ids(self.repository, self.organisation_id), set())
        self.assertGreater(len(self.repository.list_requirements(self.organisation_id, rejected["id"])), 0)  # checklist built on reconsidering

    def test_a_rejected_application_has_no_checklist(self):
        rejected = self.decide(3, "reject")
        self.assertEqual(self.repository.list_requirements(self.organisation_id, rejected["id"]), [])


class StatusTests(ApplicationTestCase):
    def test_the_full_journey_to_an_awarded_outcome(self):
        application = self.decide(1, "pursue")
        for status in ("drafting", "submitted"):
            application = self.move(application["id"], status)
        self.assertEqual(application["submitted_at"], date.today().isoformat())
        awarded = self.move(application["id"], "awarded", note="Panel decision 12 May", amount_awarded="£45,000")
        self.assertEqual((awarded["status"], awarded["amount_awarded"], awarded["outcome_note"]), ("awarded", 45000, "Panel decision 12 May"))
        self.assertEqual(self.statuses(application["id"]), [(None, "pursuing"), ("pursuing", "drafting"), ("drafting", "submitted"), ("submitted", "awarded")])
        persisted = self.repository.get_application(self.organisation_id, application["id"])
        self.assertEqual(persisted["amount_awarded"], 45000)

    def test_an_unsuccessful_outcome_can_be_recorded_with_a_note(self):
        application = self.decide(1, "pursue")
        self.move(application["id"], "submitted")
        result = self.move(application["id"], "unsuccessful", note="Feedback: stronger evidence needed")
        self.assertEqual((result["status"], result["outcome_note"], result["amount_awarded"]), ("unsuccessful", "Feedback: stronger evidence needed", None))

    def test_disallowed_moves_are_refused_and_change_nothing(self):
        application = self.decide(1, "pursue")
        for status in ("awarded", "unsuccessful", "rejected", "nonsense"):
            with self.assertRaises(ValidationError, msg=status):
                self.move(application["id"], status)
        self.assertEqual(self.repository.get_application(self.organisation_id, application["id"])["status"], "pursuing")
        self.assertEqual(len(self.statuses(application["id"])), 1)

    def test_outcomes_are_final(self):
        application = self.decide(1, "pursue")
        self.move(application["id"], "submitted")
        self.move(application["id"], "awarded")
        self.assertEqual(applications.next_statuses(self.repository.get_application(self.organisation_id, application["id"])), ())
        with self.assertRaises(ValidationError):
            self.move(application["id"], "pursuing")

    def test_withdrawn_can_be_reopened(self):
        application = self.decide(1, "pursue")
        self.move(application["id"], "withdrawn")
        self.assertEqual(self.move(application["id"], "pursuing")["status"], "pursuing")

    def test_amount_rules(self):
        application = self.decide(1, "pursue")
        self.move(application["id"], "submitted")
        with self.assertRaises(ValidationError) as caught:
            self.move(application["id"], "unsuccessful", amount_awarded=1000)
        self.assertEqual(list(caught.exception.errors), ["amount_awarded"])
        for bad in ("lots", "0", "-5"):
            with self.assertRaises(ValidationError, msg=bad):
                self.move(application["id"], "awarded", amount_awarded=bad)
        self.assertEqual(self.repository.get_application(self.organisation_id, application["id"])["status"], "submitted")
        self.assertIsNone(self.move(application["id"], "awarded", amount_awarded="  ")["amount_awarded"])

    def test_long_notes_are_refused(self):
        application = self.decide(1, "pursue")
        with self.assertRaises(ValidationError):
            self.move(application["id"], "withdrawn", note="x" * (applications.NOTE_LIMIT + 1))

    def test_the_deadline_follows_the_grant_and_the_list_orders_active_work_by_it(self):
        late, early, done = self.decide(7, "pursue"), self.decide(4, "pursue"), self.decide(1, "pursue")
        self.move(done["id"], "withdrawn")
        listed = applications.list_applications(self.repository, self.organisation_id)
        self.assertEqual([row["id"] for row in listed], [early["id"], late["id"], done["id"]])
        self.assertEqual(listed[0]["days_left"], (date(2027, 2, 28) - date.today()).days)


class ChecklistAndTaskTests(ApplicationTestCase):
    def test_requirements_can_be_ticked_and_added_to(self):
        application = self.decide(1, "pursue")
        first = self.repository.list_requirements(self.organisation_id, application["id"])[0]
        applications.set_requirement_done(self.repository, self.organisation_id, application["id"], first["id"], True)
        applications.add_requirement(self.repository, self.organisation_id, application["id"], "  Get trustee sign-off  ")
        items = self.repository.list_requirements(self.organisation_id, application["id"])
        self.assertTrue(items[0]["done"])
        self.assertEqual((items[-1]["text"], items[-1]["source"], items[-1]["done"]), ("Get trustee sign-off", "manual", False))
        self.assertEqual(applications.progress(self.repository, self.organisation_id, application["id"])[:2], (1, 6))
        with self.assertRaises(ValidationError):
            applications.add_requirement(self.repository, self.organisation_id, application["id"], "  ")

    def test_tasks_with_due_dates_validation_and_ordering(self):
        application = self.decide(1, "pursue")
        app_id = application["id"]
        applications.add_task(self.repository, self.organisation_id, app_id, "Ask trustees for approval", "15/03/2027")
        applications.add_task(self.repository, self.organisation_id, app_id, "Collect case study")
        applications.add_task(self.repository, self.organisation_id, app_id, "Draft budget", "01/03/2027")
        tasks = self.repository.list_tasks(self.organisation_id, app_id)
        self.assertEqual([task["text"] for task in tasks], ["Draft budget", "Ask trustees for approval", "Collect case study"])
        self.assertEqual(tasks[0]["due_date"], "2027-03-01")
        applications.set_task_done(self.repository, self.organisation_id, app_id, tasks[0]["id"], True)
        self.assertEqual(self.repository.list_tasks(self.organisation_id, app_id)[-1]["text"], "Draft budget")  # done tasks sink
        self.assertEqual(applications.progress(self.repository, self.organisation_id, app_id)[2], 2)
        for text, due in (("", None), ("x" * 301, None), ("Fine", "someday")):
            with self.assertRaises(ValidationError):
                applications.add_task(self.repository, self.organisation_id, app_id, text, due)
        self.assertEqual(len(self.repository.list_tasks(self.organisation_id, app_id)), 3)


class IsolationTests(ApplicationTestCase):
    def test_another_organisation_cannot_see_or_change_an_application(self):
        application = self.decide(1, "pursue")
        requirement = self.repository.list_requirements(self.organisation_id, application["id"])[0]
        applications.add_task(self.repository, self.organisation_id, application["id"], "Private task")
        task = self.repository.list_tasks(self.organisation_id, application["id"])[0]
        other = self.other_id
        self.assertEqual(applications.list_applications(self.repository, other), [])
        self.assertIsNone(self.repository.get_application(other, application["id"]))
        self.assertEqual(self.repository.list_requirements(other, application["id"]), [])
        self.assertEqual(self.repository.list_tasks(other, application["id"]), [])
        self.assertEqual(self.repository.list_application_events(other, application["id"]), [])
        with self.assertRaises(LookupError):
            applications.change_status(self.repository, other, application["id"], "withdrawn")
        with self.assertRaises(LookupError):
            applications.add_task(self.repository, other, application["id"], "Sneaky")
        with self.assertRaises(LookupError):
            applications.add_requirement(self.repository, other, application["id"], "Sneaky")
        with self.assertRaises(LookupError):
            applications.set_requirement_done(self.repository, other, application["id"], requirement["id"], True)
        with self.assertRaises(LookupError):
            applications.set_task_done(self.repository, other, application["id"], task["id"], True)
        self.assertFalse(self.repository.update_application(other, application["id"], status="withdrawn"))
        after = self.repository.get_application(self.organisation_id, application["id"])
        self.assertEqual(after["status"], "pursuing")
        self.assertFalse(self.repository.list_requirements(self.organisation_id, application["id"])[0]["done"])

    def test_the_database_itself_refuses_a_second_application_for_the_same_scope(self):
        self.decide(1, "save")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.create_application(self.organisation_id, None, self.stored[1], "saved")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.create_application(self.organisation_id, None, self.stored[1], "not a status")


if __name__ == "__main__":
    unittest.main()
