"""Git operations on the hub's own clone of a vault.

Rules (REBUILD-SPEC §5.3, Azora-DM AGENTS.md):
- The hub only ever commits to `scribe/…` branches. It never commits to, merges into, or pushes
  `main` here; publishing to main is H2 and needs Nate's approval in the app.
- Never force-push, never rewrite history, never reset a shared branch.
- Writes are confined to an allow-listed folder (S3: `_INBOX/`).
"""
from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("scribe_hub.vaults")
_LOCK = threading.Lock()          # one git operation at a time per process
PROTECTED = {"main", "master"}


class VaultError(RuntimeError):
    pass


def _git(repo: Path, *args: str, author: str | None = None) -> str:
    cmd = ["git", "-C", str(repo)]
    if author:
        name, _, email = author.partition("<")
        cmd += ["-c", f"user.name={name.strip()}", "-c", f"user.email={email.rstrip('>').strip()}"]
    proc = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise VaultError(f"git {args[0]} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def _safe_target(repo: Path, rel: str, allowed_prefix: str) -> Path:
    target = (repo / rel).resolve()
    root = (repo / allowed_prefix).resolve()
    if root not in target.parents:
        raise VaultError(f"refusing to write outside {allowed_prefix}: {rel}")
    return target


def stage_files(repo: Path, branch: str, files: dict[str, str], message: str, author: str,
                allowed_prefix: str = "_INBOX", push: bool = False) -> str:
    """Commit `files` ({relative path: content}) onto `branch`, created from origin/main if new.
    Returns the commit sha (or the existing HEAD if nothing changed)."""
    if branch in PROTECTED or not branch.startswith("scribe/"):
        raise VaultError(f"refusing to commit to branch {branch!r}")
    with _LOCK:
        if not (repo / ".git").exists():
            raise VaultError(f"{repo} is not a git clone")
        has_origin = bool(_git(repo, "remote").split())
        if has_origin:
            _git(repo, "fetch", "--quiet", "origin")
        local = _git(repo, "branch", "--list", branch).strip()
        remote = has_origin and _git(repo, "branch", "-r", "--list", f"origin/{branch}").strip()
        if local:
            _git(repo, "checkout", "--quiet", branch)
            if remote:
                _git(repo, "merge", "--ff-only", "--quiet", f"origin/{branch}")
        elif remote:
            _git(repo, "checkout", "--quiet", "-b", branch, f"origin/{branch}")
        else:
            base = "origin/main" if has_origin else "main"
            _git(repo, "checkout", "--quiet", "-b", branch, base)
        for rel, content in files.items():
            target = _safe_target(repo, rel, allowed_prefix)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            _git(repo, "add", "--", rel)
        if not _git(repo, "diff", "--cached", "--name-only").strip():
            return _git(repo, "rev-parse", "HEAD").strip()
        _git(repo, "commit", "--quiet", "-m", message, author=author)
        sha = _git(repo, "rev-parse", "HEAD").strip()
        if push and has_origin:
            _git(repo, "push", "--quiet", "origin", f"{branch}:{branch}")
        log.info("staged %d file(s) on %s at %s (pushed=%s)", len(files), branch, sha[:8],
                 push and has_origin)
        return sha
