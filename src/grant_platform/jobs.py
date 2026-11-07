"""Command-line jobs. Run from the project folder:
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs notify [--db FILE] [--outbox DIR] [--today YYYY-MM-DD] [--dry-run]
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs sources [--enable SLUG | --disable SLUG]
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs discover SLUG [--dry-run] [--max-pages N]
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs match [--max-assessments N] [--dry-run]
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs digest [--outbox DIR] [--today YYYY-MM-DD] [--dry-run]
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs unsubscribe CODE
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs run-daily
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs email-status
    PYTHONPATH=src .venv/bin/python -m grant_platform.jobs send-test --to ADDRESS --confirm-real-send
`notify` writes deadline reminders to a local outbox folder.
`discover` fetches one monitored source politely and saves new grants as unverified; it never closes or deletes grants.
`match` assesses new or changed grants with the AI service (needs an OpenAI key; capped per run; use fictional organisations only).
`digest` writes each consented contact's alert digest to the outbox.
Both email only if email delivery is fully set up (see `email-status`); it is off by default. `send-test` sends one real
message to one allow-listed address and needs --confirm-real-send."""
import argparse
import contextlib
import io
import os
import sys
from datetime import date
from pathlib import Path

from grant_platform import db
from grant_platform.repository import Repository
from grant_platform.services import ai, delivery, discovery, grants, matching, notifications, reminders
from grant_platform.services.fetcher import CONTACT_ENV


def iso_date(text):
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("use a date like 2027-03-03") from None


def day_list(text):
    try:
        values = tuple(sorted({int(part) for part in text.split(",")}, reverse=True))
    except ValueError:
        raise argparse.ArgumentTypeError("use whole numbers like 28,14,7,2") from None
    if not values or min(values) < 0:
        raise argparse.ArgumentTypeError("use whole numbers like 28,14,7,2")
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0], prog="grant_platform.jobs")
    commands = parser.add_subparsers(dest="command", required=True)
    notify = commands.add_parser("notify", help="write deadline reminders to the local outbox (emailed only if email is fully set up)")
    notify.add_argument("--db", default=os.environ.get("GRANT_PLATFORM_DB") or str(db.DEFAULT_DB_PATH), help="SQLite database path")
    notify.add_argument("--outbox", help="folder for reminder files (default: outbox/ next to the database)")
    notify.add_argument("--today", type=iso_date, default=None, help="pretend today is this date (for testing)")
    notify.add_argument("--days", type=day_list, default=reminders.DEFAULT_DAYS, help="days before the deadline, default 28,14,7,2")
    notify.add_argument("--dry-run", action="store_true", help="show what would be written without writing or logging")
    sources = commands.add_parser("sources", help="list monitored grant sources, or enable or disable one")
    toggle = sources.add_mutually_exclusive_group()
    toggle.add_argument("--enable", metavar="SLUG", help="allow discover to run this source (after its dry run has been reviewed)")
    toggle.add_argument("--disable", metavar="SLUG", help="stop discover running this source")
    match = commands.add_parser("match", help="assess new or changed grants for every organisation's funding needs (uses the AI service)")
    match.add_argument("--max-assessments", type=int, default=20, help="stop after this many AI assessments (default 20)")
    match.add_argument("--dry-run", action="store_true", help="list what would be assessed without calling the AI service")
    digest = commands.add_parser("digest", help="write due alert digests to the local outbox (no email is sent)")
    digest.add_argument("--outbox", help="folder for digest files (default: outbox/ next to the database)")
    digest.add_argument("--today", type=iso_date, default=None, help="pretend today is this date (for testing)")
    digest.add_argument("--dry-run", action="store_true", help="show what would be written without writing or logging")
    status = commands.add_parser("email-status", help="show whether email sending is on and what is missing (never prints secrets)")
    test = commands.add_parser("send-test", help="send ONE real test message to ONE allow-listed address (needs email fully set up)")
    test.add_argument("--to", required=True, help="the recipient; must be on EMAIL_ALLOWLIST")
    test.add_argument("--confirm-real-send", action="store_true", help="required: confirms you really want a message emailed")
    unsubscribe = commands.add_parser("unsubscribe", help="stop all email to the contact that owns this unsubscribe code")
    unsubscribe.add_argument("code")
    for command in (match, digest, unsubscribe, status, test):
        command.add_argument("--db", default=os.environ.get("GRANT_PLATFORM_DB") or str(db.DEFAULT_DB_PATH), help="SQLite database path")
    daily = commands.add_parser("run-daily", help="the scheduled routine: enabled sources, housekeeping, matching, digests, reminders; keeps a run log")
    daily.add_argument("--today", type=iso_date, default=None, help="pretend today is this date (for testing)")
    daily.add_argument("--log-dir", help="folder for run logs (default: logs/ next to the database)")
    daily.add_argument("--db", default=os.environ.get("GRANT_PLATFORM_DB") or str(db.DEFAULT_DB_PATH), help="SQLite database path")
    housekeep = commands.add_parser("housekeep", help="close grants whose recorded closing date has passed (nothing else is closed or deleted)")
    housekeep.add_argument("--today", type=iso_date, default=None, help="pretend today is this date (for testing)")
    housekeep.add_argument("--dry-run", action="store_true", help="show what would be closed without changing anything")
    housekeep.add_argument("--db", default=os.environ.get("GRANT_PLATFORM_DB") or str(db.DEFAULT_DB_PATH), help="SQLite database path")
    discover = commands.add_parser("discover", help="fetch one monitored source and save new grants as unverified")
    discover.add_argument("source", help="the source's short name, as listed by the sources command")
    discover.add_argument("--dry-run", action="store_true", help="fetch and show what would be saved, without writing anything")
    discover.add_argument("--max-pages", type=int, default=None, help="fetch at most this many pages (default: the source's own limit)")
    for command in (sources, discover):
        command.add_argument("--db", default=os.environ.get("GRANT_PLATFORM_DB") or str(db.DEFAULT_DB_PATH), help="SQLite database path")
    args = parser.parse_args(argv)
    if args.command == "sources":
        return run_sources(args)
    if args.command == "discover":
        return run_discover(args)
    if args.command == "housekeep":
        return run_housekeep(args)
    if args.command == "match":
        return run_match(args)
    if args.command == "digest":
        return run_digest(args)
    if args.command == "unsubscribe":
        return run_unsubscribe(args)
    if args.command == "run-daily":
        return run_daily(args)
    if args.command == "email-status":
        return run_email_status(args)
    if args.command == "send-test":
        return run_send_test(args)

    connection = db.connect(args.db)
    failures = []
    try:
        outbox = args.outbox or reminders.default_outbox(args.db)
        settings = email_settings()
        print(settings.describe())
        written = reminders.write_reminders(Repository(connection), outbox, args.today, args.days, args.dry_run,
                                            deliver=delivery.channel_for(settings, outbox), failures=failures)
    except OSError as error:
        print(f"Could not write to the outbox ({type(error).__name__}). Nothing was logged, so the reminders will be tried again next run", file=sys.stderr)
        return 1
    finally:
        connection.close()
    verb = "Would write" if args.dry_run else "Wrote"
    print(f"{verb} {len(written)} reminder{'s' if len(written) != 1 else ''}" + (f" to {outbox}" if written and not args.dry_run else ""))
    for name, subject in written:
        print(f"  {name}: {subject}")
    return report_failures(failures)


def email_settings():
    ai.load_env(names=delivery.ENV_NAMES)
    return delivery.EmailSettings.from_env()


def report_failures(failures):
    for address, reason in failures:
        print(f"  not sent to {address}: {reason}. It will be tried again next run", file=sys.stderr)
    return 1 if failures else 0


def run_email_status(args):
    print(email_settings().describe())
    return 0


def run_send_test(args):
    settings = email_settings()
    if not args.confirm_real_send:
        print("Nothing was sent. Add --confirm-real-send to confirm that you want a real message emailed", file=sys.stderr)
        return 1
    if not settings.enabled:
        print("Nothing was sent. " + settings.describe(), file=sys.stderr)
        return 1
    if not settings.allows(args.to):
        print("Nothing was sent. That address is not on EMAIL_ALLOWLIST", file=sys.stderr)
        return 1
    message = delivery.Message("Test recipient", args.to.strip(), "Test message from the Grant Management Platform",
                               "This is a test message from the Grant Management Platform development prototype. No action is needed.")
    try:
        delivery.send_smtp(settings, message)
    except delivery.DeliveryError as error:
        print(f"Not sent: {error}", file=sys.stderr)
        return 1
    print(f"Sent one test message to {args.to.strip()}")
    return 0


def run_daily(args):
    """Each step runs on its own; one failing step never stops the others. Output is kept in logs/daily-DATE.log.
    Email goes out only if email delivery is fully set up; otherwise messages are written to the outbox."""
    today = args.today or date.today()
    folder = Path(args.log_dir) if args.log_dir else Path(args.db).resolve().parent / "logs"
    connection = db.connect(args.db)
    try:
        repository = Repository(connection)
        discovery.register_sources(repository)
        enabled = [source["slug"] for source in repository.list_sources() if source["enabled"]]
    finally:
        connection.close()
    base = ["--db", args.db]
    day = ["--today", today.isoformat()]
    steps = [(f"discover {slug}", ["discover", slug, *base]) for slug in enabled]
    ai.load_env()
    steps += [("housekeep", ["housekeep", *day, *base])]
    if os.environ.get("OPENAI_API_KEY"):
        steps += [("match", ["match", *base])]
    steps += [("digest", ["digest", *day, *base]), ("notify", ["notify", *day, *base])]
    lines, worst = [f"Daily run for {today.isoformat()} ({'no sources are enabled' if not enabled else 'sources: ' + ', '.join(enabled)})"], 0
    if not os.environ.get("OPENAI_API_KEY"):
        lines.append("Matching skipped: AI assessment is not set up on this computer")
    for label, argv in steps:
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main(argv)
        except SystemExit as stop:
            code = stop.code or 1
        except Exception as error:  # a step must never stop the others, and the log must say why
            code = 1
            err.write(f"Unexpected {type(error).__name__}\n")
        worst = max(worst, 1 if code else 0)
        lines.append(f"--- {label}: {'ok' if not code else 'FAILED'}")
        lines += ["  " + line for line in (out.getvalue() + err.getvalue()).splitlines()]
    text = "\n".join(lines)
    print(text)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / f"daily-{today.isoformat()}.log", "a", encoding="utf-8") as log:
            log.write(text + "\n\n")
    except OSError as error:
        print(f"Could not write the run log ({type(error).__name__})", file=sys.stderr)
        worst = 1
    return worst


def run_sources(args):
    connection = db.connect(args.db)
    try:
        repository = Repository(connection)
        discovery.register_sources(repository)
        slug = args.enable or args.disable
        if slug:
            source = repository.get_source_by_slug(slug)
            if source is None:
                print(f"No source called {slug!r}", file=sys.stderr)
                return 1
            repository.set_source_enabled(source["id"], bool(args.enable))
        rows = repository.list_sources()
        if not rows:
            print("No monitored sources are set up yet")
        for source in rows:
            state = "enabled" if source["enabled"] else "disabled"
            print(f"{source['slug']}: {source['name']} ({source['publisher']}), {state}, at most {source['max_pages_per_run']} pages a run, "
                  f"{source['min_interval_seconds']:g}s between requests")
        return 0
    finally:
        connection.close()


def run_match(args, gateway=None):
    ai.load_env()
    connection = db.connect(args.db)
    try:
        repository = Repository(connection)
        if args.dry_run:
            todo = matching.outstanding(repository, model=ai.model_name())
            print(f"Would assess {min(len(todo), args.max_assessments)} of {len(todo)} outstanding grant{'s' if len(todo) != 1 else ''} (no AI calls made)")
            return 0
        gateway = gateway or ai.default_gateway()
        if gateway is None:
            print("AI assessment is not set up on this computer (no OPENAI_API_KEY), so nothing was assessed", file=sys.stderr)
            return 1
        assessed, errors, remaining = matching.assess_outstanding(repository, gateway=gateway, model=ai.model_name(), limit=args.max_assessments)
    finally:
        connection.close()
    print(f"Assessed {len(assessed)} grant{'s' if len(assessed) != 1 else ''}; {len(errors)} failed; {remaining} left for the next run")
    for message in errors.values():
        print(f"  problem: {message}")
    return 0


def run_digest(args):
    connection = db.connect(args.db)
    failures = []
    try:
        outbox = args.outbox or reminders.default_outbox(args.db)
        settings = email_settings()
        print(settings.describe())
        done = notifications.send_digests(Repository(connection), delivery.channel_for(settings, outbox), args.today, args.dry_run, failures=failures)
    except OSError as error:
        print(f"Could not write to the outbox ({type(error).__name__}). Nothing was logged, so the digests will be tried again next run", file=sys.stderr)
        return 1
    finally:
        connection.close()
    verb = "Would write" if args.dry_run else "Wrote"
    print(f"{verb} {len(done)} digest{'s' if len(done) != 1 else ''}" + (f" to {outbox}" if done and not args.dry_run else ""))
    for name, subject, address in done:
        print(f"  {name}: {subject} -> {address}")
    return 0


def run_unsubscribe(args):
    connection = db.connect(args.db)
    try:
        contact = notifications.unsubscribe(Repository(connection), args.code)
    finally:
        connection.close()
    if contact is None:
        print("That code does not match any contact", file=sys.stderr)
        return 1
    print(f"{contact['name']} will not receive any more email from this platform")
    return 0


def run_housekeep(args):
    connection = db.connect(args.db)
    try:
        repository = Repository(connection)
        today = args.today or date.today()
        if args.dry_run:
            closed = [grant["id"] for grant in repository.list_grants_past_deadline(today.isoformat())]
        else:
            closed = grants.close_expired(repository, today)
    finally:
        connection.close()
    verb = "Would close" if args.dry_run else "Closed"
    print(f"{verb} {len(closed)} grant{'s' if len(closed) != 1 else ''} whose closing date has passed")
    return 0


def run_discover(args):
    ai.load_env(names=(CONTACT_ENV,))
    connection = db.connect(args.db)
    try:
        repository = Repository(connection)
        discovery.register_sources(repository)
        summary = discovery.run(repository, args.source, dry_run=args.dry_run, max_pages=args.max_pages)
    except (LookupError, PermissionError, ValueError) as error:
        print(str(error).strip("'\""), file=sys.stderr)
        return 1
    finally:
        connection.close()
    prefix = "Dry run (nothing saved): " if summary.dry_run else ""
    print(f"{prefix}{summary.status}. Fetched {summary.pages_fetched} page{'s' if summary.pages_fetched != 1 else ''}; "
          f"{len(summary.new)} new grant{'s' if len(summary.new) != 1 else ''}, {len(summary.seen)} already saved")
    for item in summary.new:
        print(f"  new: {item if summary.dry_run else f'grant #{item}'}")
    for label, ids in (("changed", summary.updated), ("formatting only (left as saved)", summary.reformatted),
                       ("sent back to unverified", summary.reset), ("closed (closing date passed)", summary.closed),
                       ("review flag cleared", summary.cleared)):
        if ids:
            print(f"  {label}: {', '.join('#' + str(i) for i in ids)}")
    for grant_id, reason in summary.flagged:
        print(f"  needs review: #{grant_id}: {reason}")
    for title, address, kept in summary.duplicates:
        print(f"  duplicate (same funder and title; only the first is kept): {title}\n    skipped: {address}\n    kept:    {kept}")
    for problem in summary.problems:
        print(f"  problem: {problem}")
    return 0 if summary.status != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
