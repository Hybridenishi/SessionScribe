"""Read a Craig multi-track export (.zip) safely.

Craig zips hold one audio file per speaker (`1-username.flac`, `2-username_0.aac`, …), an
`info.txt` naming each track's Discord user and id, and sometimes a local-mix kit (`RunMe.command`,
`ffmpeg`, `raw.dat`) that we ignore. Only audio and info.txt are extracted, flat, by basename, so a
crafted path inside the zip cannot write outside the session folder.
"""
from __future__ import annotations

import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

AUDIO_RE = re.compile(r"^(\d+)-(.+?)(?:_\d+)?\.(aac|flac|ogg|opus|m4a|wav|mp3)$", re.I)
# Track lines come in two shapes:  "\tuser#0 (123)"  and, in newer exports,
# "\tDisplay Name (user#0) (123)". Either way the username is the token before "#N".
INFO_TRACK_RE = re.compile(r"(?:^\s*|\()([^\s#()]+)#\d+\)?\s*\((\d+)\)\s*$")
MAX_UNCOMPRESSED = 12 * 1024**3     # a 4-hour, 8-track FLAC export is ~3 GB


class CraigError(ValueError):
    pass


@dataclass(frozen=True)
class Track:
    index: int
    file: str
    username: str
    discord_id: str | None


@dataclass(frozen=True)
class Export:
    tracks: tuple[Track, ...]
    recording_id: str | None
    start_time: str | None


def parse_info(text: str) -> tuple[dict[str, str], str | None, str | None]:
    """Return ({username: discord_id}, recording id, start time) from info.txt."""
    ids, rec, start, in_tracks = {}, None, None, False
    for line in text.splitlines():
        if line.startswith("Recording "):
            rec = line.split(" ", 1)[1].strip()
        elif line.lower().startswith("start time:"):
            start = line.split(":", 1)[1].strip()
        elif line.strip().lower() == "tracks:":
            in_tracks = True
        elif in_tracks:
            m = INFO_TRACK_RE.search(line)
            if m:
                ids[m.group(1).lower()] = m.group(2)
            elif line.strip():
                in_tracks = False
    return ids, rec, start


def extract(zip_path: Path, dest: Path) -> Export:
    """Extract audio tracks into dest/tracks and info.txt into dest. Returns what was found."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise CraigError("not a zip file") from exc
    with zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if sum(m.file_size for m in members) > MAX_UNCOMPRESSED:
            raise CraigError("export is larger than the hub accepts")
        tracks_dir = dest / "tracks"
        tracks_dir.mkdir(parents=True, exist_ok=True)
        info_text, found = "", []
        for m in members:
            base = PurePosixPath(m.filename.replace("\\", "/")).name
            if base.lower() == "info.txt":
                info_text = zf.read(m).decode("utf-8", "replace")
                (dest / "info.txt").write_text(info_text, encoding="utf-8")
                continue
            match = AUDIO_RE.match(base)
            if not match or base.startswith("."):
                continue
            with zf.open(m) as src, open(tracks_dir / base, "wb") as out:
                shutil.copyfileobj(src, out, 1024 * 1024)
            found.append((int(match.group(1)), base, match.group(2)))
    if not found:
        raise CraigError("no audio tracks found in the export")
    indexes = [i for i, _, _ in found]
    if len(set(indexes)) != len(indexes):
        raise CraigError("two audio files share a track number; is this one Craig export?")
    ids, rec, start = parse_info(info_text)
    tracks = tuple(Track(index=i, file=f, username=u, discord_id=ids.get(u.lower()))
                   for i, f, u in sorted(found))
    return Export(tracks=tracks, recording_id=rec, start_time=start)
