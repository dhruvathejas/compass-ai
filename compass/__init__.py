"""Compass AI: a user-controlled adaptive assistant."""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from flask import Flask, g, url_for

from . import (auth, routes_account, routes_chat, routes_onboarding, routes_server, routes_settings, routes_ui,
               routes_workspace, security)
from .accounts import Accounts
from .config import ROOT, Config
from .limits import Policy
from .store import JsonStore
from .userdata import UserData


@dataclass
class Services:
    config: Config
    accounts: Accounts
    userdata: UserData
    throttle: security.LoginThrottle
    signups: security.RateLimiter
    signups_all: security.RateLimiter        # every sign-up on the server (server mode), whatever the address
    policy: Policy


def _load_secret(cfg: Config) -> str:
    """The key that signs session cookies. Kept in memory/auth.json (git-ignored, never sent to the browser)."""
    store = JsonStore(cfg.home / "auth.json", default=lambda: {"secret": secrets.token_hex(32)}, version=1)
    with store.transaction() as d:
        d.setdefault("secret", secrets.token_hex(32))
        d.pop("password", None)          # the old single shared password: accounts replaced it
        d.pop("epoch", None)
        return d["secret"]


def create_app(config: Config | None = None) -> Flask:
    cfg = config or Config.from_env()
    app = Flask(__name__, static_folder=str(ROOT / "static"), template_folder=str(ROOT / "templates"))
    app.config.update(MAX_CONTENT_LENGTH=cfg.max_request_bytes, TEMPLATES_AUTO_RELOAD=True)

    cfg.home.mkdir(parents=True, exist_ok=True)
    services = Services(
        config=cfg,
        accounts=Accounts(cfg.home / "accounts.json", idle_seconds=cfg.session_idle_seconds,
                          max_seconds=cfg.session_max_seconds,
                          default_registration="open" if cfg.server else "invite"),
        userdata=UserData(cfg),
        throttle=security.LoginThrottle(global_limit=500 if cfg.server else None),
        signups=security.RateLimiter(limit=5, window=3600),
        signups_all=security.RateLimiter(limit=200, window=3600),
        policy=Policy(cfg),
    )
    app.extensions["compass"] = services
    app.secret_key = _load_secret(cfg)

    auth.install(app)
    for module in (routes_onboarding, routes_ui, routes_account, routes_chat, routes_settings, routes_workspace,
                   routes_server):
        app.register_blueprint(module.bp)

    @app.context_processor
    def inject():
        def asset(path: str) -> str:
            try:
                version = int((ROOT / "static" / path).stat().st_mtime)
            except OSError:
                version = 0
            return url_for("static", filename=path, v=version)

        user = g.get("user")
        theme = services.userdata.settings(user["id"]).read(lambda d: d["theme"]) if user else "auto"
        return {"theme": theme, "asset": asset, "user": user, "server_mode": cfg.server}

    return app
