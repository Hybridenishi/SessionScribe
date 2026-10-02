"""SQLite store: devices and tokens, pairing codes, sessions and jobs.

The vault's git history stays the source of truth for canon; this file only indexes hub state.
Migrations are plain SQL, applied in order and recorded in `schema_version`.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

MIGRATIONS = [
    # 1 — devices, pairing, sessions, jobs
    """
    CREATE TABLE devices (
        id          TEXT PRIMARY KEY,        -- first 8 hex chars of the token hash
        name        TEXT NOT NULL,
        scope       TEXT NOT NULL,           -- app | worker | gpu
        token_hash  TEXT NOT NULL UNIQUE,
        created_at  REAL NOT NULL,
        last_seen   REAL,
        revoked_at  REAL
    );
    CREATE TABLE pair_codes (
        code_hash   TEXT PRIMARY KEY,
        device_name TEXT NOT NULL,
        expires_at  REAL NOT NULL,
        used_at     REAL
    );
    CREATE TABLE pair_failures (at REAL NOT NULL);
    CREATE TABLE sessions (
        number      INTEGER PRIMARY KEY,
        created_at  REAL NOT NULL,
        -- ingesting | blocked | transcribing | staging | ready | failed
        state       TEXT NOT NULL,
        detail      TEXT
    );
    CREATE TABLE jobs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session     INTEGER NOT NULL REFERENCES sessions(number),
        stage       TEXT NOT NULL,           -- s1 | s2 | s3
        lane        TEXT NOT NULL,           -- hub | mac
        -- queued | running | done | failed | blocked | cancelled
        state       TEXT NOT NULL,
        payload     TEXT NOT NULL DEFAULT '{}',
        attempts    INTEGER NOT NULL DEFAULT 0,
        error       TEXT,
        lease_until REAL,
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    );
    CREATE INDEX jobs_lane_state ON jobs(lane, state);
    CREATE INDEX jobs_session ON jobs(session);
    CREATE TABLE worker_seen (name TEXT PRIMARY KEY, at REAL NOT NULL);
    """,
    # 2 — H2: proposal batches (one S4 run) and the proposals the DM reviews
    """
    CREATE TABLE proposal_batches (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        session     INTEGER NOT NULL REFERENCES sessions(number),
        vault       TEXT NOT NULL,           -- dm | players
        provider    TEXT NOT NULL,           -- codex | claude | custom
        created_at  REAL NOT NULL,
        violations  TEXT NOT NULL DEFAULT '[]',   -- files the agent touched that it must not
        summary     TEXT,
        published_at REAL,
        publish_commit TEXT
    );
    CREATE TABLE proposals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        batch       INTEGER NOT NULL REFERENCES proposal_batches(id),
        proposal_id TEXT NOT NULL,           -- the agent's id, e.g. p-060-001
        op          TEXT NOT NULL,
        target      TEXT NOT NULL,
        section     TEXT,
        key         TEXT,
        from_path   TEXT,
        after       TEXT,
        edited_after TEXT,                   -- the DM's edit, if any, which wins over after
        before      TEXT,                    -- read by the hub from the file, never the model
        entity      TEXT NOT NULL,
        secret      INTEGER NOT NULL DEFAULT 0,
        rationale   TEXT NOT NULL DEFAULT '',
        evidence    TEXT NOT NULL DEFAULT '[]',
        conflicts   TEXT NOT NULL DEFAULT '[]',
        -- pending | accepted | rejected | deferred | rejected_by_checks | applied
        state       TEXT NOT NULL,
        check_error TEXT,
        updated_at  REAL NOT NULL,
        UNIQUE (batch, proposal_id)
    );
    CREATE INDEX proposals_batch ON proposals(batch, state);
    """,
]


class Database:
    """A small thread-safe wrapper. One connection, serialised by a lock — the hub's write rate is
    tiny, and this keeps every transaction simple."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (v INTEGER NOT NULL)")
            row = self._conn.execute("SELECT MAX(v) FROM schema_version").fetchone()
            current = row[0] or 0
            for v, sql in enumerate(MIGRATIONS, start=1):
                if v <= current:
                    continue
                self._conn.execute("BEGIN")
                try:
                    for stmt in filter(None, (s.strip() for s in sql.split(";"))):
                        self._conn.execute(stmt)
                    self._conn.execute("INSERT INTO schema_version (v) VALUES (?)", (v,))
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def transaction(self):
        return _Tx(self)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class _Tx:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db._lock.acquire()
        self.db._conn.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db._conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db._lock.release()
        return False


def now() -> float:
    return time.time()
