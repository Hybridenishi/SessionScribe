"""The real Mac worker code against the real hub app; only Whisper is faked."""
from __future__ import annotations

import io
import shutil
import subprocess
import urllib.error
import zipfile

import pytest
from conftest import drain

pytest.importorskip("scribe_worker")
from scribe_worker import main as worker  # noqa: E402

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


class _Resp(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(body)
        self.status = status


def client_opener(client, extra_headers):
    """Make urllib calls land on the FastAPI TestClient."""
    def opener(req, timeout=None):
        path = req.full_url.split("://", 1)[1].split("/", 1)[1]
        headers = {**dict(req.header_items()), **extra_headers}
        r = client.request(req.get_method(), "/" + path, content=req.data, headers=headers)
        if r.status_code >= 400:
            raise urllib.error.HTTPError(req.full_url, r.status_code, r.text, {}, None)
        return _Resp(r.status_code, r.content)
    return opener


def tone_track(tmp_path, name):
    wav = tmp_path / name
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "aevalsrc='if(between(t,1,3),0.3*sin(2*PI*330*t),0)':s=16000",
                    "-t", "5", "-ac", "1", str(wav)], check=True)
    return wav.read_bytes()


def test_worker_transcribes_and_hub_stages(tmp_path, client, hub, app_headers, worker_headers):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("info.txt", "Tracks:\n\tgm_user#0 (1001)\n\tpat_user#0 (1002)\n")
        zf.writestr("1-gm_user.wav", tone_track(tmp_path, "a.wav"))
        zf.writestr("2-pat_user.wav", tone_track(tmp_path, "b.wav"))
    client.post("/sessions?number=70", headers=app_headers,
                files={"file": ("c.zip", buf.getvalue(), "application/zip")})
    drain(hub)

    token = worker_headers["Authorization"].split(" ", 1)[1]
    ts = {k: v for k, v in worker_headers.items() if k != "Authorization"}
    h = worker.Hub("https://hub.test", token, opener=client_opener(client, ts))
    seen_prompts = []

    def fake_whisper(samples, prompt):
        seen_prompts.append(prompt)
        return "The party enters the hall."

    for _ in range(2):
        job = h.claim()
        work = tmp_path / f"w{job['job']}"
        work.mkdir()
        engine = worker.run_job(h, job, fake_whisper, work)
        assert engine["chunks"] == 1 and list(work.iterdir()) == []   # temp audio deleted
    assert h.claim() is None
    assert set(seen_prompts) == {"A game in a test world. Alpha talks with Beta."}

    drain(hub)
    s = client.get("/sessions/70", headers=app_headers).json()
    assert s["state"] == "ready"
    u = client.get("/sessions/70/utterances", headers=app_headers).json()["utterances"]
    assert [(x["speaker"], x["start_ms"] // 1000) for x in u] == \
        [("Gamemaster/DM", 1), ("Pat/Alpha", 1)]
    s2 = [j for j in s["jobs"] if j["stage"] == "s2"]
    assert all(j["state"] == "done" for j in s2)
    assert all(j["progress"]["done"] == j["progress"]["total"] == 1 for j in s2)
