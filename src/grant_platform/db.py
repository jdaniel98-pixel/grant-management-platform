"""SQLite connection and numbered schema migrations (PRAGMA user_version)."""
import sqlite3
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "instance" / "grant_platform.sqlite3"

MIGRATIONS = (
    """
    CREATE TABLE organisation (
        id INTEGER PRIMARY KEY,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        organisation_type TEXT,
        registration_country TEXT,
        registration_number TEXT,
        legal_form TEXT,
        founded_year INTEGER,
        annual_income_band TEXT,
        regions TEXT NOT NULL DEFAULT '[]',
        mission TEXT,
        activities TEXT,
        beneficiaries TEXT,
        field_sources TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE TABLE evidence_record (
        id INTEGER PRIMARY KEY,
        organisation_id INTEGER NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
        record_key TEXT NOT NULL,
        text TEXT NOT NULL,
        knowledge_type TEXT NOT NULL,
        topic TEXT,
        source TEXT,
        external_use TEXT NOT NULL,
        approval_status TEXT NOT NULL,
        reporting_period TEXT,
        origin TEXT NOT NULL,
        extra TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        UNIQUE (organisation_id, record_key)
    );
    CREATE TABLE funding_need (
        id INTEGER PRIMARY KEY,
        organisation_id INTEGER NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        kind TEXT NOT NULL CHECK (kind IN ('programme', 'project', 'core')),
        description TEXT NOT NULL,
        amount_sought INTEGER,
        timeframe TEXT,
        geography TEXT,
        beneficiaries TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        UNIQUE (organisation_id, title)
    );
    """,
    """
    CREATE TABLE grant_record (
        id INTEGER PRIMARY KEY,
        origin TEXT NOT NULL CHECK (origin IN ('manual', 'url', 'monitored')),
        source_url TEXT,
        canonical_url TEXT UNIQUE,
        title TEXT NOT NULL,
        funder TEXT NOT NULL,
        purpose TEXT,
        eligibility TEXT,
        scope TEXT,
        funding TEXT,
        duration TEXT,
        deadline_text TEXT,
        deadline_date TEXT,
        key_requirements TEXT,
        exclusions TEXT,
        status TEXT NOT NULL DEFAULT 'unknown' CHECK (status IN ('open', 'closed', 'unknown')),
        verification_status TEXT NOT NULL DEFAULT 'unverified' CHECK (verification_status IN ('unverified', 'verified')),
        verified_by TEXT,
        verified_at TEXT,
        first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        dedupe_key TEXT NOT NULL UNIQUE
    );
    """,
    """
    CREATE TABLE match_result (
        id INTEGER PRIMARY KEY,
        organisation_id INTEGER NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
        funding_need_id INTEGER REFERENCES funding_need(id) ON DELETE CASCADE,
        grant_id INTEGER NOT NULL REFERENCES grant_record(id) ON DELETE CASCADE,
        fingerprint TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        model TEXT NOT NULL,
        eligibility_status TEXT NOT NULL CHECK (eligibility_status IN ('eligible', 'unclear', 'ineligible')),
        eligibility_items TEXT NOT NULL DEFAULT '[]',
        suitability TEXT NOT NULL DEFAULT '{}',
        suitability_total INTEGER NOT NULL,
        band TEXT NOT NULL CHECK (band IN ('Strong fit', 'Potential fit', 'Poor fit')),
        recommendation TEXT NOT NULL CHECK (recommendation IN ('Pursue', 'Investigate further', 'Reject')),
        exclusion_conflict TEXT NOT NULL DEFAULT '{}',
        summary TEXT NOT NULL DEFAULT '',
        uncertainties TEXT NOT NULL DEFAULT '[]',
        missing_information TEXT NOT NULL DEFAULT '[]',
        grant_verified INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE UNIQUE INDEX match_result_scope ON match_result (organisation_id, COALESCE(funding_need_id, 0), grant_id);
    """,
    """
    CREATE TABLE application (
        id INTEGER PRIMARY KEY,
        organisation_id INTEGER NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
        funding_need_id INTEGER REFERENCES funding_need(id) ON DELETE CASCADE,
        grant_id INTEGER NOT NULL REFERENCES grant_record(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK (status IN ('saved', 'pursuing', 'drafting', 'submitted', 'awarded', 'unsuccessful', 'withdrawn', 'rejected')),
        outcome_note TEXT,
        amount_awarded INTEGER,
        submitted_at TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE UNIQUE INDEX application_scope ON application (organisation_id, COALESCE(funding_need_id, 0), grant_id);
    CREATE TABLE application_event (
        id INTEGER PRIMARY KEY,
        application_id INTEGER NOT NULL REFERENCES application(id) ON DELETE CASCADE,
        from_status TEXT,
        to_status TEXT NOT NULL,
        note TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE TABLE application_requirement (
        id INTEGER PRIMARY KEY,
        application_id INTEGER NOT NULL REFERENCES application(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('grant', 'manual')),
        done INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE application_task (
        id INTEGER PRIMARY KEY,
        application_id INTEGER NOT NULL REFERENCES application(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        due_date TEXT,
        done INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    """,
    """
    CREATE TABLE draft_answer (
        id INTEGER PRIMARY KEY,
        application_id INTEGER NOT NULL REFERENCES application(id) ON DELETE CASCADE,
        question TEXT NOT NULL,
        word_limit INTEGER,
        guidance TEXT,
        ai_text TEXT NOT NULL,
        edited_text TEXT,
        citations TEXT NOT NULL DEFAULT '[]',
        missing_evidence TEXT NOT NULL DEFAULT '[]',
        review_flags TEXT NOT NULL DEFAULT '[]',
        reviewed_by_user INTEGER NOT NULL DEFAULT 0,
        model TEXT NOT NULL,
        prompt_version TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        modified_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    """,
    """
    CREATE TABLE notification_log (
        id INTEGER PRIMARY KEY,
        application_id INTEGER NOT NULL REFERENCES application(id) ON DELETE CASCADE,
        trigger_key TEXT NOT NULL,
        outbox_file TEXT,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        UNIQUE (application_id, trigger_key)
    );
    """,
    """
    CREATE TABLE grant_source (
        id INTEGER PRIMARY KEY,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        publisher TEXT NOT NULL,
        base_url TEXT NOT NULL,
        adapter TEXT NOT NULL,
        coverage TEXT NOT NULL DEFAULT '[]',
        licence TEXT,
        terms_url TEXT,
        terms_checked_on TEXT,
        min_interval_seconds REAL NOT NULL DEFAULT 5 CHECK (min_interval_seconds >= 1),
        max_pages_per_run INTEGER NOT NULL DEFAULT 40 CHECK (max_pages_per_run >= 1),
        enabled INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    );
    CREATE TABLE discovery_run (
        id INTEGER PRIMARY KEY,
        source_id INTEGER NOT NULL REFERENCES grant_source(id) ON DELETE CASCADE,
        started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
        finished_at TEXT,
        status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'partial', 'failed')),
        pages_fetched INTEGER NOT NULL DEFAULT 0,
        grants_new INTEGER NOT NULL DEFAULT 0,
        grants_seen INTEGER NOT NULL DEFAULT 0,
        problems TEXT NOT NULL DEFAULT '[]'
    );
    ALTER TABLE grant_record ADD COLUMN source_id INTEGER REFERENCES grant_source(id);
    """,
    """
    ALTER TABLE grant_record ADD COLUMN last_checked_at TEXT;
    ALTER TABLE grant_record ADD COLUMN last_changed_at TEXT;
    ALTER TABLE grant_record ADD COLUMN review_reason TEXT;
    ALTER TABLE grant_record ADD COLUMN review_flagged_at TEXT;
    ALTER TABLE grant_record ADD COLUMN verification_reset_at TEXT;
    ALTER TABLE grant_record ADD COLUMN verification_reset_reason TEXT;
    CREATE TABLE grant_snapshot (
        id INTEGER PRIMARY KEY,
        grant_id INTEGER NOT NULL REFERENCES grant_record(id) ON DELETE CASCADE,
        source_id INTEGER REFERENCES grant_source(id),
        taken_at TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        fields TEXT NOT NULL
    );
    CREATE INDEX grant_snapshot_grant ON grant_snapshot (grant_id, id);
    CREATE TABLE grant_event (
        id INTEGER PRIMARY KEY,
        grant_id INTEGER NOT NULL REFERENCES grant_record(id) ON DELETE CASCADE,
        run_id INTEGER REFERENCES discovery_run(id) ON DELETE SET NULL,
        kind TEXT NOT NULL CHECK (kind IN ('found', 'updated', 'reformatted', 'verification_reset', 'closed_by_deadline',
            'flagged_for_review', 'review_cleared', 'closed_by_person')),
        detail TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    );
    CREATE INDEX grant_event_grant ON grant_event (grant_id, id);
    """,
    """
    CREATE TABLE notification_contact (
        id INTEGER PRIMARY KEY,
        organisation_id INTEGER NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        consent_given_at TEXT,
        consent_wording TEXT,
        consent_recorded_by TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        unsubscribed_at TEXT,
        unsubscribe_token TEXT NOT NULL UNIQUE,
        alerts_frequency TEXT NOT NULL DEFAULT 'weekly' CHECK (alerts_frequency IN ('weekly', 'daily', 'off')),
        digest_weekday INTEGER NOT NULL DEFAULT 0 CHECK (digest_weekday BETWEEN 0 AND 6),
        min_band TEXT NOT NULL DEFAULT 'Potential fit' CHECK (min_band IN ('Strong fit', 'Potential fit')),
        include_unclear INTEGER NOT NULL DEFAULT 1,
        verified_only INTEGER NOT NULL DEFAULT 0,
        reminders_enabled INTEGER NOT NULL DEFAULT 1,
        alerts_since TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (organisation_id, email)
    );
    CREATE TABLE message_log (
        id INTEGER PRIMARY KEY,
        organisation_id INTEGER NOT NULL REFERENCES organisation(id) ON DELETE CASCADE,
        contact_id INTEGER NOT NULL REFERENCES notification_contact(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK (kind IN ('digest', 'grant_alert', 'deadline_reminder')),
        trigger_key TEXT NOT NULL,
        outbox_file TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (contact_id, kind, trigger_key)
    );
    """,
)


def connect(path):
    """Open (creating if needed) and migrate the database at path; ':memory:' for tests."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    return connection


def migrate(connection):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        connection.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")
    return len(MIGRATIONS)
