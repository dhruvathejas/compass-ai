"""Per-user private data: chats, settings and the workspace folder.

Layout (both folders are git-ignored)::

    memory/users/<uid>/chats.json       that person's conversations
    memory/users/<uid>/settings.json    that person's model and theme
    workspace/<uid>/                    that person's projects (and, later, their terminal's working directory)

Isolation rule: a path is built only from a user id that came out of a verified session (never from client input)
and is re-validated here, so there is no way to name another person's folder.
"""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

from .accounts import ID_RE
from .config import Config
from .store import JsonStore


def _migrate_chats_v1(data: dict) -> None:
    """v1 -> v2: make sure every chat has the full set of fields the app relies on."""
    for chat in data.setdefault("chats", []):
        chat.setdefault("title", "Untitled chat")
        chat.setdefault("created", 0.0)
        chat.setdefault("updated", chat["created"])
        chat.setdefault("messages", [])
        for m in chat["messages"]:
            m.setdefault("ts", chat["updated"])


class UserData:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._chats: dict[str, JsonStore] = {}
        self._settings: dict[str, JsonStore] = {}

    @staticmethod
    def _check(uid: str) -> str:
        if not isinstance(uid, str) or not ID_RE.match(uid):
            raise ValueError("bad user id")
        return uid

    def home(self, uid: str) -> Path:
        return self.cfg.home / "users" / self._check(uid)

    def workspace(self, uid: str) -> Path:
        return self.cfg.workspace / self._check(uid)

    def ensure_workspace(self, uid: str) -> Path:
        path = self.workspace(uid)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def chats(self, uid: str) -> JsonStore:
        with self._lock:
            if uid not in self._chats:
                self._chats[uid] = JsonStore(self.home(uid) / "chats.json", default=lambda: {"chats": []},
                                             version=2, migrations={1: _migrate_chats_v1})
            return self._chats[uid]

    def settings(self, uid: str) -> JsonStore:
        with self._lock:
            if uid not in self._settings:
                self._settings[uid] = JsonStore(
                    self.home(uid) / "settings.json",
                    default=lambda: {"model": self.cfg.default_model, "theme": "auto"}, version=1)
            return self._settings[uid]

    def adopt_legacy(self, uid: str) -> bool:
        """Before accounts existed, chats and settings lived directly in memory/. The owner inherits them."""
        moved = False
        for name in ("chats.json", "settings.json"):
            old, new = self.cfg.home / name, self.home(uid) / name
            if old.is_file() and not new.exists():
                new.parent.mkdir(parents=True, exist_ok=True)
                os.replace(old, new)
                moved = True
        for extra in self.cfg.home.glob("chats.json.bak-v*"):      # keep the migration backups with the data
            os.replace(extra, self.home(uid) / extra.name)
        return moved

    def delete(self, uid: str) -> None:
        """Erase everything this person has stored (used by account deletion)."""
        self._check(uid)
        with self._lock:
            self._chats.pop(uid, None)
            self._settings.pop(uid, None)
        for base, path in ((self.cfg.home / "users", self.home(uid)), (self.cfg.workspace, self.workspace(uid))):
            if path.is_symlink():
                path.unlink()
            elif path.exists() and path.resolve().is_relative_to(base.resolve()):
                shutil.rmtree(path, ignore_errors=True)
