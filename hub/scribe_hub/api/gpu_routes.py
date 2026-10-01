"""The GPU-queue proxy (scope `gpu`). Served only by the GPU app on its own port.

OpenAI-compatible paths, so iris-bot only changes IRIS_LLM_BASE (and sends its token).
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from .. import auth

router = APIRouter(tags=["gpu"])


@router.get("/health")
async def health(request: Request):
    ok = await request.app.state.hub.gpu.health()
    return Response(status_code=200 if ok else 503)


@router.post("/v1/chat/completions")
async def chat(body: dict, request: Request, _=auth.require_gpu):
    if body.get("stream"):
        return Response('{"error":"streaming is not supported by the GPU queue"}',
                        status_code=400, media_type="application/json")
    resp = await request.app.state.hub.gpu.run("POST", "/v1/chat/completions", body)
    return Response(resp.content, status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"))
