"""Device tokens and pairing (REBUILD-SPEC Addendum 1 §4).

- A pairing code is short, single-use and expires in ten minutes. Only its hash is stored.
- A device token is 32 random bytes, returned once. Only its SHA-256 hash is stored; its id is the
  first 8 hex chars of that hash, and that id is the only part of a token ever logged.
- Five bad pairing codes within the TTL lock pairing until the window passes.
- Scopes: `app` (the Mac app), `worker` (the Mac transcription worker), `gpu` (iris-bot, GPU proxy
  only). App and worker requests must also carry the expected `Tailscale-User-Login` header.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets

from fastapi import Depends, HTTPException, Request

from .db import Database, now

log = logging.getLogger("scribe_hub.auth")

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"   # no 0/O, 1/I/L
CODE_LEN = 8
MAX_BAD_CODES = 5
SCOPES = {"app", "worker", "gpu"}


class AuthError(Exception):
    pass


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _normalise_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def create_pair_code(db: Database, device_name: str, ttl_s: int) -> str:
    name = device_name.strip()
    if not name:
        raise AuthError("device name is empty")
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    db.execute("INSERT INTO pair_codes (code_hash, device_name, expires_at) VALUES (?, ?, ?)",
               (_hash(code), name, now() + ttl_s))
    return code


def _issue(db: Database, name: str, scope: str) -> tuple[str, str]:
    if scope not in SCOPES:
        raise AuthError(f"unknown scope {scope!r}")
    token = secrets.token_urlsafe(32)
    h = _hash(token)
    db.execute("INSERT INTO devices (id, name, scope, token_hash, created_at) VALUES (?,?,?,?,?)",
               (h[:8], name, scope, h, now()))
    log.info("issued %s token id=%s name=%r", scope, h[:8], name)
    return h[:8], token


def redeem_pair_code(db: Database, code: str, ttl_s: int) -> tuple[str, str, str]:
    """Return (device_id, device_name, token). Raises AuthError on any failure."""
    cutoff = now() - ttl_s
    error = None
    with db.transaction():   # failures are recorded and committed; only success issues a token
        db.execute("DELETE FROM pair_failures WHERE at < ?", (cutoff,))
        fails = db.one("SELECT COUNT(*) FROM pair_failures")[0]
        row = db.one("SELECT * FROM pair_codes WHERE code_hash = ?",
                     (_hash(_normalise_code(code)),))
        if fails >= MAX_BAD_CODES:
            error = "pairing is locked after too many bad codes; try again later"
        elif row is None or row["used_at"] is not None or row["expires_at"] < now():
            db.execute("INSERT INTO pair_failures (at) VALUES (?)", (now(),))
            error = "invalid or expired pairing code"
        else:
            db.execute("UPDATE pair_codes SET used_at = ? WHERE code_hash = ?",
                       (now(), row["code_hash"]))
            device_id, token = _issue(db, row["device_name"], "app")
    if error:
        log.warning("pairing refused: %s", error)
        raise AuthError(error)
    return device_id, row["device_name"], token


def create_service_token(db: Database, name: str, scope: str) -> tuple[str, str]:
    """Worker and GPU tokens, issued from the CLI on atomsk (no pairing step)."""
    return _issue(db, name, scope)


def lookup(db: Database, token: str) -> dict | None:
    h = _hash(token)
    row = db.one("SELECT * FROM devices WHERE token_hash = ? AND revoked_at IS NULL", (h,))
    if row is None or not hmac.compare_digest(row["token_hash"], h):
        return None
    db.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (now(), row["id"]))
    return dict(row)


def list_devices(db: Database) -> list[dict]:
    rows = db.all("SELECT id, name, scope, created_at, last_seen, revoked_at FROM devices "
                  "ORDER BY created_at")
    return [dict(r) for r in rows]


def revoke(db: Database, device_id: str) -> bool:
    cur = db.execute("UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                     (now(), device_id))
    if cur.rowcount:
        log.info("revoked token id=%s", device_id)
    return bool(cur.rowcount)


# ----------------------------------------------------------------- FastAPI dependencies
def _bearer(request: Request) -> str:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    return value.strip()


def require(scope: str, tailscale: bool = True):
    def dependency(request: Request) -> dict:
        state = request.app.state
        device = lookup(state.db, _bearer(request))
        if device is None or device["scope"] != scope:
            raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
        expected = state.settings.tailscale_login
        if tailscale and expected:
            seen = request.headers.get("tailscale-user-login", "")
            if not hmac.compare_digest(seen.lower(), expected.lower()):
                log.warning("token id=%s rejected: tailscale identity mismatch", device["id"])
                raise HTTPException(403, "request is not from the expected tailnet identity")
        return device
    return Depends(dependency)


require_app = require("app")
require_worker = require("worker")
require_gpu = require("gpu", tailscale=False)   # iris-bot reaches the GPU listener directly
