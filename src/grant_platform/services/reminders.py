"""Deadline reminders.

A reminder is due when an application that is still being worked on (saved, pursuing or drafting) has a deadline
that is 28, 14, 7 or 2 days away. If the job misses a day, the nearest threshold already passed is used, once.

Who gets it:
- an organisation with no notification contacts at all: one unaddressed file in the local outbox (nothing is emailed);
- an organisation with contacts: one message to each contact who has given consent, has not unsubscribed and has
  reminders switched on, through the delivery channel (the outbox by default; email only when it is fully set up).
  Each contact gets each reminder once; a contact whose delivery failed is retried on the next run without repeating it
  to the others."""
import os
import re
from datetime import date
from pathlib import Path

from grant_platform.services import applications, grants, notifications
from grant_platform.services.delivery import DeliveryError, Message, outbox_channel

DEFAULT_DAYS = (28, 14, 7, 2)
REMIND_STATUSES = ("saved", "pursuing", "drafting")
MAX_TASKS_LISTED = 10


def _line(text):
    """One line of plain text: no line breaks, so a title can never add a header to a reminder."""
    return " ".join(str(text or "").split())


def crossed_thresholds(days_left, days=DEFAULT_DAYS):
    return [threshold for threshold in days if days_left is not None and 0 <= days_left <= threshold]


def render_body(organisation, application, requirements, tasks, days_left, today=None):
    """(subject, text) with no recipient line and no delivery note."""
    when = "today" if days_left == 0 else f"in {days_left} day{'s' if days_left != 1 else ''}"
    subject = f"Deadline {when}: {_line(application['grant_title'])} ({_line(organisation['name'])})"
    done = sum(item["done"] for item in requirements)
    open_tasks = [task for task in tasks if not task["done"]]
    lines = [
        f"Grant: {_line(application['grant_title'])}",
        f"Funder: {_line(application['grant_funder'])}",
        f"For: {_line(applications.scope_label(application))}",
        f"Deadline: {grants.deadline_label(application, today)}",
        f"Status: {applications.STATUSES[application['status']]}",
        f"Requirements ticked: {done} of {len(requirements)}",
        f"Open tasks: {len(open_tasks)}",
    ]
    for task in open_tasks[:MAX_TASKS_LISTED]:
        lines.append(f"  - {_line(task['text'])}" + (f" (due {task['due_date']})" if task["due_date"] else ""))
    if len(open_tasks) > MAX_TASKS_LISTED:
        lines.append(f"  - and {len(open_tasks) - MAX_TASKS_LISTED} more")
    return subject, "\n".join(lines)


def render(organisation, application, requirements, tasks, days_left, today=None):
    """The unaddressed local-outbox version (used when an organisation has no contacts)."""
    subject, text = render_body(organisation, application, requirements, tasks, days_left, today)
    lines = ["To: (no recipient: no notification contacts have been added)", f"Subject: {subject}", "", text,
             "", "This reminder was written to the local outbox. It was not emailed."]
    return subject, "\n".join(lines) + "\n"


def recipients(repository, organisation_id):
    """Contacts who may be sent reminders: consent recorded, not unsubscribed, reminders on."""
    return [contact for contact in repository.list_contacts(organisation_id)
            if notifications.emailable(contact) and contact["reminders_enabled"]]


def outbox_name(today, application, threshold):
    """Built only from numbers and dates, never from user text."""
    return f"{today.isoformat()}_application-{application['id']}_{threshold}-days.txt"


def due_reminders(repository, today, days=DEFAULT_DAYS):
    """(organisation, application, threshold, days_left) for every reminder not yet written."""
    due = []
    for organisation in repository.list_organisations():
        for application in repository.list_applications(organisation["id"]):
            if application["status"] not in REMIND_STATUSES:
                continue
            days_left = grants.days_until_deadline(application, today)
            crossed = crossed_thresholds(days_left, days)
            if not crossed:
                continue
            logged = {row["trigger_key"] for row in repository.list_notifications(organisation["id"], application["id"])}
            tightest = min(crossed)
            if f"deadline:{tightest}" not in logged:
                due.append((organisation, application, tightest, days_left, crossed))
    return due


def contact_outbox_name(today, application, threshold, contact):
    return f"{today.isoformat()}_application-{application['id']}_{threshold}-days_contact-{contact['id']}.txt"


def _write_unaddressed(repository, outbox, name, body, application, threshold, crossed):
    outbox.mkdir(parents=True, exist_ok=True)
    with repository.connection:  # rolls the log back if the file cannot be written
        for crossed_threshold in crossed:  # larger thresholds already passed are not sent later
            repository.log_notification(application["id"], f"deadline:{crossed_threshold}", name if crossed_threshold == threshold else None)
        temporary = outbox / f".{name}.tmp"
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, outbox / name)


def _send_to_contacts(repository, deliver, organisation, application, threshold, crossed, days_left, today, now, dry_run, failures):
    """Returns [(file name, subject)] for the contacts reached (or that would be, on a dry run)."""
    requirements = repository.list_requirements(organisation["id"], application["id"])
    tasks = repository.list_tasks(organisation["id"], application["id"])
    subject, text = render_body(organisation, application, requirements, tasks, days_left, today)
    key = f"{application['id']}:{threshold}"
    written, missing = [], 0
    for contact in recipients(repository, organisation["id"]):
        if repository.has_message(contact["id"], "deadline_reminder", key):
            continue
        name = contact_outbox_name(today, application, threshold, contact)
        message = Message(contact["name"], contact["email"], subject, notifications.with_footer(text, organisation, contact))
        if not dry_run:
            try:
                with repository.connection:
                    repository.log_message(organisation["id"], contact["id"], "deadline_reminder", key, now, name)
                    deliver(message, name)
            except DeliveryError as error:
                missing += 1
                if failures is not None:
                    failures.append((message.address(), str(error)))
                continue
        written.append((name, subject))
    if not dry_run and not missing:
        with repository.connection:  # everyone has it: this threshold is finished
            for crossed_threshold in crossed:
                repository.log_notification(application["id"], f"deadline:{crossed_threshold}")
    return written


def write_reminders(repository, outbox, today=None, days=DEFAULT_DAYS, dry_run=False, deliver=None, failures=None, now=None):
    """Produce each due reminder and log it. Returns the list of (file name, subject).
    A reminder is logged only if its delivery succeeded, so a failure is retried next run. `deliver` is the delivery
    channel (default: write to the outbox folder); `failures` collects (address, reason) for messages it refused."""
    today = today or date.today()
    outbox = Path(outbox)
    deliver = deliver or outbox_channel(outbox)
    now = now or notifications._now()
    written = []
    for organisation, application, threshold, days_left, crossed in due_reminders(repository, today, days):
        if repository.list_contacts(organisation["id"]):
            written += _send_to_contacts(repository, deliver, organisation, application, threshold, crossed, days_left, today, now, dry_run, failures)
            continue
        requirements = repository.list_requirements(organisation["id"], application["id"])
        tasks = repository.list_tasks(organisation["id"], application["id"])
        subject, body = render(organisation, application, requirements, tasks, days_left, today)
        name = outbox_name(today, application, threshold)
        if not dry_run:
            _write_unaddressed(repository, outbox, name, body, application, threshold, crossed)
        written.append((name, subject))
    return written


def default_outbox(database_path):
    """Next to the database, inside instance/, which is git-ignored."""
    return Path(database_path).resolve().parent / "outbox"
