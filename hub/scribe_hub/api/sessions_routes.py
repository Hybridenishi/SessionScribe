"""Sessions: upload a Craig export (S1), follow the stages, read the transcript, play clips."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile

from .. import archive, auth, campaign
from ..db import now
from ..jobs import queue
from ..jobs.stages import start_transcription

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _session(db, n: int) -> dict:
    row = db.one("SELECT * FROM sessions WHERE number = ?", (n,))
    if row is None:
        raise HTTPException(404, f"no session {n}")
    return dict(row)


@router.post("", status_code=202)
def create_session(request: Request, number: int = Query(ge=1, le=9999),
                         file: UploadFile = File(...), _=auth.require_app):
    """Upload a Craig .zip for session `number`. Starts S1 and returns its job id."""
    hub = request.app.state.hub
    if hub.db.one("SELECT 1 FROM sessions WHERE number = ?", (number,)):
        raise HTTPException(409, f"session {number} already exists")
    sdir = archive.session_dir(hub.settings.archive_dir, number)
    sdir.mkdir(parents=True, exist_ok=True)
    with open(sdir / "source.zip", "wb") as out:
        shutil.copyfileobj(file.file, out, 1024 * 1024)
    hub.db.execute("INSERT INTO sessions (number, created_at, state) VALUES (?, ?, 'ingesting')",
                   (number, now()))
    job_id = queue.enqueue(hub.db, number, "s1", "hub")
    hub.notify()
    return {"session": number, "job": job_id}


@router.get("")
def list_sessions(request: Request, _=auth.require_app):
    rows = request.app.state.db.all("SELECT * FROM sessions ORDER BY number DESC")
    return {"sessions": [dict(r) for r in rows]}


@router.get("/{n}")
def get_session(n: int, request: Request, _=auth.require_app):
    db = request.app.state.db
    s = _session(db, n)
    jobs = [{k: j[k] for k in ("id", "stage", "lane", "state", "attempts", "error", "updated_at")}
            | {"track": j["payload"].get("track"), "progress": j["payload"].get("progress")}
            for j in queue.for_session(db, n)]
    return {**s, "manifest": archive.read_manifest(request.app.state.settings.archive_dir, n),
            "jobs": jobs}


@router.get("/{n}/utterances")
def utterances(n: int, request: Request, offset: int = Query(0, ge=0),
               limit: int = Query(200, ge=1, le=2000), _=auth.require_app):
    _session(request.app.state.db, n)
    sdir = archive.session_dir(request.app.state.settings.archive_dir, n)
    rows = archive.read_ndjson(sdir / "utterances.ndjson")
    return {"total": len(rows), "offset": offset, "utterances": rows[offset:offset + limit]}


@router.get("/{n}/audio/{track}")
def audio_clip(n: int, track: int, request: Request, from_ms: int = Query(alias="from", ge=0),
               to_ms: int = Query(alias="to", ge=1), _=auth.require_app):
    """A WAV clip of one speaker's track, for playing a cited moment."""
    settings = request.app.state.settings
    _session(request.app.state.db, n)
    manifest = archive.read_manifest(settings.archive_dir, n) or {}
    t = next((t for t in manifest.get("tracks", []) if t["index"] == track), None)
    if t is None:
        raise HTTPException(404, f"no track {track}")
    path = archive.session_dir(settings.archive_dir, n) / "tracks" / t["file"]
    try:
        data = archive.clip_wav(path, from_ms, to_ms)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return Response(data, media_type="audio/wav")


@router.post("/{n}/retry", status_code=202)
def retry(n: int, request: Request, _=auth.require_app):
    """Re-run S1 after fixing campaign.yaml (e.g. an unmapped speaker), or after a failure."""
    hub = request.app.state.hub
    s = _session(hub.db, n)
    if s["state"] not in ("blocked", "failed"):
        raise HTTPException(409, f"session {n} is {s['state']}, not blocked or failed")
    queue.cancel_open(hub.db, n, "s1")
    job_id = queue.enqueue(hub.db, n, "s1", "hub")
    hub.db.execute("UPDATE sessions SET state = 'ingesting', detail = NULL WHERE number = ?", (n,))
    hub.notify()
    return {"session": n, "job": job_id}


@router.post("/{n}/transcribe", status_code=202)
def retranscribe(n: int, request: Request, _=auth.require_app):
    """Re-run S2 for every track (and then S3)."""
    hub = request.app.state.hub
    _session(hub.db, n)
    manifest = archive.read_manifest(hub.settings.archive_dir, n)
    if not manifest or manifest.get("unmapped"):
        raise HTTPException(409, "session has not finished S1")
    prompt = campaign.load(hub.settings.campaign_file).vocabulary_prompt
    return {"session": n, "jobs": start_transcription(hub, n, manifest, prompt)}
