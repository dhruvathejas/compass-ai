"""Compass AI: a user-controlled adaptive assistant."""
from __future__ import annotations

from dataclasses import dataclass

from flask import Flask, url_for

from . import auth, routes_chat, routes_settings, routes_ui
from .config import ROOT, Config
from .store import JsonStore


@dataclass
class Services:
    config: Config
    chats: JsonStore
    settings: JsonStore
    auth: auth.AuthState
    throttle: auth.LoginThrottle


def _migrate_chats_v1(data: dict) -> None:
    """v1 -> v2: make sure every chat has the full set of fields the app relies on."""
    for chat in data.setdefault("chats", []):
        chat.setdefault("title", "Untitled chat")
        chat.setdefault("created", 0.0)
        chat.setdefault("updated", chat["created"])
        chat.setdefault("messages", [])
        for m in chat["messages"]:
            m.setdefault("ts", chat["updated"])


def create_app(config: Config | None = None) -> Flask:
    cfg = config or Config.from_env()
    app = Flask(__name__, static_folder=str(ROOT / "static"), template_folder=str(ROOT / "templates"))
    app.config.update(MAX_CONTENT_LENGTH=cfg.max_request_bytes, TEMPLATES_AUTO_RELOAD=True)

    cfg.home.mkdir(parents=True, exist_ok=True)
    services = Services(
        config=cfg,
        chats=JsonStore(cfg.home / "chats.json", default=lambda: {"chats": []}, version=2,
                        migrations={1: _migrate_chats_v1}),
        settings=JsonStore(cfg.home / "settings.json",
                           default=lambda: {"model": cfg.default_model, "theme": "auto"}, version=1),
        auth=auth.AuthState(cfg.home / "auth.json"),
        throttle=auth.LoginThrottle(),
    )
    app.extensions["compass"] = services
    app.secret_key = services.auth.secret_key()

    auth.install(app)
    app.register_blueprint(routes_ui.bp)
    app.register_blueprint(routes_chat.bp)
    app.register_blueprint(routes_settings.bp)

    @app.context_processor
    def inject():
        def asset(path: str) -> str:
            try:
                version = int((ROOT / "static" / path).stat().st_mtime)
            except OSError:
                version = 0
            return url_for("static", filename=path, v=version)
        return {"theme": services.settings.read(lambda d: d["theme"]), "asset": asset}

    return app
