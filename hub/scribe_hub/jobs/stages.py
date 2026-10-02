"""Pipeline stages S1–S3 (REBUILD-SPEC §4). Each is idempotent: running it twice gives the same
result, so a job interrupted by a reboot can simply run again.

S1 ingest     Craig zip -> archive (tracks, info.txt, manifest). Unmapped speaker -> blocked.
S2 transcribe One `mac` job per track, claimed by the Mac worker; results arrive via the API.
S3 stage      Merge tracks -> utterances.ndjson + transcripts; commit the Markdown transcript to
              `_INBOX/` on branch `scribe/session-NNN` of the hub's Azora-DM clone.
"""
from __future__ import annotations

import logging

from .. import archive, campaign, craig, transcript, vaults
from ..db import Database, now
from . import queue

log = logging.getLogger("scribe_hub.stages")


def set_session(db: Database, number: int, state: str, detail: str | None = None) -> None:
    db.execute("UPDATE sessions SET state = ?, detail = ? WHERE number = ?",
               (state, detail, number))


# ----------------------------------------------------------------- S1
def s1_ingest(hub, job: dict) -> None:
    n = job["session"]
    sdir = archive.session_dir(hub.settings.archive_dir, n)
    export = craig.extract(sdir / "source.zip", sdir)
    camp = campaign.load(hub.settings.campaign_file)
    tracks, unmapped = [], []
    for t in export.tracks:
        sp = camp.resolve(t.username, t.discord_id)
        if sp is None:
            unmapped.append({"index": t.index, "username": t.username, "discord_id": t.discord_id})
        tracks.append({
            "index": t.index, "file": t.file, "username": t.username, "discord_id": t.discord_id,
            "speaker": ({"label": sp.label, "role": sp.role, "pc": sp.pc} if sp else None),
            "duration_s": archive.probe_duration(sdir / "tracks" / t.file),
        })
    manifest = {
        "session": n, "created_at": (archive.read_manifest(hub.settings.archive_dir, n) or {})
        .get("created_at", now()),
        "craig": {"recording_id": export.recording_id, "start_time": export.start_time},
        "tracks": tracks, "unmapped": unmapped, "stage3": None,
    }
    archive.write_manifest(hub.settings.archive_dir, n, manifest)
    if unmapped:
        who = ", ".join(f"{u['username']} ({u['discord_id'] or 'no id'})" for u in unmapped)
        queue.block(hub.db, job["id"], f"unmapped speaker(s): {who}")
        set_session(hub.db, n, "blocked", f"Add to campaign.yaml, then retry: {who}")
        log.info("session %s blocked: %d unmapped track(s)", n, len(unmapped))
        return
    queue.finish(hub.db, job["id"])
    start_transcription(hub, n, manifest, camp.vocabulary_prompt)


# ----------------------------------------------------------------- S2
def start_transcription(hub, n: int, manifest: dict, prompt: str) -> list[int]:
    queue.cancel_open(hub.db, n, "s2")
    queue.cancel_open(hub.db, n, "s3")
    ids = [queue.enqueue(hub.db, n, "s2", "mac",
                         {"track": t["index"], "file": t["file"], "prompt": prompt})
           for t in manifest["tracks"]]
    set_session(hub.db, n, "transcribing", f"{len(ids)} track(s) waiting for the Mac worker")
    return ids


def accept_transcription(hub, job: dict, utterances: list[dict], engine: dict) -> None:
    """Called when the Mac worker posts a track's result."""
    n, track = job["session"], job["payload"]["track"]
    clean = []
    for u in utterances:
        start, end = int(u["start_ms"]), int(u["end_ms"])
        if end < start:
            raise ValueError("an utterance ends before it starts")
        clean.append({"start_ms": start, "end_ms": end, "text": str(u.get("text", "")),
                      "confidence": u.get("confidence")})
    out = archive.session_dir(hub.settings.archive_dir, n) / "utterances"
    out.mkdir(exist_ok=True)
    archive.write_ndjson(out / f"track-{track}.ndjson", clean)
    queue.finish(hub.db, job["id"], {"engine": engine, "utterances": len(clean)})
    _maybe_stage(hub, n)


def _maybe_stage(hub, n: int) -> None:
    jobs = queue.for_session(hub.db, n)
    s2 = [j for j in jobs if j["stage"] == "s2" and j["state"] != "cancelled"]
    if s2 and all(j["state"] == "done" for j in s2):
        if not any(j["stage"] == "s3" and j["state"] in ("queued", "running") for j in jobs):
            queue.enqueue(hub.db, n, "s3", "hub")
            set_session(hub.db, n, "staging", None)


# ----------------------------------------------------------------- S3
def s3_stage(hub, job: dict) -> None:
    n = job["session"]
    s = hub.settings
    manifest = archive.read_manifest(s.archive_dir, n)
    sdir = archive.session_dir(s.archive_dir, n)
    per_track = {t["index"]: archive.read_ndjson(sdir / "utterances" / f"track-{t['index']}.ndjson")
                 for t in manifest["tracks"]}
    speakers = {t["index"]: t["speaker"] for t in manifest["tracks"]}
    utts = transcript.merge(per_track, speakers)
    archive.write_ndjson(sdir / "utterances.ndjson", utts)
    (sdir / "transcript.txt").write_text(transcript.to_blocks(utts), encoding="utf-8")
    name = f"Session-{n:03d}-Full-Transcript.md"
    md = transcript.to_markdown(utts)
    (sdir / name).write_text(md, encoding="utf-8")

    result = {"utterances": len(utts), "branch": None, "commit": None, "pushed": False}
    if s.dm_vault:
        branch = f"scribe/session-{n:03d}"
        sha = vaults.stage_files(
            s.dm_vault, branch, {f"_INBOX/{name}": md},
            f"Stage Session {n:03d} transcript for ingest", s.git_author,
            push=s.push_branches)
        result.update(branch=branch, commit=sha, pushed=s.push_branches)
    manifest["stage3"] = {**result, "at": now()}
    archive.write_manifest(s.archive_dir, n, manifest)
    queue.finish(hub.db, job["id"], result)
    note = None if s.dm_vault else "Transcript ready; no vault clone configured, so not staged"
    set_session(hub.db, n, "ready", note)
    if s.dm_vault and (s.s4_provider or s.s4_command):
        queue.enqueue(hub.db, n, "s4", "hub")          # S4 drafts proposals for review


def _s4(hub, job):
    from .s4_propose import s4_propose  # local import: s4 imports archive/vaults, like us
    s4_propose(hub, job)


HUB_STAGES = {"s1": s1_ingest, "s3": s3_stage, "s4": _s4}
