import sqlite3
import tempfile
import unittest
from pathlib import Path

from signaljudge.state import StateStore


LEGACY_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, mode TEXT NOT NULL,
    odds_fetched_at TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL, warnings_json TEXT NOT NULL, raw_snapshot_json TEXT NOT NULL
);
CREATE TABLE decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id), event_id TEXT NOT NULL,
    final_rank INTEGER NOT NULL,
    winner TEXT NOT NULL CHECK (winner IN ('MODEL', 'MARKET')),
    reconciled_probability REAL NOT NULL, payload_json TEXT NOT NULL,
    previous_decision_id INTEGER REFERENCES decisions(decision_id),
    previous_audit_hash TEXT NOT NULL, audit_hash TEXT NOT NULL UNIQUE,
    UNIQUE(run_id, event_id)
);
CREATE INDEX idx_decisions_event ON decisions(event_id, decision_id);
CREATE INDEX idx_decisions_run_rank ON decisions(run_id, final_rank);
CREATE TABLE source_metrics (
    run_id TEXT NOT NULL REFERENCES runs(run_id), source TEXT NOT NULL,
    brier REAL NOT NULL, log_loss REAL NOT NULL, accuracy REAL NOT NULL,
    sample_size INTEGER NOT NULL, PRIMARY KEY(run_id, source)
);
"""


class StateMigrationTests(unittest.TestCase):
    def test_legacy_schema_is_migrated_and_audited(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = sqlite3.connect(str(path))
            connection.executescript(LEGACY_SCHEMA)
            connection.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "legacy-run",
                    "2026-08-16T00:00:00Z",
                    "fixture",
                    "2026-08-16T00:00:00Z",
                    "legacy-content",
                    "COMPLETE",
                    "[]",
                    '{"success":true,"fetched_at":"2026-08-16T00:00:00Z","data":[]}',
                ),
            )
            connection.commit()
            connection.close()

            with StateStore(path) as store:
                columns = {
                    row["name"]
                    for row in store.connection.execute("PRAGMA table_info(runs)").fetchall()
                }
                decisions_sql = store.connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='decisions'"
                ).fetchone()["sql"]
                valid, count = store.verify_audit_chain()

            self.assertIn("run_audit_hash", columns)
            self.assertIn("context_key", columns)
            self.assertIn("ABSTAIN", decisions_sql)
            self.assertTrue(valid)
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
