"""GET /status — the dashboard rollup."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .. import auth
from ..db import now
from ..jobs import queue

router = APIRouter(tags=["status"])


@router.get("/healthz")
def healthz():
    """Unauthenticated liveness probe for Docker's HEALTHCHECK. Reveals nothing."""
    return {"ok": True}


@router.get("/status")
async def status(request: Request, _=auth.require_app):
    hub = request.app.state.hub
    db = hub.db
    sessions = [dict(r) for r in db.all(
        "SELECT number, state, detail, created_at FROM sessions ORDER BY number DESC LIMIT 5")]
    waiting = db.one("SELECT COUNT(*) FROM sessions WHERE state = 'blocked'")[0]
    seen = db.one("SELECT name, at FROM worker_seen ORDER BY at DESC LIMIT 1")
    return {
        "time": now(),
        "sessions": {"recent": sessions, "blocked": waiting},
        "jobs": queue.counts(db),
        "gpu": {**hub.gpu.status(), "naota_healthy": await hub.gpu.health()},
        "mac_worker": ({"name": seen["name"], "last_seen": seen["at"]} if seen else None),
        "vault": {"configured": hub.settings.dm_vault is not None,
                  "push_branches": hub.settings.push_branches},
    }
