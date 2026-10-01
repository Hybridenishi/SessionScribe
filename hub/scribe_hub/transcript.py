"""Merge per-track utterances into one chronological transcript, in the formats the vault and the
old `scribe` pipeline already use:

  transcript.txt                    "Label\\ntext\\n\\n" blocks
  Session-NNN-Full-Transcript.md    "**[HH:MM:SS] Label:** text"
"""
from __future__ import annotations

import re

MERGE_GAP_MS = 2000
HALLUCINATION_PATTERNS = [
    re.compile(r"^\s*(\[.*\]|\(.*\))\s*$"),                                   # [BLANK_AUDIO]
    re.compile(r"^\s*(thanks? (you )?for watching|please subscribe|subtitles? by).*", re.I),
    re.compile(r"^\s*\.+\s*$"),
]


def is_hallucination(text: str) -> bool:
    return not text.strip() or any(p.match(text) for p in HALLUCINATION_PATTERNS)


def utterance_id(track: int, start_ms: int) -> str:
    """Stable across merges, so evidence citations survive (they only change if S2 re-runs)."""
    return f"t{track}-{start_ms}"


def merge(per_track: dict[int, list[dict]], speakers: dict[int, dict]) -> list[dict]:
    """per_track: {track_index: [{start_ms, end_ms, text, confidence?}]}.
    Returns chronological utterances with id, track and speaker label."""
    rows = []
    for track, utts in per_track.items():
        sp = speakers[track]
        for u in utts:
            text = (u.get("text") or "").strip()
            if is_hallucination(text):
                continue
            rows.append({
                "id": utterance_id(track, int(u["start_ms"])),
                "track": track,
                "speaker": sp["label"],
                "role": sp["role"],
                "start_ms": int(u["start_ms"]),
                "end_ms": int(u["end_ms"]),
                "text": text,
                "confidence": u.get("confidence"),
            })
    rows.sort(key=lambda r: (r["start_ms"], r["track"]))
    seen: dict[str, int] = {}
    for r in rows:                      # two lines starting on the same ms keep distinct ids
        k = r["id"]
        if k in seen:
            seen[k] += 1
            r["id"] = f"{k}.{seen[k]}"
        else:
            seen[k] = 0
    return rows


def paragraphs(utterances: list[dict]) -> list[dict]:
    """Collapse consecutive lines from one speaker with short gaps, as merge-tracks.js did."""
    out: list[dict] = []
    for u in utterances:
        prev = out[-1] if out else None
        same = prev and prev["speaker"] == u["speaker"]
        if same and u["start_ms"] - prev["end_ms"] <= MERGE_GAP_MS:
            prev["text"] += " " + u["text"]
            prev["end_ms"] = u["end_ms"]
        else:
            out.append({"speaker": u["speaker"], "start_ms": u["start_ms"],
                        "end_ms": u["end_ms"], "text": u["text"]})
    return out


def _hms(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def to_blocks(utterances: list[dict]) -> str:
    return "".join(f"{p['speaker']}\n{p['text']}\n\n" for p in paragraphs(utterances))


def to_markdown(utterances: list[dict]) -> str:
    return "".join(f"**[{_hms(p['start_ms'])}] {p['speaker']}:** {p['text']}\n\n"
                   for p in paragraphs(utterances))
