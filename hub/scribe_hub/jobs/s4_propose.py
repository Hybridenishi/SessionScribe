"""S4 Propose: run the vault's Session Ingestor in HUB MODE and load its proposals (spec §4, H2).

The agent runs headless (Codex CLI or Claude Code CLI, signed in by Nate inside this container) in
the hub's own Azora-DM clone, on the session's `scribe/session-NNN` branch. It may only write
`_INBOX/Session-NNN-proposals.json` and `_INBOX/Session-NNN-Downstream-Plan.md`. Anything else it
touches is reverted and listed as a violation. Its proposals are checked (proposals/validate.py)
and stored for review; failures land in the "Rejected by checks" tray with the reason.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from .. import archive, vaults
from ..db import now
from ..proposals.validate import check_batch
from . import queue

log = logging.getLogger("scribe_hub.s4")
SCRATCH = ".scribe"          # inside the clone, git-ignored via .git/info/exclude

PROMPT = """HUB MODE.

You are the Session Ingestor described in .github/agents/session-ingest.agent.md. Follow AGENTS.md
and that agent file exactly, in hub mode as defined by .github/skills/structured-proposals.md.

Session number: {n}
Transcript: _INBOX/Session-{n3}-Full-Transcript.md
Utterances file (cite these ids as evidence): {utterances}

Change no vault file. Write only these two files:
  _INBOX/Session-{n3}-proposals.json
  _INBOX/Session-{n3}-Downstream-Plan.md
Anything else you edit will be reverted and reported to the DM.
"""


class NotSignedIn(RuntimeError):
    pass


def allowed_outputs(n: int) -> set[str]:
    return {f"_INBOX/Session-{n:03d}-proposals.json", f"_INBOX/Session-{n:03d}-Downstream-Plan.md"}


def provider_command(settings, prompt: str, repo: Path, scratch: Path) -> tuple[list[str], dict]:
    env = dict(os.environ)
    p = settings.s4_provider
    if settings.s4_command:
        cmd = [part.replace("{prompt}", prompt).replace("{dir}", str(repo))
               for part in settings.s4_command]
        return cmd, env
    if p == "codex":
        if shutil.which("codex") is None:
            raise NotSignedIn("the Codex CLI is not installed in the hub image")
        status = subprocess.run(["codex", "login", "status"], capture_output=True, text=True,
                                timeout=60, check=False)
        if status.returncode != 0:
            raise NotSignedIn("Codex is not signed in. On atomsk run: "
                              "docker exec -it scribe-hub codex login --device-auth")
        return (["codex", "exec", "--skip-git-repo-check", "--ephemeral",
                 "-s", settings.codex_sandbox, "-C", str(repo),
                 "-o", str(scratch / "last-message.txt"), prompt], env)
    if p == "claude":
        if shutil.which("claude") is None:
            raise NotSignedIn("the Claude Code CLI is not installed in the hub image")
        token_file = settings.claude_token_file
        if not token_file or not Path(token_file).exists():
            raise NotSignedIn("Claude is not signed in. On atomsk run: docker exec -it scribe-hub "
                              "claude setup-token, and save the token to the file named by "
                              "SCRIBE_CLAUDE_TOKEN_FILE (mode 0600)")
        env["CLAUDE_CODE_OAUTH_TOKEN"] = Path(token_file).read_text().strip()
        return (["claude", "-p", prompt, "--permission-mode", "acceptEdits",
                 "--allowedTools", "Read,Glob,Grep,Write,Edit"], env)
    raise NotSignedIn("S4 is off: set SCRIBE_S4_PROVIDER to codex or claude")


def _git(repo, *args, **kw):
    return vaults._git(repo, *args, **kw)


def _changed_paths(repo: Path) -> list[str]:
    out = _git(repo, "status", "--porcelain", "--untracked-files=all")
    paths = []
    for line in out.splitlines():
        path = line[3:]
        if " -> " in path:
            paths.extend(path.split(" -> "))
        else:
            paths.append(path)
    return [p.strip('"') for p in paths if not p.startswith(SCRATCH + "/")]


def _revert(repo: Path, paths: list[str]) -> None:
    tracked = set(_git(repo, "ls-files", "--", *paths).split()) if paths else set()
    for p in paths:
        if p in tracked:
            _git(repo, "checkout", "--quiet", "HEAD", "--", p)
        else:
            _git(repo, "clean", "-fdq", "--", p)


def s4_propose(hub, job: dict) -> None:
    """Agent runs cost subscription usage, so a failed run is never retried automatically: the
    session shows the reason and Nate re-runs it from the app."""
    try:
        _run(hub, job)
    except Exception as exc:  # noqa: BLE001 — every failure stops here; none is retried
        queue.fail(hub.db, job["id"], f"{type(exc).__name__}: {exc}", retry=False)
        hub.db.execute("UPDATE sessions SET state = 'failed', detail = ? WHERE number = ?",
                       (f"Proposing failed: {exc}"[:500], job["session"]))
        log.warning("S4 session %s failed: %s", job["session"], exc)


def _run(hub, job: dict) -> None:
    n = job["session"]
    s = hub.settings
    if not s.dm_vault:
        queue.block(hub.db, job["id"], "no vault clone configured")
        return
    repo: Path = s.dm_vault
    sdir = archive.session_dir(s.archive_dir, n)
    utterances = archive.read_ndjson(sdir / "utterances.ndjson")
    branch = f"scribe/session-{n:03d}"
    with vaults._LOCK:
        _git(repo, "checkout", "--quiet", branch)
        exclude = repo / ".git" / "info" / "exclude"
        if f"/{SCRATCH}/" not in exclude.read_text(encoding="utf-8"):
            with open(exclude, "a", encoding="utf-8") as fh:
                fh.write(f"\n/{SCRATCH}/\n")
        scratch = repo / SCRATCH
        scratch.mkdir(exist_ok=True)
        utt_file = scratch / f"Session-{n:03d}-utterances.ndjson"
        shutil.copyfile(sdir / "utterances.ndjson", utt_file)
        for f in allowed_outputs(n):          # a re-run starts clean
            (repo / f).unlink(missing_ok=True)

        prompt = PROMPT.format(n=n, n3=f"{n:03d}", utterances=f"{SCRATCH}/{utt_file.name}")
        try:
            cmd, env = provider_command(s, prompt, repo, scratch)
        except NotSignedIn as exc:
            queue.block(hub.db, job["id"], str(exc))
            hub.db.execute("UPDATE sessions SET state = 'blocked', detail = ? WHERE number = ?",
                           (str(exc), n))
            return
        hub.db.execute("UPDATE sessions SET state = 'proposing', detail = NULL WHERE number = ?",
                       (n,))
        log.info("S4 session %s: running %s", n, s.s4_provider or "custom command")
        proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True,
                              timeout=s.s4_timeout_s, check=False)
        (sdir / "s4-agent.log").write_text(
            f"exit {proc.returncode}\n--- stdout ---\n{proc.stdout[-20000:]}\n"
            f"--- stderr ---\n{proc.stderr[-20000:]}\n", encoding="utf-8")

        changed = _changed_paths(repo)
        allowed = allowed_outputs(n)
        violations = sorted(p for p in changed if p not in allowed)
        if violations:
            log.warning("S4 session %s: agent touched %d file(s) it must not; reverting",
                        n, len(violations))
            _revert(repo, violations)
        if proc.returncode != 0:
            raise RuntimeError(f"agent exited {proc.returncode}; see s4-agent.log")
        pfile = repo / f"_INBOX/Session-{n:03d}-proposals.json"
        if not pfile.exists():
            raise RuntimeError("the agent wrote no proposals file")
        checked = check_batch(pfile.read_text(encoding="utf-8"), n, repo, utterances)

        present = [f for f in sorted(allowed) if (repo / f).exists()]
        _git(repo, "add", "--", *present)
        if _git(repo, "diff", "--cached", "--name-only").strip():
            msg = f"Propose Session {n:03d} vault changes (hub mode)"
            _git(repo, "commit", "--quiet", "-m", msg, author=s.git_author)

    store_batch(hub, n, s.s4_provider or "custom", checked, violations)
    queue.finish(hub.db, job["id"], {"proposals": len(checked),
                                     "rejected_by_checks": sum(not c.ok for c in checked),
                                     "violations": violations})


def store_batch(hub, n: int, provider: str, checked, violations: list[str]) -> int:
    t = now()
    with hub.db.transaction():
        # an older, unpublished batch for this session is superseded
        hub.db.execute(
            "UPDATE proposals SET state = 'superseded', updated_at = ? WHERE state IN "
            "('pending','accepted','deferred','rejected_by_checks') AND batch IN "
            "(SELECT id FROM proposal_batches WHERE session = ? AND published_at IS NULL)", (t, n))
        cur = hub.db.execute(
            "INSERT INTO proposal_batches (session, vault, provider, created_at, violations) "
            "VALUES (?, 'dm', ?, ?, ?)", (n, provider, t, json.dumps(violations)))
        batch = cur.lastrowid
        for c in checked:
            hub.db.execute(
                "INSERT INTO proposals (batch, proposal_id, op, target, section, key, "
                "from_path, after, before, entity, secret, rationale, evidence, conflicts, "
                "state, check_error, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (batch, c.proposal_id, c.op, c.target, c.section, c.key, c.from_path, c.after,
                 c.before, c.entity or "?", int(c.secret), c.rationale, json.dumps(c.evidence),
                 json.dumps(c.conflicts), "pending" if c.ok else "rejected_by_checks", c.error, t))
        hub.db.execute("UPDATE sessions SET state = 'review', detail = ? WHERE number = ?",
                       (f"{sum(c.ok for c in checked)} proposal(s) to review"
                        + (f", {len(violations)} agent edit(s) reverted" if violations else ""), n))
    return batch

