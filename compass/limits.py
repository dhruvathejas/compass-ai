"""Server mode: the free tier. Fair use limits so one shared Ollama machine can serve many people.

Everything here is a no-op in personal mode (``Policy.enabled`` is False), so a private host is unchanged.

* **Daily messages** and a **per-minute** limit per member, a cap on **stored chats**, and a cap on **accounts**.
  The owner of the host is never limited (they pay for the machine). The numbers live in ``memory/server.json``
  and the owner edits them in the app; the starting values come from the environment.
* **Capacity**: at most ``max_concurrent`` replies are generated at once, and a member can have only one reply
  in progress, so nobody can hold every slot. A request that cannot get a slot in ``busy_wait`` seconds is told the
  server is busy (and is not counted against their daily messages).
* A message that never got an answer (model down, server busy, chat deleted) is refunded.
"""
from __future__ import annotations

import datetime as dt
import threading
import time

from . import security
from .config import Config
from .store import JsonStore

LIMIT_FIELDS = {          # name -> (label, largest value the owner may set)
    "daily_messages": ("Messages per person per day", 100_000),
    "per_minute": ("Messages per person per minute", 600),
    "max_chats": ("Saved chats per person", 10_000),
    "max_users": ("Accounts on this server", 1_000_000),
}
DAYS_KEPT = 14


class PolicyError(Exception):
    """A limit was hit. ``str(err)`` is safe to show; ``retry_after`` is in seconds (0 = not a time-based limit)."""

    def __init__(self, message: str, status: int = 429, retry_after: int = 0, kind: str = "limit"):
        super().__init__(message)
        self.status, self.retry_after, self.kind = status, retry_after, kind


class ServerSettings:
    """The owner-editable limits. 0 means "no limit"."""

    def __init__(self, cfg: Config):
        self._store = JsonStore(cfg.home / "server.json", version=1, default=lambda: {
            "daily_messages": cfg.free_daily_messages, "per_minute": cfg.free_per_minute,
            "max_chats": cfg.free_max_chats, "max_users": cfg.max_users})

    def get(self) -> dict:
        return self._store.read(lambda d: {k: int(d.get(k, 0)) for k in LIMIT_FIELDS})

    def update(self, changes: dict) -> dict:
        clean = {}
        for key, value in changes.items():
            if key not in LIMIT_FIELDS:
                raise ValueError(f"Unknown limit: {key}")
            label, top = LIMIT_FIELDS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= top:
                raise ValueError(f"{label}: use a whole number from 0 (no limit) to {top:,}.")
            clean[key] = value
        with self._store.transaction() as d:
            d.update(clean)
        return self.get()


class Usage:
    """How many messages each person has sent today (UTC days, the last two weeks are kept)."""

    def __init__(self, cfg: Config, clock=time.time):
        self._store = JsonStore(cfg.home / "usage.json", default=lambda: {"days": {}}, version=1)
        self.clock = clock

    def _day(self) -> str:
        return dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc).strftime("%Y-%m-%d")

    def resets_at(self) -> float:
        now = dt.datetime.fromtimestamp(self.clock(), dt.timezone.utc)
        return (now.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)).timestamp()

    def used(self, uid: str) -> int:
        return self._store.read(lambda d: int(d["days"].get(self._day(), {}).get(uid, 0)))

    def today(self) -> dict:
        return self._store.read(lambda d: dict(d["days"].get(self._day(), {})))

    def consume(self, uid: str, limit: int) -> bool:
        """Count one message unless ``limit`` (0 = unlimited) is already reached. Returns whether it was counted."""
        with self._store.transaction() as d:
            days = d["days"]
            today = days.setdefault(self._day(), {})
            for old in sorted(days)[:-DAYS_KEPT]:
                del days[old]
            if limit and today.get(uid, 0) >= limit:
                return False
            today[uid] = today.get(uid, 0) + 1
            return True

    def refund(self, uid: str) -> None:
        with self._store.transaction() as d:
            today = d["days"].get(self._day(), {})
            if today.get(uid, 0) > 0:
                today[uid] -= 1

    def forget(self, uid: str) -> None:
        with self._store.transaction() as d:
            for day in d["days"].values():
                day.pop(uid, None)


class Ticket:
    """One admitted message. Call ``refund()`` if it got no answer; always call ``release()`` when done."""

    def __init__(self, policy: "Policy | None" = None, uid: str = "", counted: bool = False, slot: bool = False):
        self._policy, self._uid, self._counted, self._slot = policy, uid, counted, slot

    def refund(self) -> None:
        if self._counted and self._policy:
            self._policy.usage.refund(self._uid)
            self._counted = False

    def release(self) -> None:
        if self._slot and self._policy:
            self._policy._free_slot(self._uid)
            self._slot = False


class Policy:
    def __init__(self, cfg: Config, *, clock=time.time, monotonic=time.monotonic):
        self.cfg = cfg
        self.enabled = cfg.server
        self.settings = ServerSettings(cfg)
        self.usage = Usage(cfg, clock=clock)
        self.minute = security.RateLimiter(limit=1, window=60, clock=monotonic)
        self._slots = threading.BoundedSemaphore(cfg.max_concurrent)
        self._active: set[str] = set()
        self._lock = threading.Lock()

    # ------------------------------------------------------------ who is limited

    def limited(self, user: dict) -> bool:
        return self.enabled and user["role"] != "owner"

    # ------------------------------------------------------------ models

    def model_ok(self, name: str) -> bool:
        if not self.enabled or not self.cfg.models:
            return True
        return name in self.cfg.models

    def pick_model(self, wanted: str) -> str:
        """The model to really use: the person's choice if allowed, else the host's default."""
        if self.model_ok(wanted):
            return wanted
        return self.cfg.default_model if self.model_ok(self.cfg.default_model) else self.cfg.models[0]

    # ------------------------------------------------------------ admitting a message

    def admit(self, user: dict) -> Ticket:
        """Raise ``PolicyError`` when this message must wait or be refused; otherwise return a ticket."""
        if not self.enabled:
            return Ticket()
        uid = user["id"]
        limits = self.settings.get()
        counted = False
        if self.limited(user):
            wait = self.minute.hit(uid, limit=limits["per_minute"]) if limits["per_minute"] else 0
            if wait:
                raise PolicyError(f"You're sending messages quickly. Try again in {wait} seconds.", 429, wait, "rate")
            if not self.usage.consume(uid, limits["daily_messages"]):
                raise PolicyError(
                    f"You've used all {limits['daily_messages']} free messages for today. "
                    "They come back at midnight UTC.", 429, max(1, int(self.usage.resets_at() - self.usage.clock())),
                    "daily")
            counted = True
        with self._lock:
            if uid in self._active and self.limited(user):
                if counted:
                    self.usage.refund(uid)
                raise PolicyError("Compass AI is still answering your last message. Wait for it to finish.",
                                  429, 0, "busy-self")
            self._active.add(uid)
        if not self._slots.acquire(timeout=self.cfg.busy_wait):
            with self._lock:
                self._active.discard(uid)
            if counted:
                self.usage.refund(uid)
            raise PolicyError("Compass AI is busy right now. Please try again in a minute.", 503, 30, "capacity")
        return Ticket(self, uid, counted, slot=True)

    def _free_slot(self, uid: str) -> None:
        with self._lock:
            self._active.discard(uid)
        self._slots.release()

    # ------------------------------------------------------------ chats and accounts

    def check_new_chat(self, user: dict, current: int) -> None:
        if not self.limited(user):
            return
        top = self.settings.get()["max_chats"]
        if top and current >= top:
            raise PolicyError(f"You've reached the limit of {top} saved chats. Delete one to start another.", 403,
                              0, "chats")

    def accounts_full(self, count: int) -> bool:
        top = self.settings.get()["max_users"]
        return self.enabled and bool(top) and count >= top

    # ------------------------------------------------------------ for the UI

    def usage_for(self, user: dict) -> dict:
        if not self.limited(user):
            return {"limited": False}
        limits = self.settings.get()
        used = self.usage.used(user["id"])
        top = limits["daily_messages"]
        return {"limited": True, "daily_limit": top, "used": used,
                "remaining": max(0, top - used) if top else None, "resets": self.usage.resets_at()}
