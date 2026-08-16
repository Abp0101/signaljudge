"""SQLite state store with idempotent runs and a tamper-evident audit chain."""

from __future__ import annotations

import hashlib
import json
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
    raw_snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    decision_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    event_id TEXT NOT NULL,
    final_rank INTEGER NOT NULL,
    winner TEXT NOT NULL CHECK (winner IN ('MODEL', 'MARKET')),
    reconciled_probability REAL NOT NULL,
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
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def latest_snapshot(self) -> Optional[Mapping[str, Any]]:
        row = self.connection.execute(
            "SELECT raw_snapshot_json FROM runs ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return json.loads(row["raw_snapshot_json"]) if row else None

    def latest_decisions(self) -> Dict[str, Decision]:
        rows = self.connection.execute(
            """
            SELECT d.* FROM decisions d
            JOIN (SELECT event_id, MAX(decision_id) AS id FROM decisions GROUP BY event_id) latest
              ON latest.id = d.decision_id
            """
        ).fetchall()
        return {row["event_id"]: self._decision_from_row(row) for row in rows}

    def find_run(self, content_hash: str) -> Optional[str]:
        row = self.connection.execute(
            "SELECT run_id FROM runs WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return row["run_id"] if row else None

    def load_run(self, run_id: str) -> List[Decision]:
        rows = self.connection.execute(
            "SELECT * FROM decisions WHERE run_id = ? ORDER BY final_rank", (run_id,)
        ).fetchall()
        return [self._decision_from_row(row) for row in rows]

    def persist_run(
        self,
        run_id: str,
        created_at: str,
        mode: str,
        odds_fetched_at: str,
        content_hash: str,
        snapshot: Mapping[str, Any],
        decisions: List[Decision],
        warnings: List[str],
    ) -> None:
        latest = self.latest_decisions()
        last_row = self.connection.execute(
            "SELECT audit_hash FROM decisions ORDER BY decision_id DESC LIMIT 1"
        ).fetchone()
        chain_hash = last_row["audit_hash"] if last_row else "GENESIS"
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO runs (
                    run_id, created_at, mode, odds_fetched_at, content_hash, status,
                    warnings_json, raw_snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    created_at,
                    mode,
                    odds_fetched_at,
                    content_hash,
                    "DEGRADED" if warnings or snapshot.get("degraded") else "COMPLETE",
                    json.dumps(warnings, sort_keys=True),
                    json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
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
            expected = hashlib.sha256(
                (expected_previous + row["payload_json"]).encode("utf-8")
            ).hexdigest()
            if expected != row["audit_hash"]:
                return False, row["decision_id"]
            expected_previous = row["audit_hash"]
        return True, len(rows)

    def run_history(self) -> List[Dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT run_id, created_at, mode, odds_fetched_at, status, warnings_json FROM runs ORDER BY created_at"
        ).fetchall()
        return [
            {
                "run_id": row["run_id"],
                "created_at": row["created_at"],
                "mode": row["mode"],
                "odds_fetched_at": row["odds_fetched_at"],
                "status": row["status"],
                "warnings": json.loads(row["warnings_json"]),
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

