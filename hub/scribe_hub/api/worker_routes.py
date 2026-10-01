"""The Mac transcription worker's side of S2 (scope `worker`)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import archive, auth
from ..db import now
from ..jobs import queue
from ..jobs.stages import accept_transcription

router = APIRouter(prefix="/worker", tags=["worker"])


class Utterance(BaseModel):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=0)
    text: str
    confidence: float | None = None


class Result(BaseModel):
    utterances: list[Utterance]
    engine: dict = {}


class Failure(BaseModel):
    error: str = Field(max_length=2000)


def _seen(request: Request, device: dict) -> None:
    request.app.state.db.execute(
        "INSERT INTO worker_seen (name, at) VALUES (?, ?) "
        "ON CONFLICT(name) DO UPDATE SET at = excluded.at", (device["name"], now()))


def _own_running(request: Request, job_id: int) -> dict:
    job = queue.get(request.app.state.db, job_id)
    if job is None or job["lane"] != "mac" or job["state"] != "running":
        raise HTTPException(409, "job is not running (cancelled, finished, or lease expired)")
    return job


@router.post("/claim")
def claim(request: Request, device=auth.require_worker):
    _seen(request, device)
    hub = request.app.state.hub
    job = queue.claim(hub.db, "mac", lease_s=hub.settings.worker_lease_s)
    if job is None:
        return Response(status_code=204)
    return {"job": job["id"], "session": job["session"], "track": job["payload"]["track"],
            "file": job["payload"]["file"], "prompt": job["payload"].get("prompt", ""),
            "lease_s": hub.settings.worker_lease_s}


@router.get("/jobs/{job_id}/audio")
def job_audio(job_id: int, request: Request, device=auth.require_worker):
    job = _own_running(request, job_id)
    path = (archive.session_dir(request.app.state.settings.archive_dir, job["session"])
            / "tracks" / job["payload"]["file"])
    return FileResponse(path, filename=job["payload"]["file"])


@router.post("/jobs/{job_id}/heartbeat")
def heartbeat(job_id: int, request: Request, device=auth.require_worker):
    _own_running(request, job_id)
    _seen(request, device)
    queue.extend(request.app.state.db, job_id, request.app.state.settings.worker_lease_s)
    return {"ok": True}


@router.post("/jobs/{job_id}/result")
def result(job_id: int, body: Result, request: Request, device=auth.require_worker):
    job = _own_running(request, job_id)
    _seen(request, device)
    try:
        accept_transcription(request.app.state.hub, job,
                             [u.model_dump() for u in body.utterances], body.engine)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    request.app.state.hub.notify()
    return {"ok": True}


@router.post("/jobs/{job_id}/fail")
def failed(job_id: int, body: Failure, request: Request, device=auth.require_worker):
    _own_running(request, job_id)
    state = queue.fail(request.app.state.db, job_id, body.error)
    return {"state": state}
