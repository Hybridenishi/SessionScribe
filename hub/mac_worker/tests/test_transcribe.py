"""The trial recipe, without loading Whisper: a fake recognizer stands in for the model."""
from __future__ import annotations

import shutil
import subprocess

import numpy as np
import pytest
from scribe_worker import transcribe as tx


def test_regions_are_the_gaps_between_silences():
    assert tx.regions_from_silences([(0.0, 2.0), (5.0, 6.0)], 10.0) == [(2.0, 5.0), (6.0, 10.0)]
    assert tx.regions_from_silences([(0.0, 10.0)], 10.0) == []                 # all silence
    assert tx.regions_from_silences([(1.0, 1.1)], 1.2) == [(0.0, 1.0)]        # scraps dropped


def test_chunks_never_exceed_one_whisper_window():
    chunks = tx.pack([(0, 3), (3.5, 10), (12, 75)])
    assert all(b - a <= tx.MAX_CHUNK_S for a, b in chunks)
    assert chunks[0] == (0, 10)                       # close regions are joined
    assert chunks[1][0] == 12 and len(chunks) == 4   # a 63 s monologue is split


def test_loop_detection():
    assert tx.is_loop("images " * 30)
    assert not tx.is_loop("Before you ask, you're in Ravenscroft under the Vanguard's custody.")
    assert not tx.is_loop("ok ok ok")                 # too short to judge


def test_prompt_goes_to_every_chunk_and_loops_retry_without_it():
    pcm = np.zeros(tx.SR * 80, dtype=np.float32)
    calls = []

    def fake(samples, prompt):
        calls.append(prompt)
        if len(calls) == 2:
            return "images " * 30                      # loops with the prompt...
        return "a real line" if prompt or len(calls) == 3 else ""

    utts, stats = tx.transcribe(pcm, [(0, 20), (30, 50), (60, 70)], "PROMPT", fake)
    assert calls == ["PROMPT", "PROMPT", None, "PROMPT"]   # ...and is retried once without it
    assert [u["start_ms"] for u in utts] == [0, 30000, 60000]
    assert stats.loops_retried == 1 and stats.loops_dropped == 0


def test_a_chunk_that_loops_both_ways_is_dropped():
    pcm = np.zeros(tx.SR * 10, dtype=np.float32)
    utts, stats = tx.transcribe(pcm, [(0, 5)], "P", lambda s, p: "go " * 40)
    assert utts == [] and stats.loops_dropped == 1


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_silence_only_audio_produces_no_utterances(tmp_path):
    """Acceptance criterion 2: silence yields nothing, even from a model that always invents."""
    wav = tmp_path / "silence.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                    "-t", "20", str(wav)], check=True)
    pcm = tx.load_pcm(str(wav))
    regions = tx.speech_regions(str(wav), len(pcm) / tx.SR)
    bait = []
    utts, _ = tx.transcribe(pcm, regions, "P", lambda s, p: bait.append(1) or "Thank you.")
    assert regions == [] and utts == [] and bait == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_speech_between_silences_is_found(tmp_path):
    wav = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "aevalsrc='if(between(t,3,6),0.3*sin(2*PI*440*t),0)':s=16000",
                    "-t", "10", "-ac", "1", str(wav)], check=True)
    pcm = tx.load_pcm(str(wav))
    regions = tx.speech_regions(str(wav), len(pcm) / tx.SR)
    assert len(regions) == 1 and abs(regions[0][0] - 3) < 0.3 and abs(regions[0][1] - 6) < 0.3


def test_progress_reports_are_throttled_but_keep_first_and_last():
    from scribe_worker.main import _progress_reporter

    sent = []

    class FakeHub:
        def heartbeat(self, job, done, total):
            sent.append((done, total))

    now = [0.0]
    report = _progress_reporter(FakeHub(), 1, every_s=15, clock=lambda: now[0])
    for i in range(0, 11):            # 10 chunks, one every 4 s
        report(i, 10)
        now[0] += 4
    # at t=0 (first), t=16 and t=32 (15 s apart), and t=40 (last, always sent)
    assert sent == [(0, 10), (4, 10), (8, 10), (10, 10)]


def test_a_failed_progress_report_never_stops_the_work():
    import urllib.error

    from scribe_worker.main import _progress_reporter

    class DownHub:
        def heartbeat(self, *a):
            raise urllib.error.URLError("hub unreachable")

    report = _progress_reporter(DownHub(), 1)
    report(0, 5)
    report(5, 5)                      # no exception
