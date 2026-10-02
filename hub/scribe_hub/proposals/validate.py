"""Check an agent's proposals.json against the vault and the session transcript (spec §5.1).

Every proposal comes back either valid (with `before` read from the file, never from the model) or
rejected with a plain reason, so it can sit in the app's "Rejected by checks" tray instead of being
dropped silently.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import markdown as md

OPS = {"create-note", "update-section", "add-to-list", "set-frontmatter", "open-question",
       "move-note"}
NEEDS_AFTER = OPS - {"move-note"}
DM_ONLY = re.compile(r">\s*\[!warning\]-?\s*DM Only", re.I)
FORBIDDEN_ROOTS = {".git", ".github", ".obsidian", ".claude", ".scribe"}


class BatchError(ValueError):
    """The file as a whole is unusable (not JSON, wrong schema, wrong session)."""


@dataclass
class Checked:
    proposal_id: str
    op: str
    target: str
    entity: str
    section: str | None = None
    key: str | None = None
    from_path: str | None = None
    after: str | None = None
    before: str | None = None
    secret: bool = False
    rationale: str = ""
    evidence: list[dict] = field(default_factory=list)
    conflicts: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def safe_path(vault: Path, rel: str) -> Path:
    """Resolve a vault-relative .md path, refusing anything that escapes or touches tooling."""
    if not rel or rel.startswith("/") or "\\" in rel:
        raise ValueError(f"not a vault-relative path: {rel!r}")
    p = PurePosixPath(rel)
    if ".." in p.parts or p.parts[0] in FORBIDDEN_ROOTS:
        raise ValueError(f"path not allowed: {rel!r}")
    if p.suffix != ".md":
        raise ValueError(f"only .md notes can be changed: {rel!r}")
    full = (vault / rel).resolve()
    if vault.resolve() not in full.parents:
        raise ValueError(f"path escapes the vault: {rel!r}")
    return full


def _resolve_evidence(raw, utterances: dict[str, dict]) -> list[dict]:
    if not isinstance(raw, list):
        raise ValueError("evidence must be a list")
    out = []
    for e in raw:
        uid = e.get("utterance_id") if isinstance(e, dict) else None
        if not isinstance(uid, str) or uid not in utterances:
            raise ValueError(f"evidence {uid!r} is not an utterance in this session")
        u = utterances[uid]
        # the quote shown to the DM is the hub's copy of the utterance, not the model's wording
        out.append({"utterance_id": uid, "track": u["track"], "speaker": u["speaker"],
                    "start_ms": u["start_ms"], "end_ms": u["end_ms"], "text": u["text"]})
    return out


def check_one(raw: dict, vault: Path, utterances: dict[str, dict]) -> Checked:
    c = Checked(proposal_id=str(raw.get("proposal_id") or "?"), op=str(raw.get("op") or "?"),
                target=str(raw.get("target") or ""), entity=str(raw.get("entity") or "").strip(),
                section=raw.get("section"), key=raw.get("key"), from_path=raw.get("from"),
                after=raw.get("after"), secret=bool(raw.get("secret", False)),
                rationale=str(raw.get("rationale") or "").strip(),
                conflicts=raw.get("conflicts_with") or [])
    try:
        if c.op not in OPS:
            raise ValueError(f"unknown op {c.op!r}")
        if not c.entity:
            raise ValueError("missing entity")
        if not c.rationale:
            raise ValueError("missing rationale")
        if c.op in NEEDS_AFTER and not (isinstance(c.after, str) and c.after.strip()):
            raise ValueError("missing after")
        if not isinstance(c.conflicts, list):
            raise ValueError("conflicts_with must be a list")
        c.evidence = _resolve_evidence(raw.get("evidence") or [], utterances)
        target = safe_path(vault, c.target)

        if c.op in ("create-note", "open-question"):
            if target.exists():
                raise ValueError(f"{c.target} already exists")
            if c.op == "open-question" and not re.fullmatch(
                    r"Meta/Open-Question-Tickets/OQ-\d{4}-\d{2}-\d{2}-[A-Za-z0-9-]+\.md", c.target):
                raise ValueError("open-question target must be "
                                 "Meta/Open-Question-Tickets/OQ-YYYY-MM-DD-Label.md")
            if not md.frontmatter_ok(c.after):
                raise ValueError("a new note needs valid YAML frontmatter")
            c.before = ""
        elif c.op == "move-note":
            if not c.from_path:
                raise ValueError("move-note needs from")
            src = safe_path(vault, c.from_path)
            if not src.exists():
                raise ValueError(f"{c.from_path} does not exist")
            if target.exists():
                raise ValueError(f"{c.target} already exists")
            c.before = ""
        else:
            if not target.exists():
                raise ValueError(f"{c.target} does not exist")
            text = target.read_text(encoding="utf-8")
            if c.op == "set-frontmatter":
                if not c.key:
                    raise ValueError("set-frontmatter needs key")
                md.set_frontmatter(text, c.key, c.after)      # proves it would apply
                c.before = md.frontmatter_value(text, c.key) or ""
            else:
                if not c.section:
                    raise ValueError(f"{c.op} needs section")
                c.before = md.section_body(text, c.section)
                if c.op == "add-to-list":
                    md.append_to_list(text, c.section, c.after)
        if c.secret and not DM_ONLY.search(c.after or ""):
            raise ValueError("secret proposal without a '> [!warning]- DM Only' callout")
    except (ValueError, md.MarkdownError) as exc:
        c.error = str(exc)
    return c


def check_batch(raw_json: str, session: int, vault: Path, utterances: list[dict]) -> list[Checked]:
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise BatchError(f"proposals file is not valid JSON: {exc}") from None
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise BatchError("proposals file must be an object with schema 1")
    if data.get("session") != session:
        raise BatchError(f"proposals file is for session {data.get('session')}, not {session}")
    items = data.get("proposals")
    if not isinstance(items, list):
        raise BatchError("proposals must be a list")
    by_id = {u["id"]: u for u in utterances}
    seen: set[str] = set()
    out = []
    for raw in items:
        if not isinstance(raw, dict):
            out.append(Checked(proposal_id="?", op="?", target="", entity="?",
                               error="proposal is not an object"))
            continue
        c = check_one(raw, vault, by_id)
        if c.proposal_id in seen:
            c.error = c.error or f"duplicate proposal_id {c.proposal_id}"
            c.proposal_id = f"{c.proposal_id}#{len(out)}"
        seen.add(c.proposal_id)
        out.append(c)
    return out
