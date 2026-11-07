"""Discovery: run a monitored source's adapter and save what it finds as unverified grants.

Each source is a grant_source row plus one adapter. An adapter turns the source's pages into plain grant fields
(the same fields a person types on the Grants screen) and never decides verification. Fetched text is only ever
stored as data.

What a run does with a grant it has seen before (only grants this source found; hand-entered grants are never touched):
- unchanged: only last_checked_at moves;
- formatting changes only: nothing is rewritten, so nothing is re-assessed;
- details changed: the new details and a snapshot are saved, the change is logged, and a verified grant goes back to
  unverified with the reason;
- closing: a grant is closed only when the source gives a closing date that has passed; it is never reopened
  automatically (a person is asked to check);
- missing: a grant that has vanished from a *complete* listing, whose page has gone (404), or that is no longer open to
  non-profits is flagged for review, never closed or deleted. A failed or partial run flags nothing.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone

from grant_platform.services import ValidationError, changes as change_rules, grants
from grant_platform.services.fetcher import FetchError, PageLimitReached, PoliteFetcher, RobotsError, SourceFormatError
from grant_platform.services.sources import find_a_grant

# adapter name -> adapter factory. Sources are added one at a time, after the product owner approves each one.
ADAPTERS = {"find_a_grant": find_a_grant.FindAGrantAdapter}
# slug -> source definition (grant_source columns). Registered sources start disabled; enabling is a deliberate step.
SOURCES = {find_a_grant.SOURCE["slug"]: find_a_grant.SOURCE}


@dataclass
class RunSummary:
    source: str
    dry_run: bool
    status: str = "ok"
    pages_fetched: int = 0
    new: list = field(default_factory=list)
    seen: list = field(default_factory=list)
    updated: list = field(default_factory=list)
    reformatted: list = field(default_factory=list)
    reset: list = field(default_factory=list)
    closed: list = field(default_factory=list)
    flagged: list = field(default_factory=list)
    cleared: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    duplicates: list = field(default_factory=list)  # (title, address, address of the entry kept) for same funder and title seen twice in one run
    keys: dict = field(default_factory=dict)  # funder-and-title key -> address of the first entry this run
    run_id: int | None = None


def register_sources(repository, definitions=None):
    """Make sure every known source has a row. Existing rows keep their enabled flag."""
    return [repository.upsert_source(source) for source in (definitions or SOURCES).values()]


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _existing(repository, grant):
    return (grant.canonical_url and repository.find_grant_by_url(grant.canonical_url)) or repository.find_grant_by_dedupe_key(grant.dedupe_key)


def _short(value, limit=400):
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _snapshot(repository, grant_id, source_id, now, fields):
    repository.add_grant_snapshot(grant_id, source_id, now, change_rules.content_hash(fields), {name: fields.get(name) for name in (*change_rules.MATERIAL_FIELDS, "status")})


def _flag(repository, summary, grant_id, reason, now, flagged_now):
    flagged_now.add(grant_id)
    if summary.dry_run:
        summary.flagged.append((grant_id, reason))
    elif repository.flag_grant_for_review(grant_id, reason, now):
        repository.add_grant_event(grant_id, "flagged_for_review", {"reason": reason}, now, summary.run_id)
        summary.flagged.append((grant_id, reason))


def _save_new(repository, source, grant, summary, now):
    if summary.dry_run:
        summary.new.append(grant.title)
        return
    try:
        stored = grants.create_grant_record(repository, grant)
    except grants.DuplicateGrantError as error:
        summary.seen.append(error.existing["id"])
        return
    fields = grant.model_dump()
    repository.update_grant_details(stored["id"], {}, now, changed=True)
    repository.record_grant_check(stored["id"], now)
    _snapshot(repository, stored["id"], source["id"], now, fields)
    repository.add_grant_event(stored["id"], "found", {"source": source["slug"], "url": grant.canonical_url}, now, summary.run_id)
    summary.new.append(stored["id"])


def _update_owned(repository, source, stored, grant, summary, now, flagged_now):
    """Apply what the source now says to a grant this source found earlier."""
    incoming = grant.model_dump()
    found = change_rules.differences(stored, incoming)
    material = change_rules.details_changed(found)
    values = {}
    if material:
        values = {change["field"]: change["new"] for change in found if change["field"] != "status"}
        if "title" in values or "funder" in values:
            key = grants.dedupe_key(values.get("funder", stored["funder"]), values.get("title", stored["title"]))
            clash = repository.find_grant_by_dedupe_key(key)
            if clash and clash["id"] != stored["id"]:
                values.pop("title", None), values.pop("funder", None)
                _flag(repository, summary, stored["id"], f"The title or funder changed on the source but now matches saved grant #{clash['id']}. Check for a duplicate", now, flagged_now)
            else:
                values["dedupe_key"] = key
    status_change = next((change for change in found if change["field"] == "status"), None)
    if status_change:
        new_status = status_change["new"]
        if new_status == "closed" and stored["status"] != "closed":
            values["status"] = "closed"
        elif stored["status"] == "unknown" and new_status == "open":
            values["status"] = "open"
        elif stored["status"] == "closed" and new_status != "closed" and material and "deadline_date" in values:
            _flag(repository, summary, stored["id"], f"The source now shows a different closing date ({values['deadline_date'] or 'none'}) for a grant marked closed. Check it and reopen it if it is right", now, flagged_now)
    if summary.dry_run:
        if material or values:
            summary.updated.append(stored["id"])
        elif found:
            summary.reformatted.append(stored["id"])
        return
    if not material and not values:
        repository.touch_grant_seen(stored["id"], now)
        if found:
            repository.add_grant_event(stored["id"], "reformatted", {"fields": [change["field"] for change in found]}, now, summary.run_id)
            summary.reformatted.append(stored["id"])
        return
    reset_reason = None
    if material and stored["verification_status"] == "verified":
        labels = ", ".join(change_rules.FIELD_LABELS[change["field"]] for change in found if change["material"] and change["field"] != "status")
        reset_reason = f"The source changed on {now[:10]} ({labels}). Check the details again"
    repository.update_grant_details(stored["id"], values, now, changed=bool(material), reset_reason=reset_reason)
    repository.record_grant_check(stored["id"], now)
    if material:
        fresh = {**incoming, **{name: value for name, value in values.items() if name in incoming}}
        _snapshot(repository, stored["id"], source["id"], now, fresh)
        repository.add_grant_event(stored["id"], "updated", {"changes": [
            {"field": change["field"], "old": _short(change["old"]), "new": _short(change["new"])}
            for change in found if change["field"] in values], "material": True}, now, summary.run_id)
        summary.updated.append(stored["id"])
    if reset_reason:
        repository.add_grant_event(stored["id"], "verification_reset", {
            "reason": reset_reason, "previously_verified_by": stored["verified_by"], "previously_verified_at": stored["verified_at"]}, now, summary.run_id)
        summary.reset.append(stored["id"])
    if values.get("status") == "closed":
        repository.add_grant_event(stored["id"], "closed_by_deadline", {"deadline_date": incoming["deadline_date"], "source": source["slug"]}, now, summary.run_id)
        summary.closed.append(stored["id"])


def _save(repository, source, raw, summary, now, seen_urls, flagged_now):
    try:
        grant = grants.clean_grant(raw, "monitored")
    except ValidationError as error:
        summary.problems.append(f"Skipped an entry that could not be read ({raw.get('source_url') or raw.get('title') or 'no address'}): {error}")
        return
    grant = grant.model_copy(update={"source_id": source["id"]})
    first = summary.keys.setdefault(grant.dedupe_key, grant.canonical_url or grant.title)
    if first != (grant.canonical_url or grant.title):
        # The same funder and title listed at a second address in this run. Keep the first only; letting both through would
        # have them overwrite each other every run, resetting verification each time.
        summary.duplicates.append((grant.title, grant.canonical_url, first))
        seen_urls.add(grant.canonical_url)
        return
    seen_urls.add(grant.canonical_url)
    stored = _existing(repository, grant)
    if stored is None:
        _save_new(repository, source, grant, summary, now)
        return
    summary.seen.append(stored["id"])
    if stored["source_id"] != source["id"]:
        return  # entered by hand or found by another source: never overwritten here
    _update_owned(repository, source, stored, grant, summary, now, flagged_now)
    if stored["review_reason"] and stored["id"] not in flagged_now and not summary.dry_run:
        if repository.clear_grant_review(stored["id"]):
            repository.add_grant_event(stored["id"], "review_cleared", {"reason": "The source lists this grant again"}, now, summary.run_id)
            summary.cleared.append(stored["id"])


def _flag_missing(repository, source, adapter, seen_urls, summary, now, flagged_now):
    """Only after a complete listing: flag (never close) grants this source found that it no longer offers."""
    listed = {grants.normalise_url(url) for url in getattr(adapter, "listed_urls", ())}
    gone = {grants.normalise_url(url) for url in getattr(adapter, "unavailable_urls", ())}
    unreadable = {grants.normalise_url(url) for url in getattr(adapter, "skipped_urls", ())}
    for stored in repository.find_grants_by_source(source["id"]):
        url = stored["canonical_url"]
        if not url or url in seen_urls or url in unreadable or stored["status"] == "closed":
            continue
        if url in gone:
            reason = "The source's page for this grant has gone. It may have closed or moved; check the source"
        elif url in listed:
            reason = "The source still lists this grant but no longer shows it as open to non-profit organisations. Check the source"
        else:
            reason = "This grant is no longer in the source's list. It may have closed or moved; check the source"
        _flag(repository, summary, stored["id"], reason, now, flagged_now)


def run(repository, source_slug, *, dry_run=False, adapters=None, fetcher=None, max_pages=None, now=None):
    """Run one enabled source. Returns a RunSummary; dry runs fetch but write nothing (no grants, no run record).
    A dry run may be requested for a disabled source so it can be reviewed before it is enabled; a real run never can."""
    source = repository.get_source_by_slug(source_slug)
    if source is None:
        raise LookupError(f"No source called {source_slug!r}. Known sources: {', '.join(s['slug'] for s in repository.list_sources()) or 'none'}")
    if not source["enabled"] and not dry_run:
        raise PermissionError(f"Source {source_slug!r} is not enabled. Run it with --dry-run first, review that, then enable it")
    factory = (adapters or ADAPTERS).get(source["adapter"])
    if factory is None:
        raise LookupError(f"No adapter called {source['adapter']!r}")
    fetcher = fetcher or PoliteFetcher(source["base_url"], min_interval=source["min_interval_seconds"],
                                       redirect_hosts=getattr(factory, "redirect_hosts", ()),
                                       max_pages=max_pages or source["max_pages_per_run"])
    now = now or utc_now()
    summary = RunSummary(source=source_slug, dry_run=dry_run)
    adapter, seen_urls, flagged_now = None, set(), set()
    if not dry_run:
        summary.run_id = repository.start_discovery_run(source["id"])
    try:
        adapter = factory()
        for raw in adapter.discover(fetcher):
            _save(repository, source, raw, summary, now, seen_urls, flagged_now)
        if getattr(adapter, "complete", False):
            _flag_missing(repository, source, adapter, seen_urls, summary, now, flagged_now)
    except SourceFormatError as error:
        summary.status = "partial" if summary.new or summary.seen else "failed"
        summary.problems.append(f"Stopped: {error}")
    except PageLimitReached as error:
        summary.status = "partial"
        summary.problems.append(str(error))
    except RobotsError as error:
        summary.status = "failed"
        summary.problems.append(f"Stopped: {error}")
    except FetchError as error:
        summary.status = "partial" if summary.new or summary.seen else "failed"
        summary.problems.append(f"Stopped: {error} ({error.url})")
    finally:
        summary.problems.extend(getattr(adapter, "problems", ()))
        summary.pages_fetched = fetcher.pages_fetched
        if summary.run_id is not None:
            repository.finish_discovery_run(
                summary.run_id, status=summary.status, pages_fetched=summary.pages_fetched,
                grants_new=len(summary.new), grants_seen=len(summary.seen), problems=summary.problems,
            )
    return summary
