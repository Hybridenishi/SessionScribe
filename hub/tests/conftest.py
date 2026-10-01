"""Shared fixtures. Everything is synthetic: fake players, fake audio, a throwaway git vault."""
from __future__ import annotations

import io
import subprocess
import wave
import zipfile
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from scribe_hub import auth
from scribe_hub.app import Hub, create_app, create_gpu_app
from scribe_hub.config import Settings

TS_LOGIN = "dm@example.com"
TS = {"Tailscale-User-Login": TS_LOGIN}

CAMPAIGN = {
    "speakers": [
        {"label": "Gamemaster/DM", "role": "dm", "discord_id": "1001", "usernames": ["gm_user"]},
        {"label": "Pat/Alpha", "role": "player", "pc": "alpha", "discord_id": "1002",
         "usernames": ["pat_user"]},
    ],
    "vocabulary_prompt": "A game in a test world. Alpha talks with Beta.",
}


def wav_bytes(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(seconds * rate))
    return buf.getvalue()


def craig_zip(tracks: list[tuple[int, str, str | None]], extra: dict[str, bytes] | None = None
              ) -> bytes:
    """tracks: [(index, username, discord_id)]"""
    info = ["Recording TESTREC", "", "Start time:\t2026-10-01T00:00:00.000Z", "", "Tracks:"]
    info += [f"\t{u}#0 ({d})" if d else f"\t{u}#0" for _, u, d in tracks]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("info.txt", "\n".join(info) + "\n")
        for i, u, _ in tracks:
            zf.writestr(f"{i}-{u}.wav", wav_bytes())
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout


@pytest.fixture
def vault(tmp_path) -> tuple[Path, Path]:
    """A bare 'GitHub' origin plus the hub's clone of it, with one commit on main."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    (seed / "_INBOX").mkdir()
    (seed / "_INBOX" / ".keep").write_text("")
    (seed / "README.md").write_text("test vault\n")
    git(seed, "add", "-A")
    git(seed, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "seed")
    git(seed, "push", "-q", "origin", "main")
    clone = tmp_path / "hub-clone"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    return origin, clone


@pytest.fixture
def settings(tmp_path, vault) -> Settings:
    data = tmp_path / "data"
    data.mkdir()
    (data / "campaign.yaml").write_text(yaml.safe_dump(CAMPAIGN))
    return Settings(data_dir=data, dm_vault=vault[1], campaign_file=data / "campaign.yaml",
                    tailscale_login=TS_LOGIN, naota_llm_base="http://naota.test:8080")


def fake_naota(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health":
        return httpx.Response(200, json={"status": "ok"})
    return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})


@pytest.fixture
def hub(settings) -> Hub:
    h = Hub(settings, gpu_transport=httpx.MockTransport(fake_naota))
    yield h
    h.db.close()


@pytest.fixture
def client(hub) -> TestClient:
    return TestClient(create_app(hub, run_worker=False))


@pytest.fixture
def gpu_client(hub) -> TestClient:
    return TestClient(create_gpu_app(hub))


@pytest.fixture
def app_headers(hub) -> dict:
    code = auth.create_pair_code(hub.db, "Test Mac", 600)
    _, _, token = auth.redeem_pair_code(hub.db, code, 600)
    return {"Authorization": f"Bearer {token}", **TS}


@pytest.fixture
def worker_headers(hub) -> dict:
    _, token = auth.create_service_token(hub.db, "test-worker", "worker")
    return {"Authorization": f"Bearer {token}", **TS}


@pytest.fixture
def gpu_headers(hub) -> dict:
    _, token = auth.create_service_token(hub.db, "iris-bot", "gpu")
    return {"Authorization": f"Bearer {token}"}


def drain(hub: Hub) -> int:
    """Run in-process jobs until the hub lane is empty."""
    n = 0
    while hub.run_one_hub_job():
        n += 1
    return n
