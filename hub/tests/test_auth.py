"""Pairing codes, device tokens, scopes and the Tailscale identity check (Addendum 1 §4)."""
from __future__ import annotations

import logging

from conftest import TS

from scribe_hub import auth
from scribe_hub.db import now


def pair(client, code, headers=TS):
    return client.post("/auth/pair", json={"code": code}, headers=headers)


def test_pairing_issues_a_working_token_once(client, hub):
    code = auth.create_pair_code(hub.db, "Nate's Mac", 600)
    r = pair(client, code.lower()[:4] + "-" + code.lower()[4:])   # dash and case are tolerated
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert client.get("/status", headers={"Authorization": f"Bearer {token}", **TS}
                      ).status_code == 200
    assert pair(client, code).status_code == 400                     # single use


def test_only_the_hash_of_a_token_is_stored(client, hub):
    code = auth.create_pair_code(hub.db, "Mac", 600)
    token = pair(client, code).json()["token"]
    dump = "\n".join(hub.db._conn.iterdump())
    assert token not in dump and code not in dump


def test_expired_code_is_refused(client, hub):
    code = auth.create_pair_code(hub.db, "Mac", 600)
    hub.db.execute("UPDATE pair_codes SET expires_at = ?", (now() - 1,))
    assert pair(client, code).status_code == 400


def test_five_bad_codes_lock_pairing_even_for_a_good_code(client, hub):
    good = auth.create_pair_code(hub.db, "Mac", 600)
    for _ in range(5):
        assert pair(client, "WRONGCODE").status_code == 400
    r = pair(client, good)
    assert r.status_code == 400 and "locked" in r.json()["detail"]


def test_pairing_requires_the_tailnet_identity(client, hub):
    code = auth.create_pair_code(hub.db, "Mac", 600)
    assert pair(client, code, headers={}).status_code == 403
    assert pair(client, code, headers={"Tailscale-User-Login": "someone@else"}).status_code == 403


def test_app_requests_need_token_and_tailnet_identity(client, app_headers):
    assert client.get("/status").status_code == 401
    assert client.get("/status", headers={"Authorization": "Bearer nope", **TS}).status_code == 401
    no_ts = {"Authorization": app_headers["Authorization"]}
    assert client.get("/status", headers=no_ts).status_code == 403
    assert client.get("/status", headers=app_headers).status_code == 200


def test_scopes_do_not_cross(client, worker_headers, gpu_headers):
    assert client.get("/status", headers=worker_headers).status_code == 401
    assert client.get("/status", headers={**gpu_headers, **TS}).status_code == 401


def test_revoked_device_is_refused(client, hub, app_headers):
    dev = client.get("/auth/devices", headers=app_headers).json()["devices"][0]
    assert client.delete(f"/auth/devices/{dev['id']}", headers=app_headers).status_code == 200
    assert client.get("/status", headers=app_headers).status_code == 401


def test_tokens_and_codes_never_reach_the_logs(client, hub, caplog):
    caplog.set_level(logging.DEBUG)
    code = auth.create_pair_code(hub.db, "Mac", 600)
    pair(client, "BADBADBA")
    token = pair(client, code).json()["token"]
    client.get("/status", headers={"Authorization": f"Bearer {token}"})   # 403, logged
    _, svc = auth.create_service_token(hub.db, "w", "worker")
    text = caplog.text
    assert token not in text and code not in text and svc not in text
    assert token[:8] not in text      # the id logged is the hash prefix, not the token prefix


def test_healthz_is_open_and_reveals_nothing(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
