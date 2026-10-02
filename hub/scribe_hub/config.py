"""Hub settings, read from the environment. No secrets live here: tokens are in the database
(hashed) and the CLI logins live on their own volume."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _cidrs(name: str) -> tuple[str, ...]:
    return tuple(c.strip() for c in os.environ.get(name, "").split(",") if c.strip())


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    dm_vault: Path | None = None
    campaign_file: Path | None = None
    # When set, app- and worker-scoped requests must carry this Tailscale-User-Login header.
    # `tailscale serve` sets it; leave empty only for local development.
    tailscale_login: str = ""
    naota_llm_base: str = "http://192.168.7.59:8080"
    push_branches: bool = False
    git_author: str = "Scribe Hub <scribe-hub@atomsk.local>"
    pair_code_ttl_s: int = 600
    worker_lease_s: int = 1800
    # Source networks each listener accepts (CIDRs). Empty = accept any (local development).
    # On atomsk: main = the `azora` network (tailscale serve arrives via its gateway),
    # gpu = the `azora-iris` network. Keeps iris-bot off the main API even though both listeners
    # live in one container.
    main_allowed_cidrs: tuple[str, ...] = ()
    gpu_allowed_cidrs: tuple[str, ...] = ()
    # S4: which agent CLI drafts proposals ("codex" | "claude"; empty = S4 does not run by itself).
    s4_provider: str = ""
    # tests/dev: a custom command, with {prompt} and {dir} filled in
    s4_command: tuple[str, ...] = ()
    s4_timeout_s: int = 45 * 60
    codex_sandbox: str = "workspace-write"
    claude_token_file: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "hub.sqlite3"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"

    @classmethod
    def from_env(cls) -> Settings:
        data = Path(os.environ.get("SCRIBE_DATA_DIR", "/data"))
        vault = os.environ.get("SCRIBE_DM_VAULT")
        campaign = os.environ.get("SCRIBE_CAMPAIGN_FILE")
        return cls(
            data_dir=data,
            dm_vault=Path(vault) if vault else None,
            campaign_file=Path(campaign) if campaign else data / "campaign.yaml",
            tailscale_login=os.environ.get("SCRIBE_TAILSCALE_LOGIN", ""),
            naota_llm_base=os.environ.get("SCRIBE_NAOTA_LLM_BASE", "http://192.168.7.59:8080"),
            push_branches=_bool("SCRIBE_PUSH_BRANCHES", False),
            git_author=os.environ.get("SCRIBE_GIT_AUTHOR", "Scribe Hub <scribe-hub@atomsk.local>"),
            main_allowed_cidrs=_cidrs("SCRIBE_MAIN_ALLOWED_CIDRS"),
            gpu_allowed_cidrs=_cidrs("SCRIBE_GPU_ALLOWED_CIDRS"),
            s4_provider=os.environ.get("SCRIBE_S4_PROVIDER", ""),
            s4_command=tuple(json.loads(os.environ.get("SCRIBE_S4_COMMAND", "[]"))),
            s4_timeout_s=int(os.environ.get("SCRIBE_S4_TIMEOUT_S", str(45 * 60))),
            codex_sandbox=os.environ.get("SCRIBE_CODEX_SANDBOX", "workspace-write"),
            claude_token_file=os.environ.get("SCRIBE_CLAUDE_TOKEN_FILE", ""),
        )
