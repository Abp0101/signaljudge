"""SQLite state store with idempotent runs and a tamper-evident audit chain."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from signaljudge.models import Decision


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    odds_fetched_at TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    raw_snapshot_json TEXT NOT NULL,
    context_key TEXT NOT NULL DEFAULT 'legacy',
    previous_run_hash TEXT NOT NULL DEFAULT 'GENESIS',
    run_audit_hash TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    event_id TEXT NOT NULL,
    final_rank INTEGER,
    winner TEXT NOT NULL CHECK (winner IN ('MODEL', 'MARKET', 'ABSTAIN')),
    reconciled_probability REAL,
    payload_json TEXT NOT NULL,
    previous_decision_id INTEGER REFERENCES decisions(decision_id),
    previous_audit_hash TEXT NOT NULL,
    audit_hash TEXT NOT NULL UNIQUE,
    UNIQUE(run_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_decisions_event ON decisions(event_id, decision_id);
CREATE INDEX IF NOT EXISTS idx_decisions_run_rank ON decisions(run_id, final_rank);
CREATE TABLE IF NOT EXISTS source_metrics (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    source TEXT NOT NULL,
    brier REAL NOT NULL,
    log_loss REAL NOT NULL,
    accuracy REAL NOT NULL,
    sample_size INTEGER NOT NULL,
    PRIMARY KEY(run_id, source)
);
"""


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=10.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self.connection.execute("PRAGMA busy_timeout=10000")
        self._migrate_schema()
        self._backfill_run_audit_hashes()
        self._restrict_permissions()

    def close(self) -> None:
        self.connection.close()
        self._restrict_permissions()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def latest_snapshot(self, context_key: Optional[str] = None) -> Optional[Mapping[str, Any]]:
        if context_key is None:
            row = self.connection.execute(
                "SELECT raw_snapshot_json FROM runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT raw_snapshot_json FROM runs WHERE context_key = ? ORDER BY rowid DESC LIMIT 1",
                (context_key,),
            ).fetchone()
        return json.loads(row["raw_snapshot_json"]) if row else None

    def latest_decisions(self, context_key: Optional[str] = None) -> Dict[str, Decision]:
        if context_key is None:
            rows = self.connection.execute(
                """
                SELECT d.* FROM decisions d
                JOIN (SELECT event_id, MAX(decision_id) AS id FROM decisions GROUP BY event_id) latest
                  ON latest.id = d.decision_id
                """
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT d.* FROM decisions d
                JOIN runs r ON r.run_id = d.run_id
                JOIN (
                    SELECT d2.event_id, MAX(d2.decision_id) AS id
                    FROM decisions d2 JOIN runs r2 ON r2.run_id = d2.run_id
                    WHERE r2.context_key = ? GROUP BY d2.event_id
                ) latest ON latest.id = d.decision_id
                WHERE r.context_key = ?
                """,
                (context_key, context_key),
            ).fetchall()
        return {row["event_id"]: self._decision_from_row(row) for row in rows}

    def find_run(self, content_hash: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT run_id FROM runs WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return row["run_id"] if row else None

    def load_run(self, run_id: str) -> List[Decision]:
        rows = self.connection.execute(
            """SELECT * FROM decisions WHERE run_id = ?
               ORDER BY final_rank IS NULL, final_rank, decision_id""",
            (run_id,),
        ).fetchall()
        return [self._decision_from_row(row) for row in rows]

    def load_run_warnings(self, run_id: str) -> List[str]:
        row = self.connection.execute(
            "SELECT warnings_json FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return json.loads(row["warnings_json"]) if row else []

    def persist_run(
        self,
        run_id: str,
        created_at: str,
        mode: str,
        context_key: str,
        odds_fetched_at: str,
        content_hash: str,
        snapshot: Mapping[str, Any],
        decisions: List[Decision],
        warnings: List[str],
    ) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            latest = self.latest_decisions(context_key)
            last_row = self.connection.execute(
                "SELECT audit_hash FROM decisions ORDER BY decision_id DESC LIMIT 1"
            ).fetchone()
            chain_hash = last_row["audit_hash"] if last_row else "GENESIS"
            last_run = self.connection.execute(
                "SELECT run_audit_hash FROM runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            previous_run_hash = last_run["run_audit_hash"] if last_run else "GENESIS"
            self.connection.execute(
                """
                INSERT INTO runs (
                    run_id, created_at, mode, odds_fetched_at, content_hash, status,
                    warnings_json, raw_snapshot_json, context_key, previous_run_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    created_at,
                    mode,
                    odds_fetched_at,
                    content_hash,
                    "DEGRADED"
                    if warnings
                    or snapshot.get("degraded")
                    or any(item.status != "RECONCILED" for item in decisions)
                    else "COMPLETE",
                    json.dumps(warnings, sort_keys=True),
                    json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
                    context_key,
                    previous_run_hash,
                ),
            )
            for decision in decisions:
                previous = latest.get(decision.event_id)
                decision.previous_decision_id = previous.decision_id if previous else None
                payload = decision.as_dict()
                payload.pop("decision_id", None)
                payload.pop("audit_hash", None)
                canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
                audit_hash = hashlib.sha256((chain_hash + canonical).encode("utf-8")).hexdigest()
                cursor = self.connection.execute(
                    """
                    INSERT INTO decisions (
                        run_id, event_id, final_rank, winner, reconciled_probability,
                        payload_json, previous_decision_id, previous_audit_hash, audit_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        decision.event_id,
                        decision.final_rank,
                        decision.winner,
                        decision.reconciled_probability,
                        canonical,
                        decision.previous_decision_id,
                        chain_hash,
                        audit_hash,
                    ),
                )
                decision.decision_id = cursor.lastrowid
                decision.audit_hash = audit_hash
                chain_hash = audit_hash
            row = self.connection.execute("SELECT rowid, * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            run_hash = self._calculate_run_hash(row, previous_run_hash)
            self.connection.execute(
                "UPDATE runs SET run_audit_hash = ? WHERE run_id = ?", (run_hash, run_id)
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def save_metrics(self, run_id: str, metrics: Mapping[str, Mapping[str, float]]) -> None:
        with self.connection:
            for source, values in metrics.items():
                self.connection.execute(
                    """
                    INSERT OR REPLACE INTO source_metrics
                    (run_id, source, brier, log_loss, accuracy, sample_size)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        source,
                        values["brier"],
                        values["log_loss"],
                        values["accuracy"],
                        int(values["sample_size"]),
                    ),
                )

    def verify_audit_chain(self) -> Tuple[bool, int]:
        rows = self.connection.execute("SELECT * FROM decisions ORDER BY decision_id").fetchall()
        expected_previous = "GENESIS"
        for row in rows:
            if row["previous_audit_hash"] != expected_previous:
                return False, row["decision_id"]
            try:
                payload = json.loads(row["payload_json"])
            except json.JSONDecodeError:
                return False, row["decision_id"]
            if (
                payload.get("event_id") != row["event_id"]
                or payload.get("final_rank") != row["final_rank"]
                or payload.get("winner") != row["winner"]
                or payload.get("reconciled_probability") != row["reconciled_probability"]
                or payload.get("previous_decision_id") != row["previous_decision_id"]
            ):
                return False, row["decision_id"]
            expected = hashlib.sha256(
                (expected_previous + row["payload_json"]).encode("utf-8")
            ).hexdigest()
            if expected != row["audit_hash"]:
                return False, row["decision_id"]
            expected_previous = row["audit_hash"]
        expected_run_previous = "GENESIS"
        runs = self.connection.execute("SELECT rowid, * FROM runs ORDER BY rowid").fetchall()
        for run in runs:
            if run["previous_run_hash"] != expected_run_previous:
                return False, -int(run["rowid"])
            expected = self._calculate_run_hash(run, expected_run_previous)
            if expected != run["run_audit_hash"]:
                return False, -int(run["rowid"])
            expected_run_previous = run["run_audit_hash"]
        return True, len(rows)

    def run_history(self) -> List[Dict[str, Any]]:
        rows = self.connection.execute(
            """SELECT run_id, created_at, mode, context_key, odds_fetched_at, status,
                      warnings_json, previous_run_hash, run_audit_hash
               FROM runs ORDER BY rowid"""
        ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "created_at": row["created_at"],
                "mode": row["mode"],
                "context_key": row["context_key"],
                "odds_fetched_at": row["odds_fetched_at"],
                "status": row["status"],
                "warnings": json.loads(row["warnings_json"]),
                "previous_run_hash": row["previous_run_hash"],
                "run_audit_hash": row["run_audit_hash"],
            }
            for row in rows
        ]

    @staticmethod
    def _decision_from_row(row: sqlite3.Row) -> Decision:
        payload = json.loads(row["payload_json"])
        allowed = set(Decision.__dataclass_fields__)
        decision = Decision(**{key: value for key, value in payload.items() if key in allowed})
        decision.decision_id = row["decision_id"]
        decision.previous_decision_id = row["previous_decision_id"]
        decision.audit_hash = row["audit_hash"]
        return decision

    def _migrate_schema(self) -> None:
        run_columns = {
            row["name"] for row in self.connection.execute("PRAGMA table_info(runs)").fetchall()
        }
        additions = {
            "context_key": "TEXT NOT NULL DEFAULT 'legacy'",
            "previous_run_hash": "TEXT NOT NULL DEFAULT 'GENESIS'",
            "run_audit_hash": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in run_columns:
                self.connection.execute(f"ALTER TABLE runs ADD COLUMN {name} {declaration}")
        self.connection.commit()

        decisions_sql_row = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'decisions'"
        ).fetchone()
        decisions_sql = decisions_sql_row["sql"] if decisions_sql_row else ""
        if "ABSTAIN" not in decisions_sql:
            self.connection.execute("PRAGMA foreign_keys=OFF")
            try:
                self.connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    ALTER TABLE decisions RENAME TO decisions_v1;
                    CREATE TABLE decisions (
                        decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL REFERENCES runs(run_id),
                        event_id TEXT NOT NULL,
                        final_rank INTEGER,
                        winner TEXT NOT NULL CHECK (winner IN ('MODEL', 'MARKET', 'ABSTAIN')),
                        reconciled_probability REAL,
                        payload_json TEXT NOT NULL,
                        previous_decision_id INTEGER REFERENCES decisions(decision_id),
                        previous_audit_hash TEXT NOT NULL,
                        audit_hash TEXT NOT NULL UNIQUE,
                        UNIQUE(run_id, event_id)
                    );
                    INSERT INTO decisions SELECT * FROM decisions_v1;
                    DROP TABLE decisions_v1;
                    CREATE INDEX idx_decisions_event ON decisions(event_id, decision_id);
                    CREATE INDEX idx_decisions_run_rank ON decisions(run_id, final_rank);
                    COMMIT;
                    """
                )
            except Exception:
                self.connection.rollback()
                raise
            finally:
                self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_audit_hash ON runs(run_audit_hash)"
        )
        self.connection.commit()

    def _run_envelope(self, row: sqlite3.Row) -> Dict[str, Any]:
        decision_hashes = [
            item["audit_hash"]
            for item in self.connection.execute(
                "SELECT audit_hash FROM decisions WHERE run_id = ? ORDER BY decision_id",
                (row["run_id"],),
            ).fetchall()
        ]
        return {
            "run_id": row["run_id"],
            "created_at": row["created_at"],
            "mode": row["mode"],
            "context_key": row["context_key"],
            "odds_fetched_at": row["odds_fetched_at"],
            "content_hash": row["content_hash"],
            "status": row["status"],
            "warnings_json": row["warnings_json"],
            "raw_snapshot_json": row["raw_snapshot_json"],
            "decision_hashes": decision_hashes,
        }

    def _calculate_run_hash(self, row: sqlite3.Row, previous_hash: str) -> str:
        canonical = json.dumps(self._run_envelope(row), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256((previous_hash + canonical).encode("utf-8")).hexdigest()

    def _backfill_run_audit_hashes(self) -> None:
        previous_hash = "GENESIS"
        runs = self.connection.execute("SELECT rowid, * FROM runs ORDER BY rowid").fetchall()
        with self.connection:
            for row in runs:
                if row["run_audit_hash"] is not None:
                    previous_hash = row["run_audit_hash"]
                    continue
                run_hash = self._calculate_run_hash(row, previous_hash)
                self.connection.execute(
                    "UPDATE runs SET previous_run_hash = ?, run_audit_hash = ? WHERE run_id = ?",
                    (previous_hash, run_hash, row["run_id"]),
                )
                previous_hash = run_hash

    def _restrict_permissions(self) -> None:
        for candidate in (self.path, Path(f"{self.path}-wal"), Path(f"{self.path}-shm")):
            if candidate.exists():
                os.chmod(candidate, 0o600)
