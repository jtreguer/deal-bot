"""SQLite storage: runs, listing history and the extraction cache."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from deal_bot.models import ExtractedSpec, Scored

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, target TEXT NOT NULL, started_at TEXT NOT NULL, sources TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS listings (
    key TEXT PRIMARY KEY, source TEXT NOT NULL, url TEXT NOT NULL,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, raw TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
    run_id INTEGER NOT NULL REFERENCES runs(id), key TEXT NOT NULL REFERENCES listings(key),
    landed_eur REAL, fair_eur REAL, verdict TEXT NOT NULL, risk INTEGER NOT NULL,
    PRIMARY KEY (run_id, key)
);
CREATE TABLE IF NOT EXISTS extractions (
    hash TEXT PRIMARY KEY, model TEXT NOT NULL, spec TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    """One connection shared by extraction worker threads, so every access takes the lock."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def get_extraction(self, h: str) -> ExtractedSpec | None:
        with self._lock:
            row = self.db.execute("SELECT spec FROM extractions WHERE hash = ?", (h,)).fetchone()
        return ExtractedSpec.model_validate_json(row[0]) if row else None

    def put_extraction(self, h: str, model: str, spec: ExtractedSpec) -> None:
        with self._lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO extractions VALUES (?, ?, ?, ?)",
                (h, model, spec.model_dump_json(), _now()),
            )

    def record_run(self, target: str, source_status: dict[str, str], items: list[Scored]) -> int:
        now = _now()
        with self._lock, self.db:
            run_id = self.db.execute(
                "INSERT INTO runs (target, started_at, sources) VALUES (?, ?, ?)",
                (target, now, json.dumps(source_status)),
            ).lastrowid
            for s in items:
                self.db.execute(
                    """INSERT INTO listings VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET last_seen = excluded.last_seen, raw = excluded.raw""",
                    (s.raw.key, s.raw.source, s.raw.url, now, now, s.raw.model_dump_json()),
                )
                self.db.execute(
                    "INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        s.raw.key,
                        float(s.landed.total_eur) if s.landed else None,
                        float(s.fair.value_eur) if s.fair else None,
                        s.verdict.value,
                        s.risk,
                    ),
                )
        return run_id
