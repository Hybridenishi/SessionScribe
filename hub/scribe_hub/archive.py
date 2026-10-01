"""The session archive, outside git: original audio, manifest, utterances, transcripts.

archive/session-060/
  source.zip                 the Craig export as uploaded
  info.txt                   Craig's own metadata
  tracks/<file>              per-speaker audio (authoritative record)
  manifest.json              tracks, speakers, stage state
  utterances/<track>.ndjson  S2 output per track
  utterances.ndjson          merged, chronological
  transcript.txt             legacy speaker-block format
  Session-060-Full-Transcript.md
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def session_dir(archive: Path, number: int) -> Path:
    return archive / f"session-{number:03d}"


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_manifest(archive: Path, number: int) -> dict | None:
    p = session_dir(archive, number) / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_manifest(archive: Path, number: int, manifest: dict) -> None:
    write_json(session_dir(archive, number) / "manifest.json", manifest)


def write_ndjson(path: Path, rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def read_ndjson(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def probe_duration(path: Path) -> float | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
             str(path)], capture_output=True, text=True, timeout=60, check=True).stdout.strip()
        return round(float(out), 3)
    except (subprocess.SubprocessError, ValueError):
        return None


def clip_wav(path: Path, from_ms: int, to_ms: int) -> bytes:
    """A mono 16 kHz WAV of one track between two times, for playback in the app."""
    if to_ms <= from_ms:
        raise ValueError("to must be after from")
    if to_ms - from_ms > 10 * 60 * 1000:
        raise ValueError("clips are limited to 10 minutes")
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{from_ms / 1000:.3f}", "-to", f"{to_ms / 1000:.3f}",
         "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "wav", "-"],
        capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg could not cut the clip")
    return proc.stdout
