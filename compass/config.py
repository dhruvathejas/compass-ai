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
    allowed_hosts: frozenset = field(default_factory=frozenset)  # extra Host names accepted on direct connections
    repo_url: str = "https://github.com/dhruvathejas/compass-ai.git"   # shown on the Install page
    # --- server mode: a shared public host where people sign up and use it for free ---------------------------
    mode: str = "personal"                    # "personal" (your own PC) or "server" (shared host, free tier)
    public_url: str = ""                      # e.g. https://chat.example.com (cookies become Secure when https)
    proxy_header: str = ""                    # header carrying the real visitor IP, ONLY when a proxy you control sets it
    models: tuple = ()                        # allow-list of model names people may pick (empty = all installed)
    reply_timeout: int = 300                  # seconds to wait for one model reply
    max_concurrent: int = 2                   # replies generated at the same time (server mode)
    busy_wait: int = 20                       # seconds a request waits for a free slot before "busy"
    free_daily_messages: int = 50             # starting free-tier limits; the owner can change them in the app
    free_per_minute: int = 6
    free_max_chats: int = 100
    max_users: int = 1000
    max_chat_messages: int = 200              # messages kept in one chat before "start a new chat"
    context_messages: int = 40                # server mode: only the latest messages go to the model
    context_chars: int = 24_000

    @property
    def server(self) -> bool:
        return self.mode == "server"

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        extra = os.environ.get("COMPASS_ALLOWED_HOSTS", "")
        server = os.environ.get("COMPASS_MODE", "personal").strip().lower() == "server"
        public_url = os.environ.get("COMPASS_PUBLIC_URL", "").strip().rstrip("/")
        hosts = {h.strip().lower() for h in extra.split(",") if h.strip()}
        if public_url:
            from urllib.parse import urlsplit
            hosts.add((urlsplit(public_url).hostname or "").lower())

        def num(name: str, default: int) -> int:
            try:
                return max(0, int(os.environ.get(name, default)))
            except ValueError:
                return default

        cfg = cls(
            home=Path(os.environ.get("COMPASS_HOME", ROOT / "memory")),
            workspace=Path(os.environ.get("COMPASS_WORKSPACE", ROOT / "workspace")),
            ollama_host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/"),
            default_model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
            allowed_hosts=frozenset(hosts - {""}),
            repo_url=os.environ.get("COMPASS_REPO_URL", cls.repo_url),
            mode="server" if server else "personal",
            public_url=public_url,
            proxy_header=os.environ.get("COMPASS_PROXY_HEADER", "").strip(),
            models=tuple(m.strip() for m in os.environ.get("COMPASS_MODELS", "").split(",") if m.strip()),
            reply_timeout=num("COMPASS_REPLY_TIMEOUT", 180 if server else 300) or 300,
            max_concurrent=num("COMPASS_MAX_CONCURRENT", 2) or 2,
            free_daily_messages=num("COMPASS_FREE_DAILY", 50),
            free_per_minute=num("COMPASS_FREE_PER_MINUTE", 6),
            free_max_chats=num("COMPASS_FREE_CHATS", 100),
            max_users=num("COMPASS_MAX_USERS", 1000),
            max_message_chars=num("COMPASS_MAX_MESSAGE_CHARS", 8_000 if server else 20_000) or 20_000,
        )
        return replace(cfg, **overrides) if overrides else cfg
