"""campaign.yaml: who each Discord track is, and the names Whisper should expect."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Speaker:
    label: str            # "Player/Character", as the vault's transcripts use
    role: str             # dm | player
    pc: str | None = None
    discord_id: str | None = None
    usernames: tuple[str, ...] = ()


@dataclass(frozen=True)
class Campaign:
    speakers: tuple[Speaker, ...]
    vocabulary_prompt: str = ""

    def resolve(self, username: str | None, discord_id: str | None) -> Speaker | None:
        """Discord user id wins (it never changes); the username is the fallback."""
        if discord_id:
            for s in self.speakers:
                if s.discord_id and s.discord_id == discord_id:
                    return s
        if username:
            u = username.lower()
            for s in self.speakers:
                if u in (n.lower() for n in s.usernames):
                    return s
        return None


class CampaignError(ValueError):
    pass


def parse(data: dict) -> Campaign:
    speakers = []
    for i, raw in enumerate(data.get("speakers") or []):
        label = str(raw.get("label") or "").strip()
        role = str(raw.get("role") or "player").strip()
        if not label:
            raise CampaignError(f"speaker #{i + 1} has no label")
        if role not in {"dm", "player"}:
            raise CampaignError(f"speaker {label!r}: role must be dm or player")
        names = raw.get("usernames") or []
        if isinstance(names, str):
            names = [names]
        did = raw.get("discord_id")
        speakers.append(Speaker(label=label, role=role, pc=raw.get("pc") or None,
                                discord_id=str(did) if did else None,
                                usernames=tuple(str(n) for n in names)))
    if not speakers:
        raise CampaignError("campaign has no speakers")
    return Campaign(speakers=tuple(speakers),
                    vocabulary_prompt=str(data.get("vocabulary_prompt") or "").strip())


def load(path: Path) -> Campaign:
    if not path.exists():
        raise CampaignError(f"{path} does not exist")
    return parse(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def to_dict(c: Campaign) -> dict:
    return {
        "speakers": [
            {k: v for k, v in {"label": s.label, "role": s.role, "pc": s.pc,
                               "discord_id": s.discord_id, "usernames": list(s.usernames)}.items()
             if v}
            for s in c.speakers],
        "vocabulary_prompt": c.vocabulary_prompt,
    }


def save(path: Path, c: Campaign) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(to_dict(c), sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    tmp.replace(path)
