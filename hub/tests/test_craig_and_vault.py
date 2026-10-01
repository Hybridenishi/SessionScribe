"""Craig zip safety and the vault branch guards."""
from __future__ import annotations

import io
import zipfile

import pytest
from conftest import craig_zip, git, wav_bytes

from scribe_hub import craig, vaults


def test_parse_info_reads_ids_and_metadata():
    ids, rec, start = craig.parse_info(
        "Recording ABC\n\nStart time:\t2026-08-16T00:52:22Z\n\nTracks:\n"
        "\tgm_user#0 (1001)\n\tpat_user#0 (1002)\n")
    assert ids == {"gm_user": "1001", "pat_user": "1002"}
    assert rec == "ABC" and start == "2026-08-16T00:52:22Z"


def test_extract_keeps_audio_only_and_flattens_paths(tmp_path):
    data = craig_zip([(1, "gm_user", "1001")], extra={
        "../../evil-1-x.wav": wav_bytes(), "nested/2-pat_user_0.wav": wav_bytes(),
        "RunMe.command": b"#!/bin/sh\n", "ffmpeg": b"\x00", "raw.dat": b"\x00"})
    zp = tmp_path / "x.zip"
    zp.write_bytes(data)
    exp = craig.extract(zp, tmp_path / "s")
    assert [t.file for t in exp.tracks] == ["1-gm_user.wav", "2-pat_user_0.wav"]
    assert exp.tracks[1].username == "pat_user"
    files = sorted(p.name for p in (tmp_path / "s").rglob("*") if p.is_file())
    assert files == ["1-gm_user.wav", "2-pat_user_0.wav", "info.txt"]
    assert not (tmp_path / "evil-1-x.wav").exists()


def test_extract_rejects_non_zip_and_empty(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    with pytest.raises(craig.CraigError):
        craig.extract(bad, tmp_path / "a")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("info.txt", "Tracks:\n")
    empty = tmp_path / "empty.zip"
    empty.write_bytes(buf.getvalue())
    with pytest.raises(craig.CraigError, match="no audio"):
        craig.extract(empty, tmp_path / "b")


@pytest.mark.parametrize("branch", ["main", "master", "feature/x"])
def test_refuses_any_branch_but_scribe(vault, branch):
    with pytest.raises(vaults.VaultError):
        vaults.stage_files(vault[1], branch, {"_INBOX/a.md": "x"}, "m", "T <t@t>")


@pytest.mark.parametrize("rel", ["README.md", "_INBOX/../README.md", "Characters/x.md"])
def test_refuses_writes_outside_inbox(vault, rel):
    with pytest.raises(vaults.VaultError):
        vaults.stage_files(vault[1], "scribe/session-001", {rel: "x"}, "m", "T <t@t>")


def test_push_sends_only_the_scribe_branch(vault):
    origin, clone = vault
    main = git(origin, "rev-parse", "main").strip()
    sha = vaults.stage_files(clone, "scribe/session-002", {"_INBOX/t.md": "hello\n"},
                             "Stage Session 002 transcript", "Scribe Hub <s@h>", push=True)
    assert git(origin, "rev-parse", "scribe/session-002").strip() == sha
    assert git(origin, "rev-parse", "main").strip() == main
    assert "Scribe Hub" in git(origin, "log", "-1", "--format=%an", "scribe/session-002")


def test_restaging_the_same_content_makes_no_new_commit(vault):
    clone = vault[1]
    a = vaults.stage_files(clone, "scribe/session-003", {"_INBOX/t.md": "x\n"}, "m", "T <t@t>")
    b = vaults.stage_files(clone, "scribe/session-003", {"_INBOX/t.md": "x\n"}, "m", "T <t@t>")
    assert a == b


def test_duplicate_track_numbers_are_refused(tmp_path):
    zp = tmp_path / "dup.zip"
    zp.write_bytes(craig_zip([(1, "gm_user", "1001")], extra={"1-gm.user.wav": wav_bytes()}))
    with pytest.raises(craig.CraigError, match="share a track number"):
        craig.extract(zp, tmp_path / "s")


def test_parse_info_reads_the_display_name_format():
    ids, rec, start = craig.parse_info(
        "Recording XYZ\n\nRequester:\tGm (gm_user#0) (1001)\nStart time:\t2025-04-06T01:09:26Z\n\n"
        "Tracks:\n\tMisty Jelly (pat_user#0) (1002)\n\tLe/Leona (sam.user_x#0) (1003)\n"
        "\tgm_user#0 (1001)\n")
    assert ids == {"pat_user": "1002", "sam.user_x": "1003", "gm_user": "1001"}
    assert start == "2025-04-06T01:09:26Z"
