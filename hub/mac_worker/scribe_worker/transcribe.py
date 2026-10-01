"""The S2 recipe from the Addendum 1 trial (session 59 DM track: 149 names right / 4 wrong).

1. Find speech with ffmpeg's silencedetect; never send silence to Whisper (that is where it invents
   text, and where a prompt gets echoed).
2. Pack speech into chunks of at most 28 s, so every chunk fits one Whisper window and sees the
   prompt. (mlx_audio drops `initial_prompt` after the first 30 s window otherwise.)
3. Prompt each chunk with the campaign's names written as prose.
4. Drop looping output (zlib compression ratio > 2.4, Whisper's own threshold). A chunk that loops
   with the prompt is retried once without it, so its speech is not lost.
"""
from __future__ import annotations

import re
import subprocess
import wave
import zlib
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

SR = 16000
MAX_CHUNK_S = 28.0
JOIN_GAP_S = 1.0
PAD_S = 0.2
MIN_REGION_S = 0.3
LOOP_RATIO = 2.4
SILENCE_DB = -38
SILENCE_MIN_S = 0.7

# recognize(samples, prompt) -> text
Recognizer = Callable[[np.ndarray, str | None], str]


@dataclass
class Stats:
    chunks: int = 0
    loops_retried: int = 0
    loops_dropped: int = 0
    speech_s: float = 0.0


def to_wav16k(src: str, dst: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", src, "-ac", "1", "-ar", str(SR), dst],
                   check=True, timeout=1800)


def load_pcm(path: str) -> np.ndarray:
    with wave.open(path) as w:
        if w.getframerate() != SR or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("expected 16 kHz mono 16-bit WAV")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768


def speech_regions(path: str, duration_s: float) -> list[tuple[float, float]]:
    log = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
         f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN_S}", "-f", "null", "-"],
        capture_output=True, text=True, timeout=1800, check=False).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    ends += [duration_s] * (len(starts) - len(ends))
    return regions_from_silences(list(zip(starts, ends, strict=True)), duration_s)


def regions_from_silences(silences: list[tuple[float, float]], duration_s: float
                          ) -> list[tuple[float, float]]:
    regions, cur = [], 0.0
    for s, e in silences:
        s = max(0.0, s)
        if s > cur:
            regions.append((cur, s))
        cur = max(cur, e)
    if cur < duration_s:
        regions.append((cur, duration_s))
    return [(a, b) for a, b in regions if b - a >= MIN_REGION_S]


def pack(regions: list[tuple[float, float]]) -> list[tuple[float, float]]:
    chunks: list[list[float]] = []
    for a, b in regions:
        while b - a > MAX_CHUNK_S:
            chunks.append([a, a + MAX_CHUNK_S])
            a += MAX_CHUNK_S
        if chunks and a - chunks[-1][1] <= JOIN_GAP_S and b - chunks[-1][0] <= MAX_CHUNK_S:
            chunks[-1][1] = b
        else:
            chunks.append([a, b])
    return [(a, b) for a, b in chunks]


def is_loop(text: str) -> bool:
    if len(text) < 40:
        return False
    raw = text.encode()
    return len(raw) / len(zlib.compress(raw)) > LOOP_RATIO


def transcribe(pcm: np.ndarray, regions: list[tuple[float, float]], prompt: str | None,
               recognize: Recognizer, on_progress: Callable[[int, int], None] | None = None
               ) -> tuple[list[dict], Stats]:
    dur = len(pcm) / SR
    chunks = pack(regions)
    stats = Stats(chunks=len(chunks), speech_s=round(sum(b - a for a, b in chunks), 1))
    out = []
    for i, (a, b) in enumerate(chunks):
        seg = pcm[int(max(0.0, a - PAD_S) * SR):int(min(dur, b + PAD_S) * SR)]
        text = (recognize(seg, prompt or None) or "").strip()
        if is_loop(text) and prompt:
            stats.loops_retried += 1
            text = (recognize(seg, None) or "").strip()
        if is_loop(text):
            stats.loops_dropped += 1
            text = ""
        if text:
            out.append({"start_ms": int(a * 1000), "end_ms": int(b * 1000), "text": text})
        if on_progress:
            on_progress(i + 1, len(chunks))
    return out, stats


def mlx_recognizer(model_path: str) -> Recognizer:
    """Whisper large-v3-turbo via mlx_audio (the model folder needs the HF tokenizer files)."""
    from mlx_audio.stt.utils import load_model

    model = load_model(model_path)

    def recognize(samples: np.ndarray, prompt: str | None) -> str:
        res = model.generate(samples, initial_prompt=prompt, language="en",
                             condition_on_previous_text=False)
        return res.text or ""

    return recognize
