"""Runtime configuration: where data lives, which Ollama to talk to, session limits."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    home: Path                      # private data: chats, settings, auth (git-ignored)
    workspace: Path                 # user projects (git-ignored)
    ollama_host: str
    default_model: str
    max_message_chars: int = 20_000
    max_request_bytes: int = 2 * 1024 * 1024
    session_idle_seconds: int = 12 * 3600
    session_max_seconds: int = 7 * 24 * 3600
    allowed_hosts: frozenset = field(default_factory=frozenset)  # extra Host names accepted locally

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        extra = os.environ.get("COMPASS_ALLOWED_HOSTS", "")
        cfg = cls(
            home=Path(os.environ.get("COMPASS_HOME", ROOT / "memory")),
            workspace=Path(os.environ.get("COMPASS_WORKSPACE", ROOT / "workspace")),
            ollama_host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
            default_model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
            allowed_hosts=frozenset(h.strip().lower() for h in extra.split(",") if h.strip()),
        )
        return replace(cfg, **overrides) if overrides else cfg
