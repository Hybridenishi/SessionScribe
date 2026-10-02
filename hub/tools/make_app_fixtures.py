"""Generate SessionScribeTests/HubFixtures.swift from REAL hub responses.

Run after changing any API shape; the Swift tests decode every fixture, so a contract break between
the hub and the app fails the app's tests.

  .venv/bin/python tools/make_app_fixtures.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests"))

OUT = HERE.parent.parent / "SessionScribeTests" / "HubFixtures.swift"

# A stand-in for the Session Ingestor in hub mode:
# one card that passes the checks, one that doesn't.
FAKE_AGENT = """
import json, pathlib, sys
repo = pathlib.Path(sys.argv[1])
card = {"op": "update-section", "target": "Characters/PCs/Pat-Alpha.md", "section": "Appearance",
        "entity": "Pat Alpha", "secret": False, "conflicts_with": [],
        "evidence": [{"utterance_id": "t2-2000"}]}
props = [dict(card, proposal_id="p-060-001", after="Tall, with a **new** scar.",
              rationale="Pat was wounded at the gate."),
         dict(card, proposal_id="p-060-002", section="Quotes", after="x",
              rationale="A section that doesn't exist.")]
(repo / "_INBOX/Session-060-proposals.json").write_text(
    json.dumps({"schema": 1, "session": 60, "proposals": props}))
(repo / "_INBOX/Session-060-Downstream-Plan.md").write_text("# Plan\\n")
"""


def main() -> None:
    import conftest as c
    from fastapi.testclient import TestClient

    from scribe_hub import auth
    from scribe_hub.app import Hub, create_app

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        origin, clone = c.vault.__wrapped__(tmp)
        note = clone / "Characters" / "PCs" / "Pat-Alpha.md"
        note.parent.mkdir(parents=True)
        note.write_text("---\ntitle: Pat Alpha\ntype: pc\n---\n\n## Appearance\n\nTall.\n")
        c.git(clone, "add", "-A")
        c.git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "note")
        c.git(clone, "push", "-q", "origin", "main")
        settings = c.settings.__wrapped__(tmp, (origin, clone))
        hub = Hub(settings, gpu_transport=c.httpx.MockTransport(c.fake_naota))
        client = TestClient(create_app(hub, run_worker=False))
        code = auth.create_pair_code(hub.db, "Fixture Mac", 600)
        pair = client.post("/auth/pair", json={"code": code}, headers=c.TS).json()
        h = {"Authorization": f"Bearer {pair['token']}", **c.TS}
        _, wtok = auth.create_service_token(hub.db, "mac-worker", "worker")
        w = {"Authorization": f"Bearer {wtok}", **c.TS}

        upload = client.post("/sessions?number=60", headers=h, files={
            "file": ("c.zip", c.craig_zip([(1, "gm_user", "1001"), (2, "pat_user", "1002")]),
                     "application/zip")}).json()
        c.drain(hub)
        while (r := client.post("/worker/claim", headers=w)).status_code == 200:
            job = r.json()
            start = 1000 * job["track"]
            client.post(f"/worker/jobs/{job['job']}/heartbeat", headers=w,
                        json={"done": 1, "total": 1})
            client.post(f"/worker/jobs/{job['job']}/result", headers=w, json={
                "utterances": [{"start_ms": start, "end_ms": start + 800,
                                "text": f"Line from track {job['track']}.", "confidence": 0.9}],
                "engine": {"engine": "fixture"}})
        c.drain(hub)
        client.post("/sessions?number=61", headers=h, files={
            "file": ("c.zip", c.craig_zip([(1, "stranger", "999")]), "application/zip")})
        c.drain(hub)

        ready = client.get("/sessions/60", headers=h).json()
        agent = tmp / "agent.py"
        agent.write_text(FAKE_AGENT)
        hub.settings = replace(hub.settings, s4_command=(sys.executable, str(agent), "{dir}"))
        client.post("/sessions/60/propose", headers=h)
        c.drain(hub)
        cards = client.get("/sessions/60/proposals", headers=h).json()
        good = next(p for p in cards["proposals"] if p["state"] == "pending")
        decided = client.patch(f"/proposals/{good['id']}", headers=h,
                               json={"action": "accept"}).json()
        dry = client.post("/sessions/60/publish", headers=h, json={"dry_run": True}).json()

        fixtures = {
            "pair": {**pair, "token": "REDACTED-FIXTURE-TOKEN"},
            "status": client.get("/status", headers=h).json(),
            "sessions": client.get("/sessions", headers=h).json(),
            "sessionReady": ready,
            "proposals": cards,
            "decided": decided,
            "publishDryRun": dry,
            "sessionBlocked": client.get("/sessions/61", headers=h).json(),
            "utterances": client.get("/sessions/60/utterances?limit=10", headers=h).json(),
            "campaign": client.get("/campaign", headers=h).json(),
            "devices": client.get("/auth/devices", headers=h).json(),
            "upload": upload,
        }
        hub.db.close()

    lines = ["// Generated by hub/tools/make_app_fixtures.py from real hub responses. Do not edit.",
             "", "enum HubFixtures {"]
    for name, body in fixtures.items():
        text = json.dumps(body, indent=2, sort_keys=True)
        lines.append(f'    static let {name} = #"""\n{text}\n"""#\n')
    lines.append("}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(fixtures)} fixtures)")


if __name__ == "__main__":
    main()
