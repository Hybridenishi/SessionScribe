"""POST /auth/pair, GET /auth/devices, DELETE /auth/devices/{id} (Addendum 1 §4)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import auth

router = APIRouter(prefix="/auth", tags=["auth"])


class PairRequest(BaseModel):
    code: str = Field(min_length=4, max_length=32)


class PairResponse(BaseModel):
    device_id: str
    device_name: str
    token: str


@router.post("/pair", response_model=PairResponse)
def pair(body: PairRequest, request: Request):
    """Exchange a one-time code (printed by `scribe-hub pair`) for a device token. The token is
    returned once and never stored in clear."""
    s = request.app.state
    expected = s.settings.tailscale_login
    if expected and request.headers.get("tailscale-user-login", "").lower() != expected.lower():
        raise HTTPException(403, "request is not from the expected tailnet identity")
    try:
        device_id, name, token = auth.redeem_pair_code(s.db, body.code, s.settings.pair_code_ttl_s)
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc)) from None
    return PairResponse(device_id=device_id, device_name=name, token=token)


@router.get("/devices")
def devices(request: Request, _=auth.require_app):
    return {"devices": auth.list_devices(request.app.state.db)}


@router.delete("/devices/{device_id}")
def revoke(device_id: str, request: Request, _=auth.require_app):
    if not auth.revoke(request.app.state.db, device_id):
        raise HTTPException(404, "no such active device")
    return {"revoked": device_id}
