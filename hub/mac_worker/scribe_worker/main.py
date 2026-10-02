"""The Mac transcription worker: pull S2 jobs from the hub, transcribe, post results.

Runs under launchd (see com.natedavis.scribe-worker.plist). Configuration:
  SCRIBE_HUB_URL       e.g. https://atomsk.<tailnet>.ts.net:8443
  SCRIBE_WORKER_TOKEN  path to a 0600 file holding the worker token (never the token itself)
  SCRIBE_WHISPER_MODEL path to the MLX Whisper model folder
Audio is downloaded to a private temp folder and deleted after each job.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import transcribe as tx

log = logging.getLogger("scribe_worker")
POLL_S = 20
HEARTBEAT_S = 300
PROGRESS_S = 15          # how often to report chunk progress for the app's progress bar


class Hub:
    def __init__(self, base: str, token: str, opener=urllib.request.urlopen):
        self.base = base.rstrip("/")
        self._token = token
        self._open = opener

    def _req(self, method: str, path: str, body: dict | None = None, timeout: float = 60):
        req = urllib.request.Request(
            f"{self.base}{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self._token}",
                     "Content-Type": "application/json"})
        return self._open(req, timeout=timeout)

    def claim(self) -> dict | None:
        with self._req("POST", "/worker/claim") as r:
            return None if r.status == 204 else json.loads(r.read())

    def download(self, job: int, dest: Path) -> None:
        with self._req("GET", f"/worker/jobs/{job}/audio", timeout=600) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f, 1024 * 1024)

    def heartbeat(self, job: int, done: int | None = None, total: int | None = None) -> None:
        body = {"done": done, "total": total} if done is not None and total is not None else None
        with self._req("POST", f"/worker/jobs/{job}/heartbeat", body):
            pass

    def result(self, job: int, utterances: list[dict], engine: dict) -> None:
        with self._req("POST", f"/worker/jobs/{job}/result",
                       {"utterances": utterances, "engine": engine}, timeout=120):
            pass

    def fail(self, job: int, error: str) -> None:
        with self._req("POST", f"/worker/jobs/{job}/fail", {"error": error[:1900]}):
            pass


def _progress_reporter(hub: Hub, jid: int, every_s: float = PROGRESS_S, clock=time.monotonic):
    """on_progress callback: report at most every `every_s`, always the first and the last.
    A failed report is logged and skipped; it must never stop the transcription."""
    last = [float("-inf")]

    def report(done: int, total: int) -> None:
        t = clock()
        if done not in (0, total) and t - last[0] < every_s:
            return
        last[0] = t
        try:
            hub.heartbeat(jid, done, total)
        except (urllib.error.URLError, OSError) as exc:
            log.warning("progress report for job %s failed: %s", jid, exc)

    return report


def run_job(hub: Hub, job: dict, recognize: tx.Recognizer, workdir: Path) -> dict:
    jid = job["job"]
    stop = threading.Event()

    def beat():
        while not stop.wait(HEARTBEAT_S):
            try:
                hub.heartbeat(jid)
            except (urllib.error.URLError, OSError) as exc:
                log.warning("heartbeat for job %s failed: %s", jid, exc)

    t = threading.Thread(target=beat, daemon=True)
    t.start()
    try:
        src = workdir / job["file"]
        hub.download(jid, src)
        wav = workdir / "track.wav"
        tx.to_wav16k(str(src), str(wav))
        pcm = tx.load_pcm(str(wav))
        regions = tx.speech_regions(str(wav), len(pcm) / tx.SR)
        started = time.time()
        report = _progress_reporter(hub, jid)
        report(0, len(tx.pack(regions)))          # "started": the app stops showing "waiting"
        utts, stats = tx.transcribe(pcm, regions, job.get("prompt") or None, recognize,
                                    on_progress=report)
        engine = {"engine": "whisper-large-v3-turbo-mlx", "recipe": "addendum-1",
                  "chunks": stats.chunks, "speech_s": stats.speech_s,
                  "loops_retried": stats.loops_retried, "loops_dropped": stats.loops_dropped,
                  "elapsed_s": round(time.time() - started, 1)}
        hub.result(jid, utts, engine)
        log.info("job %s (session %s track %s): %d utterances, %s", jid, job["session"],
                 job["track"], len(utts), engine)
        return engine
    finally:
        stop.set()
        for p in workdir.iterdir():
            p.unlink(missing_ok=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    base = os.environ["SCRIBE_HUB_URL"]
    token = Path(os.environ["SCRIBE_WORKER_TOKEN"]).read_text().strip()
    model = os.environ.get("SCRIBE_WHISPER_MODEL",
                           str(Path.home() / ".omlx/models/whisper-large-v3-turbo"))
    hub = Hub(base, token)
    recognize = tx.mlx_recognizer(model)
    log.info("worker ready (hub=%s)", base)
    while True:
        try:
            job = hub.claim()
        except (urllib.error.URLError, OSError) as exc:
            log.warning("hub unreachable: %s", exc)
            time.sleep(POLL_S)
            continue
        if job is None:
            time.sleep(POLL_S)
            continue
        with tempfile.TemporaryDirectory(prefix="scribe-worker-") as d:
            try:
                run_job(hub, job, recognize, Path(d))
            except Exception as exc:  # noqa: BLE001 — report it and keep serving
                log.exception("job %s failed", job["job"])
                try:
                    hub.fail(job["job"], f"{type(exc).__name__}: {exc}")
                except (urllib.error.URLError, OSError):
                    pass


if __name__ == "__main__":
    sys.exit(main())
