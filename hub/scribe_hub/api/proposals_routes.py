"""Review and Publish (spec §5, H2): proposal cards, the DM's decisions, and Publish to canon."""
from __future__ import annotations

import json
import threading
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import archive, auth, vaults
from ..db import now
from ..jobs import queue
from ..proposals import publish as pub
from ..proposals.validate import check_one

router = APIRouter(tags=["review"])
REVIEWABLE = ("pending", "accepted", "rejected", "deferred")
# one Publish at a time, from reading the accepted cards to recording the result, so a second
# press can't re-apply them or mark published cards as conflicts
_PUBLISH = threading.Lock()


def _row(r) -> dict:
    d = dict(r)
    d["secret"] = bool(d["secret"])
    d["evidence"] = json.loads(d["evidence"] or "[]")
    d["conflicts"] = json.loads(d["conflicts"] or "[]")
    return d


def _latest_batch(db, n: int):
    return db.one("SELECT * FROM proposal_batches WHERE session = ? ORDER BY id DESC LIMIT 1", (n,))


@router.get("/sessions/{n}/proposals")
def list_proposals(n: int, request: Request, _=auth.require_app):
    db = request.app.state.db
    batch = _latest_batch(db, n)
    if batch is None:
        return {"batch": None, "proposals": []}
    rows = db.all("SELECT * FROM proposals WHERE batch = ? AND state != 'superseded' ORDER BY id",
                  (batch["id"],))
    b = dict(batch)
    b["violations"] = json.loads(b["violations"] or "[]")
    return {"batch": b, "proposals": [_row(r) for r in rows]}


class Decision(BaseModel):
    action: Literal["accept", "reject", "defer", "pending"]
    after: str | None = None        # an edit; implies accept


STATE_FOR = {"accept": "accepted", "reject": "rejected", "defer": "deferred", "pending": "pending"}


@router.patch("/proposals/{pid}")
def decide(pid: int, body: Decision, request: Request, _=auth.require_app):
    hub = request.app.state.hub
    row = hub.db.one("SELECT p.*, b.session, b.published_at FROM proposals p "
                     "JOIN proposal_batches b ON b.id = p.batch WHERE p.id = ?", (pid,))
    if row is None:
        raise HTTPException(404, "no such proposal")
    if row["state"] not in REVIEWABLE or row["published_at"] is not None:
        raise HTTPException(409, f"proposal is {row['state']} and can't be changed")
    edited = None
    if body.after is not None:
        if body.action != "accept":
            raise HTTPException(422, "an edit is an accept: send action 'accept' with after")
        # the edited text must pass the same checks the agent's text did
        utts = archive.read_ndjson(
            archive.session_dir(hub.settings.archive_dir, row["session"]) / "utterances.ndjson")
        raw = {"proposal_id": row["proposal_id"], "op": row["op"], "target": row["target"],
               "section": row["section"], "key": row["key"], "from": row["from_path"],
               "after": body.after, "entity": row["entity"], "secret": bool(row["secret"]),
               "rationale": row["rationale"],
               "evidence": [{"utterance_id": e["utterance_id"]}
                            for e in json.loads(row["evidence"] or "[]")],
               "conflicts_with": json.loads(row["conflicts"] or "[]")}
        checked = check_one(raw, hub.settings.dm_vault, {u["id"]: u for u in utts})
        if not checked.ok:
            raise HTTPException(422, f"edit rejected: {checked.error}")
        edited = body.after
    hub.db.execute("UPDATE proposals SET state = ?, edited_after = COALESCE(?, edited_after), "
                   "check_error = NULL, updated_at = ? WHERE id = ?",
                   (STATE_FOR[body.action], edited, now(), pid))
    return _row(hub.db.one("SELECT * FROM proposals WHERE id = ?", (pid,)))


@router.post("/sessions/{n}/propose", status_code=202)
def propose(n: int, request: Request, _=auth.require_app):
    """Run (or re-run) S4. A re-run supersedes the unpublished cards of the previous run."""
    hub = request.app.state.hub
    s = hub.db.one("SELECT state FROM sessions WHERE number = ?", (n,))
    if s is None:
        raise HTTPException(404, f"no session {n}")
    stage3 = (archive.read_manifest(hub.settings.archive_dir, n) or {}).get("stage3") or {}
    if not stage3.get("branch"):
        raise HTTPException(409, "the transcript isn't staged on a vault branch yet")
    if any(j["stage"] == "s4" and j["state"] in ("queued", "running")
           for j in queue.for_session(hub.db, n)):
        raise HTTPException(409, "already proposing")
    queue.cancel_open(hub.db, n, "s4")
    job = queue.enqueue(hub.db, n, "s4", "hub")
    hub.db.execute("UPDATE sessions SET state = 'proposing', detail = NULL WHERE number = ?", (n,))
    hub.notify()
    return {"session": n, "job": job}


class PublishRequest(BaseModel):
    dry_run: bool = False


@router.post("/sessions/{n}/publish")
def publish(n: int, body: PublishRequest, request: Request, _=auth.require_app):
    """Apply every accepted card to the vault's main. Pending/deferred cards are left for later."""
    with _PUBLISH:
        return _publish(request.app.state.hub, n, body.dry_run)


def _publish(hub, n: int, dry_run: bool) -> dict:
    s = hub.settings
    batch = _latest_batch(hub.db, n)
    if batch is None or batch["published_at"] is not None:
        raise HTTPException(409, "nothing unpublished for this session")
    accepted = [dict(r) for r in hub.db.all(
        "SELECT * FROM proposals WHERE batch = ? AND state = 'accepted' ORDER BY id",
        (batch["id"],))]
    if not accepted:
        raise HTTPException(409, "accept at least one card first")
    try:
        result = pub.publish(s.dm_vault, n, accepted, s.git_author,
                             push=s.push_branches, dry_run=dry_run)
    except pub.PublishConflict as exc:
        for c in exc.conflicts:
            hub.db.execute("UPDATE proposals SET state = 'pending', check_error = ?, "
                           "updated_at = ? WHERE id = ?", (c["reason"], now(), c["id"]))
        raise HTTPException(409, {"message": str(exc), "conflicts": exc.conflicts}) from None
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except vaults.VaultError as exc:
        # e.g. the push was refused because main moved; the clone is already back on main and
        # the cards stay accepted, so Publish can simply be pressed again
        raise HTTPException(409, f"Publish stopped: {exc}") from None
    if not result.dry_run:
        t = now()
        with hub.db.transaction():
            hub.db.execute("UPDATE proposals SET state = 'applied', updated_at = ? "
                           "WHERE batch = ? AND state = 'accepted'", (t, batch["id"]))
            hub.db.execute("UPDATE proposal_batches SET published_at = ?, publish_commit = ? "
                           "WHERE id = ?", (t, result.commit, batch["id"]))
            hub.db.execute("UPDATE sessions SET state = 'published', detail = ? WHERE number = ?",
                           (f"{len(accepted)} change(s) published", n))
    return {"dry_run": result.dry_run, "commit": result.commit, "pushed": result.pushed,
            "files": result.files, "applied": len(accepted)}
