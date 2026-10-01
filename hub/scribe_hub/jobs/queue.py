"""A SQLite job table with two lanes.

- `hub` jobs (S1 ingest, S3 stage) run in-process, one at a time, by `run_hub_worker`.
- `mac` jobs (S2 transcription) are claimed over the API by the Mac worker, under a lease. A lease
  that runs out (the Mac slept, the worker crashed) puts the job back in the queue.

On startup `recover()` re-queues anything left `running`, so an unannounced reboot resumes work.
Every stage is idempotent, so running one twice is safe.
"""
from __future__ import annotations

import json

from ..db import Database, now

MAX_ATTEMPTS = 3


def enqueue(db: Database, session: int, stage: str, lane: str, payload: dict | None = None) -> int:
    t = now()
    cur = db.execute(
        "INSERT INTO jobs (session, stage, lane, state, payload, created_at, updated_at) "
        "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
        (session, stage, lane, json.dumps(payload or {}), t, t))
    return cur.lastrowid


def get(db: Database, job_id: int) -> dict | None:
    row = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return _row(row) if row else None


def _row(row) -> dict:
    d = dict(row)
    d["payload"] = json.loads(d["payload"] or "{}")
    return d


def claim(db: Database, lane: str, lease_s: float | None = None) -> dict | None:
    """Take the oldest queued job in a lane (or one whose lease ran out)."""
    with db.transaction():
        t = now()
        row = db.one(
            "SELECT * FROM jobs WHERE lane = ? AND (state = 'queued' OR "
            "(state = 'running' AND lease_until IS NOT NULL AND lease_until < ?)) "
            "ORDER BY id LIMIT 1", (lane, t))
        if row is None:
            return None
        db.execute("UPDATE jobs SET state = 'running', attempts = attempts + 1, lease_until = ?, "
                   "updated_at = ? WHERE id = ?",
                   (t + lease_s if lease_s else None, t, row["id"]))
    return get(db, row["id"])


def extend(db: Database, job_id: int, lease_s: float) -> bool:
    cur = db.execute("UPDATE jobs SET lease_until = ?, updated_at = ? "
                     "WHERE id = ? AND state = 'running'", (now() + lease_s, now(), job_id))
    return bool(cur.rowcount)


def finish(db: Database, job_id: int, payload_update: dict | None = None) -> None:
    job = get(db, job_id)
    payload = {**(job["payload"] if job else {}), **(payload_update or {})}
    db.execute("UPDATE jobs SET state = 'done', error = NULL, lease_until = NULL, payload = ?, "
               "updated_at = ? WHERE id = ?", (json.dumps(payload), now(), job_id))


def fail(db: Database, job_id: int, error: str, retry: bool = True) -> str:
    """Re-queue until MAX_ATTEMPTS, then mark failed. Returns the new state."""
    job = get(db, job_id)
    state = "queued" if retry and job and job["attempts"] < MAX_ATTEMPTS else "failed"
    db.execute("UPDATE jobs SET state = ?, error = ?, lease_until = NULL, updated_at = ? "
               "WHERE id = ?", (state, error[:1000], now(), job_id))
    return state


def block(db: Database, job_id: int, reason: str) -> None:
    """Stopped on purpose until a person acts (e.g. an unmapped speaker)."""
    db.execute("UPDATE jobs SET state = 'blocked', error = ?, lease_until = NULL, updated_at = ? "
               "WHERE id = ?", (reason[:1000], now(), job_id))


def cancel_open(db: Database, session: int, stage: str) -> int:
    cur = db.execute("UPDATE jobs SET state = 'cancelled', lease_until = NULL, updated_at = ? "
                     "WHERE session = ? AND stage = ? AND state IN ('queued','running','blocked')",
                     (now(), session, stage))
    return cur.rowcount


def recover(db: Database) -> int:
    """After a restart: in-process jobs that were running are re-queued. Leased Mac jobs keep
    their lease and are reclaimed by `claim` when it runs out."""
    cur = db.execute("UPDATE jobs SET state = 'queued', updated_at = ? "
                     "WHERE state = 'running' AND lane = 'hub'", (now(),))
    return cur.rowcount


def for_session(db: Database, session: int) -> list[dict]:
    return [_row(r) for r in db.all("SELECT * FROM jobs WHERE session = ? ORDER BY id", (session,))]


def counts(db: Database) -> dict:
    out: dict[str, dict[str, int]] = {}
    for r in db.all("SELECT lane, state, COUNT(*) AS n FROM jobs GROUP BY lane, state"):
        out.setdefault(r["lane"], {})[r["state"]] = r["n"]
    return out
