"""An existing database (from before Phase 3) upgrades in place without losing anything."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import sqlite3
import unittest

from grant_platform import db


class UpgradeTests(unittest.TestCase):
    def test_phase2_database_upgrades_and_keeps_its_grants(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        phase2 = 6  # migrations 1..6 are everything Phase 2 shipped
        for number, script in enumerate(db.MIGRATIONS[:phase2], start=1):
            connection.executescript(f"BEGIN; {script} PRAGMA user_version = {number}; COMMIT;")
        connection.execute("INSERT INTO grant_record (origin, title, funder, dedupe_key, verification_status, verified_by) "
                           "VALUES ('manual', 'Old Fund', 'A Trust', 'a trust|old fund', 'verified', 'Someone')")
        connection.commit()
        db.migrate(connection)
        row = connection.execute("SELECT * FROM grant_record").fetchone()
        self.assertEqual((row["title"], row["verification_status"], row["verified_by"]), ("Old Fund", "verified", "Someone"))
        for column in ("source_id", "last_checked_at", "last_changed_at", "review_reason", "verification_reset_reason"):
            self.assertIsNone(row[column], column)
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({"grant_source", "discovery_run", "grant_snapshot", "grant_event"} <= tables)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], len(db.MIGRATIONS))

    def test_migrating_twice_changes_nothing(self):
        connection = db.connect(":memory:")
        before = connection.execute("PRAGMA user_version").fetchone()[0]
        db.migrate(connection)
        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], before)


if __name__ == "__main__":
    unittest.main()
