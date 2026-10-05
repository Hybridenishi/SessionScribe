"""Apply accepted proposals and publish them to the vault's `main` (spec §5.3).

This is the only code in the hub that writes `main`, and it runs only when the DM presses Publish.

1. Start from the newest `origin/main`, so the DM's own Obsidian edits are the base.
2. Bring the session's `_INBOX` files (transcript, plan, proposals.json) over from its branch.
3. Before applying each accepted proposal, re-read its target. If the text it was proposed against
   has changed since, stop: nothing is applied and the DM sees which cards conflict.
4. Apply, add the CHANGELOG.md / Meta/DM-Change-Log.md entries (AGENTS.md §6), commit.
5. Push to `main`. Never forced; if `main` moved during the publish, report it and stop.
"""
from __future__ import annotations

import datetime as dt
import logging
import shutil
from dataclasses import dataclass

from .. import vaults
from . import markdown as md
from .validate import safe_path

log = logging.getLogger("scribe_hub.publish")


class PublishConflict(Exception):
    def __init__(self, conflicts: list[dict]):
        super().__init__(f"{len(conflicts)} proposal(s) no longer match the vault")
        self.conflicts = conflicts


@dataclass
class Result:
    commit: str | None
    pushed: bool
    files: list[str]
    dry_run: bool


def current_before(vault, p: dict) -> str:
    target = safe_path(vault, p["target"])
    if p["op"] in ("create-note", "open-question", "move-note"):
        # a note made in Obsidian since, or a moved-away source, is a conflict, not an overwrite
        if target.exists():
            raise ValueError(f"{p['target']} already exists")
        if p["op"] == "move-note" and not safe_path(vault, p["from_path"]).exists():
            raise ValueError(f"{p['from_path']} no longer exists")
        return ""
    text = target.read_text(encoding="utf-8") if target.exists() else None
    if text is None:
        raise ValueError(f"{p['target']} no longer exists")
    if p["op"] == "set-frontmatter":
        return md.frontmatter_value(text, p["key"]) or ""
    return md.section_body(text, p["section"])


def overlapping(vault, accepted: list[dict], skip: set) -> list[dict]:
    """Accepted cards that would overwrite each other. Each is checked against the vault on its
    own, so a second `update-section` of the same (or an enclosing) section would silently undo
    the first, and two values for one frontmatter key would leave only the last. The later card
    is the conflict; the DM accepts one or merges them by editing."""
    out, spans, keys = [], {}, {}
    for p in accepted:
        if p["id"] in skip:
            continue
        clash = None
        if p["op"] == "set-frontmatter":
            clash = keys.get((p["target"], p["key"]))
            keys.setdefault((p["target"], p["key"]), p)
        elif p["op"] in ("update-section", "add-to-list"):
            text = safe_path(vault, p["target"]).read_text(encoding="utf-8")
            a, b = md.find_section(text, p["section"])
            span = (a - 1, b)                       # heading line through end of body
            for other, (oa, ob) in spans.get(p["target"], []):
                both_lists = p["op"] == other["op"] == "add-to-list"
                if not both_lists and span[0] < ob and oa < span[1]:
                    clash = other
                    break
            spans.setdefault(p["target"], []).append((p, span))
        if clash:
            out.append({"id": p["id"], "proposal_id": p["proposal_id"],
                        "reason": f"{p['target']}: overlaps {clash['proposal_id']}, which edits "
                                  "the same text; accept one of them, or merge them by editing"})
    return out


def apply_one(vault, p: dict) -> list[str]:
    """Apply one proposal to the working tree. Returns the paths it touched."""
    after = p.get("edited_after") if p.get("edited_after") is not None else p.get("after")
    target = safe_path(vault, p["target"])
    op = p["op"]
    if op in ("create-note", "open-question"):
        if target.exists():
            raise ValueError(f"{p['target']} already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(after.rstrip("\n") + "\n", encoding="utf-8")
        return [p["target"]]
    if op == "move-note":
        src = safe_path(vault, p["from_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, target)
        return [p["from_path"], p["target"]]
    text = target.read_text(encoding="utf-8")
    if op == "update-section":
        text = md.replace_section(text, p["section"], after)
    elif op == "add-to-list":
        text = md.append_to_list(text, p["section"], after)
    elif op == "set-frontmatter":
        text = md.set_frontmatter(text, p["key"], after)
    else:
        raise ValueError(f"unknown op {op}")
    target.write_text(text, encoding="utf-8")
    return [p["target"]]


def _insert_after(text: str, marker: str, block: str) -> str:
    i = text.find(marker)
    if i < 0:
        return text.rstrip("\n") + "\n\n" + block
    j = i + len(marker)
    return text[:j] + block + text[j:]


def changelog_entries(vault, session: int, accepted: list[dict], today: str) -> list[str]:
    """Public-safe line in CHANGELOG.md; full detail (secrets included) in the DM log."""
    touched = []
    n = len(accepted)
    public = (f"- **Session {session:03d} ingested ({today}):** {n} vault "
              f"change{'s' if n != 1 else ''} from session {session} published via the Azora Hub. "
              f"See Meta/DM-Change-Log.md {today}.\n")
    cl = vault / "CHANGELOG.md"
    if cl.exists():
        text = cl.read_text(encoding="utf-8")
        marker = "## [Unreleased]\n\n### Added\n\n"
        if marker in text:
            text = _insert_after(text, marker, public)
        else:
            text = _insert_after(text, "## [Unreleased]\n\n", "### Added\n\n" + public + "\n")
        cl.write_text(text, encoding="utf-8")
        touched.append("CHANGELOG.md")
    dm = vault / "Meta" / "DM-Change-Log.md"
    if dm.exists():
        lines = [f"## {today} - Session {session:03d} ingest published", "",
                 "- Actor: DM (approved in SessionScribe); applied by Scribe Hub",
                 f"- Scope: {n} accepted proposal(s) from Session {session:03d}",
                 f"- Public Changelog Summary: Session {session:03d} ingested — {n} vault changes.",
                 "- Details (DM Only):"]
        for p in accepted:
            mark = " [secret]" if p.get("secret") else ""
            lines.append(f"  - {p['proposal_id']} {p['op']} `{p['target']}`{mark}: "
                         f"{p['rationale']}")
        lines += ["- Follow-up:", "  - None recorded by the hub.", "", ""]
        text = dm.read_text(encoding="utf-8")
        first_entry = text.find("\n## 20")
        block = "\n".join(lines)
        text = (text[:first_entry + 1] + block + text[first_entry + 1:] if first_entry >= 0
                else text.rstrip("\n") + "\n\n" + block)
        dm.write_text(text, encoding="utf-8")
        touched.append("Meta/DM-Change-Log.md")
    return touched


def publish(repo, session: int, accepted: list[dict], author: str, *, push: bool,
            dry_run: bool = False, today: str | None = None) -> Result:
    """Apply `accepted` (dicts with the proposals-table fields) on top of origin/main."""
    if not accepted:
        raise ValueError("nothing accepted to publish")
    today = today or dt.date.today().isoformat()
    branch = f"scribe/session-{session:03d}"
    work = f"scribe/publish-{session:03d}"
    git = vaults._git
    with vaults._LOCK:
        has_origin = bool(git(repo, "remote").split())
        if has_origin:
            git(repo, "fetch", "--quiet", "origin")
        base = "origin/main" if has_origin else "main"
        git(repo, "checkout", "--quiet", "-B", work, base)
        try:
            inbox = [f for f in git(repo, "ls-tree", "-r", "--name-only", branch, "_INBOX").split()
                     if f.startswith(f"_INBOX/Session-{session:03d}-")]
            if inbox:
                git(repo, "checkout", branch, "--", *inbox)
            conflicts = []
            for p in accepted:
                try:
                    now = current_before(repo, p)
                except (ValueError, md.MarkdownError) as exc:
                    conflicts.append({"id": p["id"], "proposal_id": p["proposal_id"],
                                      "reason": str(exc)})
                    continue
                if now.strip() != (p.get("before") or "").strip():
                    conflicts.append({"id": p["id"], "proposal_id": p["proposal_id"],
                                      "reason": f"{p['target']} changed since this was proposed",
                                      "current": now})
            conflicts += overlapping(repo, accepted, {c["id"] for c in conflicts})
            if conflicts:
                raise PublishConflict(conflicts)
            files = list(inbox)
            for p in accepted:
                files += apply_one(repo, p)
            files += changelog_entries(repo, session, accepted, today)
            git(repo, "add", "-A", "--", *sorted(set(files)))
            msg = (f"Publish Session {session:03d}: {len(accepted)} accepted change(s) "
                   "from the Azora Hub")
            git(repo, "commit", "--quiet", "-m", msg, author=author)
            sha = git(repo, "rev-parse", "HEAD").strip()
            if dry_run:
                return Result(commit=None, pushed=False, files=sorted(set(files)), dry_run=True)
            if has_origin:
                if not push:
                    raise PermissionError("publishing needs push access (SCRIBE_PUSH_BRANCHES)")
                git(repo, "push", "--quiet", "origin", f"{work}:main")   # fast-forward only
                git(repo, "fetch", "--quiet", "origin")
                git(repo, "checkout", "--quiet", "-B", "main", "origin/main")
            else:
                git(repo, "checkout", "--quiet", "main")
                git(repo, "merge", "--quiet", "--ff-only", work)
            log.info("published session %s: %d proposal(s) at %s", session, len(accepted), sha[:8])
            return Result(commit=sha, pushed=has_origin, files=sorted(set(files)), dry_run=False)
        finally:
            # Whatever happened, leave the hub's clone on main with nothing half-applied: drop
            # untracked files a failed apply may have created (ignored files such as .scribe/ are
            # kept), return to main, and delete the publish branch.
            git(repo, "clean", "-fdq")
            if has_origin:
                git(repo, "checkout", "--quiet", "-f", "-B", "main", "origin/main")
            else:
                git(repo, "checkout", "--quiet", "-f", "main")
            git(repo, "branch", "--quiet", "-D", work)
