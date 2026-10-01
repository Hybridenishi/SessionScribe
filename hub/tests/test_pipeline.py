"""S1 -> S2 (Mac worker) -> S3 end to end, plus blocking, leases, restarts and re-runs."""
from __future__ import annotations

import shutil

import pytest
from conftest import CAMPAIGN, craig_zip, drain, git

from scribe_hub import archive
from scribe_hub.db import now
from scribe_hub.jobs import queue

TRACKS = [(1, "gm_user", "1001"), (2, "pat_user", "1002")]


def upload(client, headers, n=60, tracks=TRACKS):
    return client.post(f"/sessions?number={n}", headers=headers,
                       files={"file": ("craig.zip", craig_zip(tracks), "application/zip")})


def work_all(client, worker_headers, lines_for):
    """Act as the Mac worker until no job is left. lines_for(track) -> utterances."""
    done = []
    while True:
        r = client.post("/worker/claim", headers=worker_headers)
        if r.status_code == 204:
            return done
        job = r.json()
        audio = client.get(f"/worker/jobs/{job['job']}/audio", headers=worker_headers)
        assert audio.status_code == 200 and audio.content[:4] == b"RIFF"
        res = client.post(f"/worker/jobs/{job['job']}/result", headers=worker_headers,
                          json={"utterances": lines_for(job["track"]), "engine": {"model": "t"}})
        assert res.status_code == 200, res.text
        done.append(job)


def lines(track):
    if track == 1:
        return [{"start_ms": 0, "end_ms": 900, "text": "Welcome back."},
                {"start_ms": 5000, "end_ms": 6000, "text": "[BLANK_AUDIO]"}]
    return [{"start_ms": 1200, "end_ms": 2000, "text": "Alpha draws a sword."}]


def test_full_pipeline_stages_the_transcript_on_a_branch(client, hub, app_headers,
                                                          worker_headers, vault):
    origin, clone = vault
    main_before = git(origin, "rev-parse", "main").strip()

    r = upload(client, app_headers)
    assert r.status_code == 202, r.text
    assert drain(hub) == 1                                   # S1
    s = client.get("/sessions/60", headers=app_headers).json()
    assert s["state"] == "transcribing"
    assert [t["speaker"]["label"] for t in s["manifest"]["tracks"]] == \
        ["Gamemaster/DM", "Pat/Alpha"]

    jobs = work_all(client, worker_headers, lines)           # S2 on the "Mac"
    assert {j["prompt"] for j in jobs} == {CAMPAIGN["vocabulary_prompt"]}
    assert drain(hub) == 1                                   # S3

    s = client.get("/sessions/60", headers=app_headers).json()
    assert s["state"] == "ready", s
    stage3 = s["manifest"]["stage3"]
    assert stage3["branch"] == "scribe/session-060" and stage3["pushed"] is False

    u = client.get("/sessions/60/utterances", headers=app_headers).json()
    assert [x["text"] for x in u["utterances"]] == ["Welcome back.", "Alpha draws a sword."]
    assert u["utterances"][0]["id"] == "t1-0"                # [BLANK_AUDIO] was dropped

    md = (clone / "_INBOX" / "Session-060-Full-Transcript.md").read_text()
    assert md.startswith("**[00:00:00] Gamemaster/DM:** Welcome back.")
    assert git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "scribe/session-060"
    assert git(origin, "rev-parse", "main").strip() == main_before     # main untouched
    assert "scribe/session-060" not in git(origin, "branch")           # push is off by default
    blocks = (archive.session_dir(hub.settings.archive_dir, 60) / "transcript.txt").read_text()
    assert blocks == "Gamemaster/DM\nWelcome back.\n\nPat/Alpha\nAlpha draws a sword.\n\n"


def test_unmapped_speaker_blocks_until_campaign_is_fixed(client, hub, app_headers):
    tracks = TRACKS + [(3, "new_player", "1003")]
    upload(client, app_headers, n=61, tracks=tracks)
    drain(hub)
    s = client.get("/sessions/61", headers=app_headers).json()
    assert s["state"] == "blocked" and "new_player" in s["detail"]
    assert client.post("/worker/claim", headers={}).status_code == 401

    camp = client.get("/campaign", headers=app_headers).json()
    camp["speakers"].append({"label": "Sam/Gamma", "role": "player", "discord_id": "1003"})
    assert client.put("/campaign", headers=app_headers, json=camp).status_code == 200
    assert client.post("/sessions/61/retry", headers=app_headers).status_code == 202
    drain(hub)
    assert client.get("/sessions/61", headers=app_headers).json()["state"] == "transcribing"


def test_bad_campaign_is_rejected(client, app_headers):
    r = client.put("/campaign", headers=app_headers, json={"speakers": [{"role": "dm"}]})
    assert r.status_code == 422


def test_duplicate_session_number_is_refused(client, hub, app_headers):
    assert upload(client, app_headers).status_code == 202
    assert upload(client, app_headers).status_code == 409


def test_expired_lease_is_reclaimed_and_stale_result_refused(client, hub, app_headers,
                                                             worker_headers):
    upload(client, app_headers)
    drain(hub)
    first = client.post("/worker/claim", headers=worker_headers).json()
    hub.db.execute("UPDATE jobs SET lease_until = ? WHERE id = ?", (now() - 1, first["job"]))
    again = client.post("/worker/claim", headers=worker_headers).json()
    assert again["job"] == first["job"]                    # the Mac slept; job handed out again
    queue.cancel_open(hub.db, 60, "s2")
    r = client.post(f"/worker/jobs/{first['job']}/result", headers=worker_headers,
                    json={"utterances": [], "engine": {}})
    assert r.status_code == 409


def test_restart_requeues_interrupted_hub_jobs(client, hub, app_headers):
    upload(client, app_headers)
    job = queue.claim(hub.db, "hub")                       # "running" when the box rebooted
    assert queue.recover(hub.db) == 1
    assert queue.get(hub.db, job["id"])["state"] == "queued"
    assert drain(hub) == 1


def test_worker_failures_retry_then_fail(client, hub, app_headers, worker_headers):
    upload(client, app_headers)
    drain(hub)
    states = []
    for _ in range(queue.MAX_ATTEMPTS):
        job = client.post("/worker/claim", headers=worker_headers).json()
        while job["track"] != 1:                            # keep failing the same track
            client.post(f"/worker/jobs/{job['job']}/fail", headers=worker_headers,
                        json={"error": "skip"})
            job = client.post("/worker/claim", headers=worker_headers).json()
        states.append(client.post(f"/worker/jobs/{job['job']}/fail", headers=worker_headers,
                                  json={"error": "mlx crashed"}).json()["state"])
    assert states[-1] == "failed"


def test_bad_utterance_times_are_rejected(client, hub, app_headers, worker_headers):
    upload(client, app_headers)
    drain(hub)
    job = client.post("/worker/claim", headers=worker_headers).json()
    r = client.post(f"/worker/jobs/{job['job']}/result", headers=worker_headers,
                    json={"utterances": [{"start_ms": 900, "end_ms": 100, "text": "x"}]})
    assert r.status_code == 422


def test_retranscribe_cancels_and_requeues(client, hub, app_headers, worker_headers):
    upload(client, app_headers)
    drain(hub)
    work_all(client, worker_headers, lines)
    drain(hub)
    r = client.post("/sessions/60/transcribe", headers=app_headers)
    assert r.status_code == 202 and len(r.json()["jobs"]) == 2
    assert client.get("/sessions/60", headers=app_headers).json()["state"] == "transcribing"
    work_all(client, worker_headers, lines)
    drain(hub)
    assert client.get("/sessions/60", headers=app_headers).json()["state"] == "ready"


def test_session_without_a_vault_clone_is_ready_but_not_staged(client, hub, app_headers,
                                                                worker_headers, settings):
    object.__setattr__(settings, "dm_vault", None)
    upload(client, app_headers)
    drain(hub)
    work_all(client, worker_headers, lines)
    drain(hub)
    s = client.get("/sessions/60", headers=app_headers).json()
    assert s["state"] == "ready" and "not staged" in s["detail"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_audio_clip_plays_a_cited_moment(client, hub, app_headers):
    upload(client, app_headers)
    drain(hub)
    r = client.get("/sessions/60/audio/2?from=200&to=700", headers=app_headers)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"
    assert r.content[:4] == b"RIFF" and 15000 < len(r.content) < 17000   # ~0.5 s at 16 kHz
    assert client.get("/sessions/60/audio/9?from=0&to=10", headers=app_headers).status_code == 404
    assert client.get("/sessions/60/audio/1?from=50&to=10", headers=app_headers).status_code == 422
