"""All SQL lives here. Every organisation-owned read and write takes organisation_id."""
import json

from grant_platform.models import EvidenceRecord, FundingNeed, Grant, Organisation


_ORG_JSON = ("regions", "field_sources")


def _organisation(row):
    if row is None:
        return None
    values = dict(row)
    for field in _ORG_JSON:
        values[field] = json.loads(values[field])
    return values


def _evidence(row):
    if row is None:
        return None
    values = dict(row)
    values["extra"] = json.loads(values["extra"])
    return values


class Repository:
    def __init__(self, connection):
        self.connection = connection

    # Organisations

    def create_organisation(self, organisation: Organisation):
        values = organisation.model_dump()
        for field in _ORG_JSON:
            values[field] = json.dumps(values[field], ensure_ascii=False)
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        with self.connection:
            cursor = self.connection.execute(
                f"INSERT INTO organisation ({columns}) VALUES ({placeholders})", values
            )
        return cursor.lastrowid

    def get_organisation(self, organisation_id):
        return _organisation(self.connection.execute(
            "SELECT * FROM organisation WHERE id = ?", (organisation_id,)
        ).fetchone())

    def find_organisation_id(self, slug):
        row = self.connection.execute(
            "SELECT id FROM organisation WHERE slug = ?", (slug,)
        ).fetchone()
        return row["id"] if row else None

    # Evidence records

    def add_evidence(self, organisation_id, record: EvidenceRecord):
        """Insert a record once; an existing record_key for this organisation is left untouched."""
        values = record.model_dump()
        values["extra"] = json.dumps(values["extra"], ensure_ascii=False)
        values["organisation_id"] = organisation_id
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        with self.connection:
            cursor = self.connection.execute(
                f"INSERT OR IGNORE INTO evidence_record ({columns}) VALUES ({placeholders})", values
            )
        return cursor.rowcount == 1

    def get_evidence(self, organisation_id, record_key):
        return _evidence(self.connection.execute(
            "SELECT * FROM evidence_record WHERE organisation_id = ? AND record_key = ?",
            (organisation_id, record_key),
        ).fetchone())

    def list_evidence(self, organisation_id, *, usable_externally=False):
        rows = self.connection.execute(
            "SELECT * FROM evidence_record WHERE organisation_id = ? ORDER BY id",
            (organisation_id,),
        ).fetchall()
        records = [_evidence(row) for row in rows]
        if usable_externally:
            records = [
                record for record in records
                if EvidenceRecord.model_validate(record).usable_externally()
            ]
        return records

    # Funding needs

    def add_funding_need(self, organisation_id, need: FundingNeed):
        """Insert a need once; an existing title for this organisation is left untouched."""
        values = need.model_dump()
        values["organisation_id"] = organisation_id
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        with self.connection:
            cursor = self.connection.execute(
                f"INSERT OR IGNORE INTO funding_need ({columns}) VALUES ({placeholders})", values
            )
        return cursor.rowcount == 1

    def get_funding_need(self, organisation_id, need_id):
        row = self.connection.execute(
            "SELECT * FROM funding_need WHERE organisation_id = ? AND id = ?",
            (organisation_id, need_id),
        ).fetchone()
        return dict(row) if row else None

    def list_funding_needs(self, organisation_id):
        rows = self.connection.execute(
            "SELECT * FROM funding_need WHERE organisation_id = ? ORDER BY id",
            (organisation_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    # Editing (callers validate first; these only persist)

    def list_organisations(self):
        rows = self.connection.execute("SELECT * FROM organisation ORDER BY name COLLATE NOCASE, id").fetchall()
        return [_organisation(row) for row in rows]

    def update_organisation(self, organisation_id, organisation: Organisation):
        """Replace the editable profile fields. The slug never changes."""
        values = organisation.model_dump(exclude={"slug"})
        for field in _ORG_JSON:
            values[field] = json.dumps(values[field], ensure_ascii=False)
        assignments = ", ".join(f"{name} = :{name}" for name in values)
        values["id"] = organisation_id
        with self.connection:
            cursor = self.connection.execute(
                f"UPDATE organisation SET {assignments}, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = :id",
                values,
            )
        return cursor.rowcount == 1

    def update_funding_need(self, organisation_id, need_id, need: FundingNeed):
        """Replace a need's fields; scoped to the organisation. Raises sqlite3.IntegrityError on a duplicate title."""
        values = need.model_dump()
        assignments = ", ".join(f"{name} = :{name}" for name in values)
        values.update(organisation_id=organisation_id, id=need_id)
        with self.connection:
            cursor = self.connection.execute(
                f"UPDATE funding_need SET {assignments} WHERE organisation_id = :organisation_id AND id = :id",
                values,
            )
        return cursor.rowcount == 1

    def get_funding_need_by_title(self, organisation_id, title):
        row = self.connection.execute(
            "SELECT * FROM funding_need WHERE organisation_id = ? AND title = ?",
            (organisation_id, title),
        ).fetchone()
        return dict(row) if row else None

    # Grants (shared reference data: no organisation_id)

    def add_grant(self, grant: Grant):
        """Insert a grant and return its id. Raises sqlite3.IntegrityError on a duplicate URL or dedupe key."""
        values = grant.model_dump()
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        with self.connection:
            cursor = self.connection.execute(
                f"INSERT INTO grant_record ({columns}) VALUES ({placeholders})", values
            )
        return cursor.lastrowid

    def get_grant(self, grant_id):
        row = self.connection.execute("SELECT * FROM grant_record WHERE id = ?", (grant_id,)).fetchone()
        return dict(row) if row else None

    def find_grant_by_url(self, canonical_url):
        row = self.connection.execute(
            "SELECT * FROM grant_record WHERE canonical_url = ?", (canonical_url,)
        ).fetchone()
        return dict(row) if row else None

    def find_grant_by_dedupe_key(self, dedupe_key):
        row = self.connection.execute(
            "SELECT * FROM grant_record WHERE dedupe_key = ?", (dedupe_key,)
        ).fetchone()
        return dict(row) if row else None

    def list_grants(self):
        """Soonest deadline first; grants with no parsed deadline last, then newest first."""
        rows = self.connection.execute(
            "SELECT * FROM grant_record ORDER BY deadline_date IS NULL, deadline_date, id DESC"
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_grant_verified(self, grant_id, verified_by):
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE grant_record SET verification_status = 'verified', verified_by = ?, "
                "verified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                (verified_by, grant_id),
            )
        return cursor.rowcount == 1

    def find_grants_by_source(self, source_id):
        rows = self.connection.execute("SELECT * FROM grant_record WHERE source_id = ? ORDER BY id", (source_id,)).fetchall()
        return [dict(row) for row in rows]

    # Monitored sources and discovery runs (shared reference data: no organisation_id)

    def _source(self, row):
        if row is None:
            return None
        values = dict(row)
        values["coverage"] = json.loads(values["coverage"])
        values["enabled"] = bool(values["enabled"])
        return values

    def upsert_source(self, source):
        """Insert or refresh a source definition by slug. Never changes whether it is enabled."""
        values = {**source, "coverage": json.dumps(list(source.get("coverage", ())))}
        columns = ("slug", "name", "publisher", "base_url", "adapter", "coverage", "licence", "terms_url",
                   "terms_checked_on", "min_interval_seconds", "max_pages_per_run")
        values = {name: values.get(name) for name in columns}
        updates = ", ".join(f"{name} = excluded.{name}" for name in columns if name != "slug")
        with self.connection:
            self.connection.execute(
                f"INSERT INTO grant_source ({', '.join(columns)}) VALUES ({', '.join(':' + c for c in columns)}) "
                f"ON CONFLICT (slug) DO UPDATE SET {updates}", values,
            )
        return self.get_source_by_slug(source["slug"])

    def get_source(self, source_id):
        return self._source(self.connection.execute("SELECT * FROM grant_source WHERE id = ?", (source_id,)).fetchone())

    def get_source_by_slug(self, slug):
        return self._source(self.connection.execute("SELECT * FROM grant_source WHERE slug = ?", (slug,)).fetchone())

    def list_sources(self):
        return [self._source(row) for row in self.connection.execute("SELECT * FROM grant_source ORDER BY name").fetchall()]

    def set_source_enabled(self, source_id, enabled):
        with self.connection:
            cursor = self.connection.execute("UPDATE grant_source SET enabled = ? WHERE id = ?", (int(bool(enabled)), source_id))
        return cursor.rowcount == 1

    def start_discovery_run(self, source_id):
        with self.connection:
            cursor = self.connection.execute("INSERT INTO discovery_run (source_id) VALUES (?)", (source_id,))
        return cursor.lastrowid

    def finish_discovery_run(self, run_id, *, status, pages_fetched, grants_new, grants_seen, problems):
        with self.connection:
            self.connection.execute(
                "UPDATE discovery_run SET status = ?, pages_fetched = ?, grants_new = ?, grants_seen = ?, problems = ?, "
                "finished_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
                (status, pages_fetched, grants_new, grants_seen, json.dumps(list(problems)), run_id),
            )

    def list_discovery_runs(self, source_id=None, limit=20):
        query, params = "SELECT * FROM discovery_run", ()
        if source_id is not None:
            query, params = query + " WHERE source_id = ?", (source_id,)
        rows = self.connection.execute(query + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        return [{**dict(row), "problems": json.loads(row["problems"])} for row in rows]

    # Grant change tracking: checks, snapshots, events and review flags (shared reference data)

    def record_grant_check(self, grant_id, when):
        with self.connection:
            self.connection.execute("UPDATE grant_record SET last_checked_at = ? WHERE id = ?", (when, grant_id))

    def add_grant_snapshot(self, grant_id, source_id, when, content_hash, fields):
        with self.connection:
            self.connection.execute(
                "INSERT INTO grant_snapshot (grant_id, source_id, taken_at, content_hash, fields) VALUES (?, ?, ?, ?, ?)",
                (grant_id, source_id, when, content_hash, json.dumps(fields, sort_keys=True)),
            )

    def latest_grant_snapshot(self, grant_id):
        row = self.connection.execute("SELECT * FROM grant_snapshot WHERE grant_id = ? ORDER BY id DESC LIMIT 1", (grant_id,)).fetchone()
        return {**dict(row), "fields": json.loads(row["fields"])} if row else None

    def list_grant_snapshots(self, grant_id):
        rows = self.connection.execute("SELECT * FROM grant_snapshot WHERE grant_id = ? ORDER BY id", (grant_id,)).fetchall()
        return [{**dict(row), "fields": json.loads(row["fields"])} for row in rows]

    def add_grant_event(self, grant_id, kind, detail, when, run_id=None):
        with self.connection:
            self.connection.execute(
                "INSERT INTO grant_event (grant_id, run_id, kind, detail, created_at) VALUES (?, ?, ?, ?, ?)",
                (grant_id, run_id, kind, json.dumps(detail, sort_keys=True), when),
            )

    def list_grant_events(self, grant_id):
        rows = self.connection.execute("SELECT * FROM grant_event WHERE grant_id = ? ORDER BY id DESC", (grant_id,)).fetchall()
        return [{**dict(row), "detail": json.loads(row["detail"])} for row in rows]

    def update_grant_details(self, grant_id, values, when, *, changed=True, reset_reason=None):
        """Apply new detail values. changed=True stamps last_changed_at. reset_reason sends a verified grant back to unverified."""
        allowed = ("title", "funder", "purpose", "eligibility", "scope", "funding", "duration", "deadline_text", "deadline_date",
                   "key_requirements", "exclusions", "status", "dedupe_key")
        unknown = set(values) - set(allowed)
        if unknown:
            raise ValueError(f"Cannot update {sorted(unknown)}")
        assignments = [f"{name} = :{name}" for name in values]
        params = {**values, "id": grant_id, "when": when}
        assignments.append("last_seen_at = :when")
        if changed:
            assignments.append("last_changed_at = :when")
        if reset_reason:
            assignments += ["verification_status = 'unverified'", "verified_by = NULL", "verified_at = NULL",
                            "verification_reset_at = :when", "verification_reset_reason = :reason"]
            params["reason"] = reset_reason
        with self.connection:
            self.connection.execute(f"UPDATE grant_record SET {', '.join(assignments)} WHERE id = :id", params)

    def touch_grant_seen(self, grant_id, when):
        with self.connection:
            self.connection.execute("UPDATE grant_record SET last_seen_at = ?, last_checked_at = ? WHERE id = ?", (when, when, grant_id))

    def flag_grant_for_review(self, grant_id, reason, when):
        """Returns True when the flag is new or its reason changed."""
        current = self.get_grant(grant_id)
        if current["review_reason"] == reason:
            return False
        with self.connection:
            self.connection.execute("UPDATE grant_record SET review_reason = ?, review_flagged_at = ? WHERE id = ?", (reason, when, grant_id))
        return True

    def clear_grant_review(self, grant_id):
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE grant_record SET review_reason = NULL, review_flagged_at = NULL WHERE id = ? AND review_reason IS NOT NULL", (grant_id,))
        return cursor.rowcount == 1

    def set_grant_status(self, grant_id, status):
        with self.connection:
            self.connection.execute("UPDATE grant_record SET status = ? WHERE id = ?", (status, grant_id))

    def list_grants_to_review(self):
        rows = self.connection.execute("SELECT * FROM grant_record WHERE review_reason IS NOT NULL ORDER BY review_flagged_at, id").fetchall()
        return [dict(row) for row in rows]

    def list_grants_past_deadline(self, today_iso):
        """Not-yet-closed grants whose parsed deadline date is before today."""
        rows = self.connection.execute(
            "SELECT * FROM grant_record WHERE status != 'closed' AND deadline_date IS NOT NULL AND deadline_date < ? ORDER BY id", (today_iso,)).fetchall()
        return [dict(row) for row in rows]

    # Notification contacts and the message log (organisation-scoped: a contact is only reached through its organisation)

    _CONTACT_FLAGS = ("active", "include_unclear", "verified_only", "reminders_enabled")
    CONTACT_PREFERENCES = ("alerts_frequency", "digest_weekday", "min_band", "include_unclear", "verified_only",
                           "reminders_enabled", "alerts_since")

    def _contact(self, row):
        if row is None:
            return None
        values = dict(row)
        for name in self._CONTACT_FLAGS:
            values[name] = bool(values[name])
        return values

    def add_contact(self, organisation_id, values):
        columns = ("name", "email", "consent_given_at", "consent_wording", "consent_recorded_by", "unsubscribe_token",
                   "alerts_frequency", "digest_weekday", "min_band", "include_unclear", "verified_only", "reminders_enabled",
                   "alerts_since", "created_at", "updated_at")
        row = {name: values[name] for name in columns if name in values}  # unset columns take the table's defaults
        columns = tuple(row)
        with self.connection:
            cursor = self.connection.execute(
                f"INSERT INTO notification_contact (organisation_id, {', '.join(columns)}) VALUES (:organisation_id, {', '.join(':' + c for c in columns)})",
                {**row, "organisation_id": organisation_id},
            )
        return cursor.lastrowid

    def get_contact(self, organisation_id, contact_id):
        return self._contact(self.connection.execute(
            "SELECT * FROM notification_contact WHERE organisation_id = ? AND id = ?", (organisation_id, contact_id)).fetchone())

    def find_contact_by_email(self, organisation_id, email):
        return self._contact(self.connection.execute(
            "SELECT * FROM notification_contact WHERE organisation_id = ? AND email = ?", (organisation_id, email)).fetchone())

    def find_contact_by_token(self, token):
        """The unsubscribe token is the secret that identifies one contact, so this is the one lookup not scoped by organisation."""
        return self._contact(self.connection.execute("SELECT * FROM notification_contact WHERE unsubscribe_token = ?", (token,)).fetchone())

    def list_contacts(self, organisation_id):
        rows = self.connection.execute("SELECT * FROM notification_contact WHERE organisation_id = ? ORDER BY id", (organisation_id,)).fetchall()
        return [self._contact(row) for row in rows]

    def update_contact(self, organisation_id, contact_id, values, when):
        allowed = ("name", "email", "consent_given_at", "consent_wording", "consent_recorded_by", "active", "unsubscribed_at",
                   *self.CONTACT_PREFERENCES)
        unknown = set(values) - set(allowed)
        if unknown:
            raise ValueError(f"Cannot update {sorted(unknown)}")
        assignments = ", ".join(f"{name} = :{name}" for name in values)
        with self.connection:
            cursor = self.connection.execute(
                f"UPDATE notification_contact SET {assignments + ', ' if assignments else ''}updated_at = :when WHERE organisation_id = :organisation_id AND id = :id",
                {**values, "when": when, "organisation_id": organisation_id, "id": contact_id},
            )
        return cursor.rowcount == 1

    def has_message(self, contact_id, kind, trigger_key):
        return self.connection.execute(
            "SELECT 1 FROM message_log WHERE contact_id = ? AND kind = ? AND trigger_key = ?", (contact_id, kind, trigger_key)).fetchone() is not None

    def log_message(self, organisation_id, contact_id, kind, trigger_key, when, outbox_file=None):
        """Record that a message was produced for a contact. Raises sqlite3.IntegrityError if it already was, or LookupError if the
        contact does not belong to the organisation. Does not commit: the caller wraps it in `with connection:` together with
        the delivery, so a failed delivery leaves no log behind."""
        if self.get_contact(organisation_id, contact_id) is None:
            raise LookupError("No such contact for this organisation")
        self.connection.execute(
            "INSERT INTO message_log (organisation_id, contact_id, kind, trigger_key, outbox_file, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (organisation_id, contact_id, kind, trigger_key, outbox_file, when),
        )

    def list_messages(self, organisation_id, kind=None, contact_id=None):
        query, params = "SELECT * FROM message_log WHERE organisation_id = ?", [organisation_id]
        for column, value in (("kind", kind), ("contact_id", contact_id)):
            if value is not None:
                query += f" AND {column} = ?"
                params.append(value)
        return [dict(row) for row in self.connection.execute(query + " ORDER BY id", params).fetchall()]

    def alerted_grant_ids(self, contact_id):
        rows = self.connection.execute("SELECT trigger_key FROM message_log WHERE contact_id = ? AND kind = 'grant_alert'", (contact_id,)).fetchall()
        return {int(row["trigger_key"].removeprefix("grant:")) for row in rows}

    # Matches (always organisation-scoped; funding_need_id None means the whole organisation)

    _MATCH_JSON = ("eligibility_items", "suitability", "exclusion_conflict", "uncertainties", "missing_information")

    def _match(self, row):
        if row is None:
            return None
        values = dict(row)
        for field in self._MATCH_JSON:
            values[field] = json.loads(values[field])
        values["grant_verified"] = bool(values["grant_verified"])
        return values

    def save_match(self, organisation_id, funding_need_id, grant_id, result):
        """Store (replacing any earlier result for the same organisation, need and grant)."""
        values = {**result, "organisation_id": organisation_id, "funding_need_id": funding_need_id, "grant_id": grant_id}
        for field in self._MATCH_JSON:
            values[field] = json.dumps(values[field], ensure_ascii=False)
        values["grant_verified"] = int(values["grant_verified"])
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        with self.connection:
            self.connection.execute(
                "DELETE FROM match_result WHERE organisation_id = ? AND COALESCE(funding_need_id, 0) = ? AND grant_id = ?",
                (organisation_id, funding_need_id or 0, grant_id),
            )
            self.connection.execute(f"INSERT INTO match_result ({columns}) VALUES ({placeholders})", values)
        return self.get_match(organisation_id, funding_need_id, grant_id)

    def get_match(self, organisation_id, funding_need_id, grant_id):
        return self._match(self.connection.execute(
            "SELECT * FROM match_result WHERE organisation_id = ? AND COALESCE(funding_need_id, 0) = ? AND grant_id = ?",
            (organisation_id, funding_need_id or 0, grant_id),
        ).fetchone())

    def list_matches(self, organisation_id, funding_need_id=None):
        rows = self.connection.execute(
            "SELECT * FROM match_result WHERE organisation_id = ? AND COALESCE(funding_need_id, 0) = ? ORDER BY id",
            (organisation_id, funding_need_id or 0),
        ).fetchall()
        return [self._match(row) for row in rows]

    # Applications (organisation-scoped; sub-records are reached only through an application the organisation owns)

    _APPLICATION_SELECT = (
        "SELECT a.*, g.title AS grant_title, g.funder AS grant_funder, g.deadline_date, g.deadline_text, "
        "g.status AS grant_status, n.title AS need_title FROM application a "
        "JOIN grant_record g ON g.id = a.grant_id LEFT JOIN funding_need n ON n.id = a.funding_need_id "
    )

    def create_application(self, organisation_id, funding_need_id, grant_id, status):
        """Raises sqlite3.IntegrityError if this organisation already has one for the same need (or none) and grant."""
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO application (organisation_id, funding_need_id, grant_id, status) VALUES (?, ?, ?, ?)",
                (organisation_id, funding_need_id, grant_id, status),
            )
        return cursor.lastrowid

    def get_application(self, organisation_id, application_id):
        row = self.connection.execute(
            self._APPLICATION_SELECT + "WHERE a.organisation_id = ? AND a.id = ?", (organisation_id, application_id)
        ).fetchone()
        return dict(row) if row else None

    def find_application(self, organisation_id, funding_need_id, grant_id):
        row = self.connection.execute(
            self._APPLICATION_SELECT + "WHERE a.organisation_id = ? AND COALESCE(a.funding_need_id, 0) = ? AND a.grant_id = ?",
            (organisation_id, funding_need_id or 0, grant_id),
        ).fetchone()
        return dict(row) if row else None

    def list_applications(self, organisation_id):
        rows = self.connection.execute(
            self._APPLICATION_SELECT + "WHERE a.organisation_id = ? ORDER BY a.id", (organisation_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def update_application(self, organisation_id, application_id, **fields):
        allowed = {"status", "outcome_note", "amount_awarded", "submitted_at"}
        if not fields or not set(fields) <= allowed:
            raise ValueError(f"Can only update {sorted(allowed)}")
        assignments = ", ".join(f"{name} = :{name}" for name in fields)
        with self.connection:
            cursor = self.connection.execute(
                f"UPDATE application SET {assignments}, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                "WHERE organisation_id = :organisation_id AND id = :id",
                {**fields, "organisation_id": organisation_id, "id": application_id},
            )
        return cursor.rowcount == 1

    def _owned(self, organisation_id, application_id):
        return self.get_application(organisation_id, application_id) is not None

    def add_application_event(self, organisation_id, application_id, from_status, to_status, note=None):
        if not self._owned(organisation_id, application_id):
            raise LookupError(f"No application {application_id} for organisation {organisation_id}")
        with self.connection:
            self.connection.execute(
                "INSERT INTO application_event (application_id, from_status, to_status, note) VALUES (?, ?, ?, ?)",
                (application_id, from_status, to_status, note),
            )

    def list_application_events(self, organisation_id, application_id):
        rows = self.connection.execute(
            "SELECT e.* FROM application_event e JOIN application a ON a.id = e.application_id "
            "WHERE a.organisation_id = ? AND e.application_id = ? ORDER BY e.id",
            (organisation_id, application_id),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_requirement(self, organisation_id, application_id, text, source):
        if not self._owned(organisation_id, application_id):
            raise LookupError(f"No application {application_id} for organisation {organisation_id}")
        with self.connection:
            self.connection.execute(
                "INSERT INTO application_requirement (application_id, text, source) VALUES (?, ?, ?)",
                (application_id, text, source),
            )

    def list_requirements(self, organisation_id, application_id):
        rows = self.connection.execute(
            "SELECT r.* FROM application_requirement r JOIN application a ON a.id = r.application_id "
            "WHERE a.organisation_id = ? AND r.application_id = ? ORDER BY r.id",
            (organisation_id, application_id),
        ).fetchall()
        return [{**dict(row), "done": bool(row["done"])} for row in rows]

    def set_requirement_done(self, organisation_id, application_id, requirement_id, done):
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE application_requirement SET done = ? WHERE id = ? AND application_id = ? AND application_id IN "
                "(SELECT id FROM application WHERE organisation_id = ?)",
                (int(done), requirement_id, application_id, organisation_id),
            )
        return cursor.rowcount == 1

    def add_task(self, organisation_id, application_id, text, due_date):
        if not self._owned(organisation_id, application_id):
            raise LookupError(f"No application {application_id} for organisation {organisation_id}")
        with self.connection:
            self.connection.execute(
                "INSERT INTO application_task (application_id, text, due_date) VALUES (?, ?, ?)",
                (application_id, text, due_date),
            )

    def list_tasks(self, organisation_id, application_id):
        rows = self.connection.execute(
            "SELECT t.* FROM application_task t JOIN application a ON a.id = t.application_id "
            "WHERE a.organisation_id = ? AND t.application_id = ? ORDER BY t.done, t.due_date IS NULL, t.due_date, t.id",
            (organisation_id, application_id),
        ).fetchall()
        return [{**dict(row), "done": bool(row["done"])} for row in rows]

    def set_task_done(self, organisation_id, application_id, task_id, done):
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE application_task SET done = ? WHERE id = ? AND application_id = ? AND application_id IN "
                "(SELECT id FROM application WHERE organisation_id = ?)",
                (int(done), task_id, application_id, organisation_id),
            )
        return cursor.rowcount == 1

    # Draft answers (reached only through an application the organisation owns)

    _DRAFT_JSON = ("citations", "missing_evidence", "review_flags")

    def _draft(self, row):
        if row is None:
            return None
        values = dict(row)
        for field in self._DRAFT_JSON:
            values[field] = json.loads(values[field])
        values["reviewed_by_user"] = bool(values["reviewed_by_user"])
        return values

    def create_draft(self, organisation_id, application_id, draft):
        if not self._owned(organisation_id, application_id):
            raise LookupError(f"No application {application_id} for organisation {organisation_id}")
        values = {**draft, "application_id": application_id}
        for field in self._DRAFT_JSON:
            values[field] = json.dumps(values[field], ensure_ascii=False)
        columns = ", ".join(values)
        placeholders = ", ".join(f":{name}" for name in values)
        with self.connection:
            cursor = self.connection.execute(f"INSERT INTO draft_answer ({columns}) VALUES ({placeholders})", values)
        return self.get_draft(organisation_id, cursor.lastrowid)

    def get_draft(self, organisation_id, draft_id):
        return self._draft(self.connection.execute(
            "SELECT d.* FROM draft_answer d JOIN application a ON a.id = d.application_id "
            "WHERE a.organisation_id = ? AND d.id = ?", (organisation_id, draft_id),
        ).fetchone())

    def list_drafts(self, organisation_id, application_id):
        rows = self.connection.execute(
            "SELECT d.* FROM draft_answer d JOIN application a ON a.id = d.application_id "
            "WHERE a.organisation_id = ? AND d.application_id = ? ORDER BY d.id DESC", (organisation_id, application_id),
        ).fetchall()
        return [self._draft(row) for row in rows]

    def update_draft(self, organisation_id, draft_id, *, edited_text, reviewed_by_user):
        """Store the person's edits (ai_text is never touched) and whether they have reviewed the draft."""
        with self.connection:
            cursor = self.connection.execute(
                "UPDATE draft_answer SET edited_text = ?, reviewed_by_user = ?, modified_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
                "WHERE id = ? AND application_id IN (SELECT id FROM application WHERE organisation_id = ?)",
                (edited_text, int(reviewed_by_user), draft_id, organisation_id),
            )
        return cursor.rowcount == 1

    # Notification log: one row per reminder already written, so a reminder is never repeated

    def log_notification(self, application_id, trigger_key, outbox_file=None):
        """Record a reminder. Returns False, changing nothing, if this application already has this trigger."""
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO notification_log (application_id, trigger_key, outbox_file) VALUES (?, ?, ?)",
            (application_id, trigger_key, outbox_file),
        )
        return cursor.rowcount == 1

    def list_notifications(self, organisation_id, application_id=None):
        rows = self.connection.execute(
            "SELECT n.* FROM notification_log n JOIN application a ON a.id = n.application_id "
            "WHERE a.organisation_id = ? AND (? IS NULL OR n.application_id = ?) ORDER BY n.id",
            (organisation_id, application_id, application_id),
        ).fetchall()
        return [dict(row) for row in rows]
