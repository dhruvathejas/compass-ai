"""Accounts, devices, sessions and invites: the one place that knows who is who.

Identity and devices are separate things:

* An **account** is a person: username, password hash, profile, role (``owner`` or ``member``).
* A **device** is a browser that the person authorised. Its id is public and random; what proves it is a secret
  *device credential* kept in an HttpOnly cookie. Only the SHA-256 of that credential is stored, so a copy of
  ``accounts.json`` cannot be used to impersonate a device. A device credential alone never signs anyone in: a
  password is always needed, and the device only says "this browser is one this person has approved".
* A **session** is one signed-in browser tab-set. The cookie holds a random id; the record lives here, so a session
  can be ended individually, per device, or for the whole account, and ending it takes effect immediately.

Nothing in here trusts the client: callers pass the user id they got from a verified session, and every lookup of a
device, session or invite is scoped to that user id.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import time

from .store import JsonStore

MIN_PASSWORD = 10
MAX_PASSWORD = 1024
SCRYPT_PARAMS = {"n": 2 ** 15, "r": 8, "p": 1}
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{2,31}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s.]+(\.[^@\s.]+)+$")
ID_RE = re.compile(r"^[0-9a-f]{16}$")           # user, device, session and invite ids
DEVICE_TYPES = ("desktop", "laptop", "tablet", "phone", "other")
REGISTRATION_MODES = ("invite", "open", "closed")
MAX_DEVICES = 25
TOUCH_EVERY = 60            # seconds: last-active times are written at most this often
ONLINE_WINDOW = 5 * 60      # a device counts as "connected now" if it was active this recently
INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"      # no 0/O/1/I/L lookalikes
INVITE_DAYS = 7


class AccountError(Exception):
    """A problem the person can fix. ``str(err)`` is safe to show."""

    def __init__(self, message: str, status: int = 400, field: str | None = None):
        super().__init__(message)
        self.status, self.field = status, field


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _new_id() -> str:
    return secrets.token_hex(8)


# ----------------------------------------------------------------------------- validation

def clean_username(value) -> str:
    name = value.strip().lower() if isinstance(value, str) else ""
    if not USERNAME_RE.match(name):
        raise AccountError("Usernames are 3-32 characters: lowercase letters, digits, dot, dash or underscore, "
                           "starting with a letter.", field="username")
    return name


def clean_display_name(value, fallback: str = "") -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    text = text or fallback
    if not text or len(text) > 60 or any(ord(c) < 32 for c in text):
        raise AccountError("Display name must be 1-60 characters.", field="display_name")
    return text


def clean_email(value) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AccountError("That email address doesn't look right.", field="email")
    text = value.strip()
    if text and (len(text) > 254 or not EMAIL_RE.match(text)):
        raise AccountError("That email address doesn't look right.", field="email")
    return text


def check_password(password, username: str = "") -> str:
    if not isinstance(password, str):
        raise AccountError("Enter a password.", field="password")
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"Use at least {MIN_PASSWORD} characters.", field="password")
    if len(password) > MAX_PASSWORD:
        raise AccountError("That password is too long.", field="password")
    if username and password.lower() == username.lower():
        raise AccountError("Your password can't be your username.", field="password")
    return password


def clean_device_name(value) -> str:
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if not text or len(text) > 40 or any(ord(c) < 32 for c in text):
        raise AccountError("Give the device a name of 1-40 characters.", field="name")
    return text


def clean_device_type(value) -> str:
    if value not in DEVICE_TYPES:
        raise AccountError("Choose a device type from the list.", field="type")
    return value


def describe_agent(ua: str) -> str:
    """A short, human description such as 'Edge on Windows' (never the raw user-agent)."""
    ua = ua or ""
    browser = next((n for p, n in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
                                   ("Chrome/", "Chrome"), ("Safari/", "Safari")) if p in ua), "Browser")
    system = next((n for p, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                  ("Windows", "Windows"), ("Mac OS X", "macOS"), ("CrOS", "ChromeOS"),
                                  ("Linux", "Linux")) if p in ua), "")
    return f"{browser} on {system}" if system else browser


def guess_device_type(ua: str) -> str:
    ua = ua or ""
    if "iPad" in ua or ("Android" in ua and "Mobile" not in ua):
        return "tablet"
    if "iPhone" in ua or "Mobile" in ua:
        return "phone"
    return "desktop"


def code_hash(code: str) -> str:
    return _sha("".join(ch for ch in str(code).upper() if ch.isalnum()))


# ----------------------------------------------------------------------------- the store

def _default(registration: str = "invite") -> dict:
    return {"registration": registration, "users": {}, "devices": {}, "sessions": {}, "invites": {}}


class Accounts:
    def __init__(self, path, *, idle_seconds: int, max_seconds: int, clock=time.time,
                 default_registration: str = "invite"):
        # a brand-new file starts in `default_registration` ("open" on a public server); an existing file keeps its own
        self._store = JsonStore(path, default=lambda: _default(default_registration), version=1)
        self.idle_seconds, self.max_seconds, self.clock = idle_seconds, max_seconds, clock
        with self._store.transaction():
            pass                                   # create the file on first run
        try:
            os.chmod(path, 0o600)                  # best effort; a no-op on Windows
        except OSError:
            pass
        # Scrypt cost is paid for unknown usernames too, so timing doesn't reveal which usernames exist.
        self._dummy = self._hash("dummy password for equal timing")

    # ------------------------------------------------------------ passwords

    @staticmethod
    def _derive(password: str, salt: bytes, params: dict) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=128 * 1024 * 1024, **params)

    def _hash(self, password: str) -> dict:
        salt = os.urandom(16)
        digest = self._derive(password, salt, SCRYPT_PARAMS)
        return {"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode(), **SCRYPT_PARAMS}

    def _check(self, password: str, rec: dict) -> bool:
        params = {k: rec[k] for k in ("n", "r", "p")}
        got = self._derive(password[:MAX_PASSWORD], base64.b64decode(rec["salt"]), params)
        return hmac.compare_digest(got, base64.b64decode(rec["hash"]))

    # ------------------------------------------------------------ users

    @staticmethod
    def public_user(u: dict) -> dict:
        return {**{k: u[k] for k in ("id", "username", "display_name", "email", "role", "created")},
                "suspended": bool(u.get("suspended"))}

    def has_users(self) -> bool:
        return self._store.read(lambda d: bool(d["users"]))

    def get_user(self, uid: str):
        return self._store.read(lambda d: self.public_user(d["users"][uid]) if uid in d["users"] else None)

    def find_user(self, username: str):
        name = username.strip().lower() if isinstance(username, str) else ""
        return self._store.read(lambda d: next(
            (self.public_user(u) for u in d["users"].values() if u["username"] == name), None))

    def owner_id(self):
        return self._store.read(lambda d: next((i for i, u in d["users"].items() if u["role"] == "owner"), None))

    def list_users(self) -> list[dict]:
        def build(d):
            out = []
            for u in sorted(d["users"].values(), key=lambda u: u["created"]):
                devices = [x for x in d["devices"].values() if x["uid"] == u["id"]]
                out.append({**self.public_user(u), "devices": len(devices),
                            "last_active": max((x["last_active"] for x in devices), default=None)})
            return out
        return self._store.read(build)

    def _gate(self, d: dict, invite_code: str, is_local: bool, consume: bool) -> None:
        if not d["users"]:
            if not is_local:
                raise AccountError("The first account can only be created on the computer that runs Compass AI.", 403)
            return
        mode = d["registration"]
        if mode == "closed":
            raise AccountError("Registration is closed on this host. Ask its owner to add you.", 403)
        if mode == "invite":
            wanted = code_hash(invite_code or "")
            for iid, inv in d["invites"].items():
                if hmac.compare_digest(inv["hash"], wanted) and inv["expires"] > self.clock():
                    if consume:
                        del d["invites"][iid]          # single use
                    return
            raise AccountError("That invite code isn't valid, or it has expired.", 403, "invite")

    def registration_gate(self, invite_code: str = "", is_local: bool = False) -> None:
        self._store.read(lambda d: self._gate(d, invite_code, is_local, consume=False))

    def registration_state(self) -> dict:
        return self._store.read(lambda d: {"first": not d["users"], "mode": d["registration"]})

    def register(self, username, display_name, email, password, *, invite_code: str = "",
                 is_local: bool = False) -> dict:
        username = clean_username(username)
        display = clean_display_name(display_name, fallback=username)
        email = clean_email(email)
        check_password(password, username)
        self.registration_gate(invite_code, is_local)      # cheap check first: don't burn CPU hashing for strangers
        pw = self._hash(password)
        with self._store.transaction() as d:
            self._gate(d, invite_code, is_local, consume=False)
            if any(u["username"] == username for u in d["users"].values()):
                raise AccountError("That username is taken.", 409, "username")
            self._gate(d, invite_code, is_local, consume=True)
            uid = _new_id()
            while uid in d["users"]:
                uid = _new_id()
            d["users"][uid] = {"id": uid, "username": username, "display_name": display, "email": email,
                               "role": "member" if d["users"] else "owner", "created": self.clock(), "password": pw}
            return self.public_user(d["users"][uid])

    def authenticate(self, username: str, password: str):
        name = username.strip().lower() if isinstance(username, str) else ""
        rec = self._store.read(lambda d: next(
            (dict(u) for u in d["users"].values() if u["username"] == name), None))
        password = password if isinstance(password, str) else ""
        if rec is None:
            self._check(password, self._dummy)
            return None
        return self.public_user(rec) if self._check(password, rec["password"]) else None

    def update_profile(self, uid: str, display_name=None, email=None) -> dict:
        changes = {}
        if display_name is not None:
            changes["display_name"] = clean_display_name(display_name)
        if email is not None:
            changes["email"] = clean_email(email)
        with self._store.transaction() as d:
            if uid not in d["users"]:
                raise AccountError("Account not found.", 404)
            d["users"][uid].update(changes)
            return self.public_user(d["users"][uid])

    def change_password(self, uid: str, current: str, new: str, keep_session: str | None = None) -> None:
        user = self._store.read(lambda d: dict(d["users"].get(uid) or {}))
        if not user or not isinstance(current, str) or not self._check(current, user["password"]):
            raise AccountError("Your current password isn't right.", 403, "current")
        check_password(new, user["username"])
        pw = self._hash(new)
        with self._store.transaction() as d:
            d["users"][uid]["password"] = pw
            keep = _sha(keep_session) if keep_session else None
            d["sessions"] = {k: s for k, s in d["sessions"].items() if s["uid"] != uid or k == keep}

    def reset_password(self, username: str, new: str) -> None:
        """Host-side reset (python app.py set-password). Signs the person out everywhere."""
        user = self.find_user(username)
        if not user:
            raise AccountError("No account has that username.", 404, "username")
        check_password(new, user["username"])
        pw = self._hash(new)
        with self._store.transaction() as d:
            d["users"][user["id"]]["password"] = pw
            d["sessions"] = {k: s for k, s in d["sessions"].items() if s["uid"] != user["id"]}

    def delete_user(self, uid: str) -> bool:
        with self._store.transaction() as d:
            if uid not in d["users"]:
                return False
            del d["users"][uid]
            d["devices"] = {k: v for k, v in d["devices"].items() if v["uid"] != uid}
            d["sessions"] = {k: v for k, v in d["sessions"].items() if v["uid"] != uid}
            d["invites"] = {k: v for k, v in d["invites"].items() if v["by"] != uid}
            return True

    def set_suspended(self, uid: str, suspended: bool) -> dict:
        """Block (or allow again) an account. Suspending signs the person out everywhere at once."""
        with self._store.transaction() as d:
            user = d["users"].get(uid)
            if user is None:
                raise AccountError("Account not found.", 404)
            if user["role"] == "owner":
                raise AccountError("The owner can't be suspended.", 409)
            user["suspended"] = bool(suspended)
            if suspended:
                d["sessions"] = {k: s for k, s in d["sessions"].items() if s["uid"] != uid}
            return self.public_user(user)

    def count_users(self) -> int:
        return self._store.read(lambda d: len(d["users"]))

    def make_device_room(self, uid: str) -> None:
        """Make room for one more device by removing the least recently used one (server mode signs devices in
        automatically, so a person who clears cookies often must not get stuck at the cap)."""
        with self._store.transaction() as d:
            mine = [x for x in d["devices"].values() if x["uid"] == uid]
            if len(mine) >= MAX_DEVICES:
                oldest = min(mine, key=lambda x: x["last_active"])
                del d["devices"][oldest["id"]]
                d["sessions"] = {k: s for k, s in d["sessions"].items() if s["did"] != oldest["id"]}

    def verify_password(self, uid: str, password) -> bool:
        user = self._store.read(lambda d: dict(d["users"].get(uid) or {}))
        return bool(user) and isinstance(password, str) and self._check(password, user["password"])

    # ------------------------------------------------------------ sessions

    def create_session(self, uid: str, did: str | None, agent: str, origin: str) -> str:
        raw = secrets.token_urlsafe(32)
        now = self.clock()
        with self._store.transaction() as d:
            self._purge(d, now)
            d["sessions"][_sha(raw)] = {"id": _new_id(), "uid": uid, "did": did, "created": now, "last": now,
                                        "agent": agent, "origin": origin}
        return raw

    def _purge(self, d: dict, now: float) -> None:
        dead = [k for k, s in d["sessions"].items()
                if now - s["created"] > self.max_seconds or now - s["last"] > self.idle_seconds]
        for k in dead:
            del d["sessions"][k]

    def lookup_session(self, raw) -> dict | None:
        """The live session for this cookie value, or None. Also refreshes last-active times (at most once a minute)."""
        if not isinstance(raw, str) or not raw:
            return None
        key, now = _sha(raw), self.clock()

        def peek(d):
            s = d["sessions"].get(key)
            if s is None:
                return None
            if now - s["created"] > self.max_seconds or now - s["last"] > self.idle_seconds:
                return "end"
            if s["uid"] not in d["users"] or (s["did"] and s["did"] not in d["devices"]):
                return "end"
            if d["users"][s["uid"]].get("suspended"):
                return "end"
            return dict(s)

        found = self._store.read(peek)
        if found is None:
            return None
        if found == "end":
            self._drop(key)
            return None
        if now - found["last"] > TOUCH_EVERY:
            with self._store.transaction() as d:
                if key in d["sessions"]:
                    d["sessions"][key]["last"] = now
                    dev = d["devices"].get(found["did"] or "")
                    if dev:
                        dev["last_active"] = now
            found["last"] = now
        return found

    def _drop(self, key: str) -> None:
        with self._store.transaction() as d:
            d["sessions"].pop(key, None)

    def attach_device(self, raw: str, did: str) -> None:
        with self._store.transaction() as d:
            if _sha(raw) in d["sessions"]:
                d["sessions"][_sha(raw)]["did"] = did

    def end_session(self, raw) -> None:
        if isinstance(raw, str) and raw:
            self._drop(_sha(raw))

    def end_all_sessions(self, uid: str) -> int:
        with self._store.transaction() as d:
            before = len(d["sessions"])
            d["sessions"] = {k: s for k, s in d["sessions"].items() if s["uid"] != uid}
            return before - len(d["sessions"])

    def end_everyone(self) -> int:
        """Host-side panic button: sign every account out of every browser."""
        with self._store.transaction() as d:
            n = len(d["sessions"])
            d["sessions"] = {}
            return n

    def list_sessions(self, uid: str, current_id: str | None) -> list[dict]:
        def build(d):
            names = {k: v["name"] for k, v in d["devices"].items()}
            rows = [{"id": s["id"], "device": names.get(s["did"] or "", "Not yet authorised"), "agent": s["agent"],
                     "origin": s["origin"], "created": s["created"], "last": s["last"],
                     "current": s["id"] == current_id}
                    for s in d["sessions"].values() if s["uid"] == uid]
            return sorted(rows, key=lambda r: r["last"], reverse=True)
        return self._store.read(build)

    def revoke_session(self, uid: str, session_id: str) -> bool:
        with self._store.transaction() as d:
            for k, s in list(d["sessions"].items()):
                if s["id"] == session_id and s["uid"] == uid:
                    del d["sessions"][k]
                    return True
            return False

    # ------------------------------------------------------------ devices

    @staticmethod
    def _public_device(dev: dict) -> dict:
        return {k: dev[k] for k in ("id", "name", "type", "created", "last_active", "reviewed", "agent")}

    def create_device(self, uid: str, name, dtype, agent: str) -> tuple[dict, str]:
        """Register a device for ``uid``. Returns (device, credential); the credential goes into a cookie, once."""
        name, dtype = clean_device_name(name), clean_device_type(dtype)
        token = secrets.token_urlsafe(32)
        now = self.clock()
        with self._store.transaction() as d:
            if uid not in d["users"]:
                raise AccountError("Account not found.", 404)
            if sum(1 for x in d["devices"].values() if x["uid"] == uid) >= MAX_DEVICES:
                raise AccountError(f"You already have {MAX_DEVICES} devices. Remove one you no longer use first.", 409)
            did = _new_id()
            while did in d["devices"]:
                did = _new_id()
            first = not any(x["uid"] == uid for x in d["devices"].values())
            d["devices"][did] = {"id": did, "uid": uid, "name": name, "type": dtype, "created": now,
                                 "last_active": now, "token_hash": _sha(token), "agent": agent,
                                 "reviewed": first}        # the account's first device is trusted; later ones get reviewed
            return self._public_device(d["devices"][did]), f"{did}.{token}"

    def verify_device(self, uid: str, credential) -> dict | None:
        """The device this cookie value proves, if it is still registered to ``uid``."""
        if not isinstance(credential, str) or "." not in credential:
            return None
        did, _, token = credential.partition(".")
        if not ID_RE.match(did):
            return None

        def find(d):
            dev = d["devices"].get(did)
            if dev and dev["uid"] == uid and hmac.compare_digest(dev["token_hash"], _sha(token)):
                return self._public_device(dev)
            return None
        return self._store.read(find)

    def get_device(self, uid: str, did: str) -> dict | None:
        if not ID_RE.match(did or ""):
            return None
        return self._store.read(
            lambda d: self._public_device(d["devices"][did]) if did in d["devices"] and d["devices"][did]["uid"] == uid
            else None)

    def list_devices(self, uid: str, current_did: str | None) -> list[dict]:
        now = self.clock()

        def build(d):
            rows = []
            for dev in d["devices"].values():
                if dev["uid"] != uid:
                    continue
                live = [s for s in d["sessions"].values() if s["did"] == dev["id"]]
                last = max([dev["last_active"]] + [s["last"] for s in live])
                rows.append({**self._public_device(dev), "last_active": last, "current": dev["id"] == current_did,
                             "signed_in": bool(live), "connected": bool(live) and now - last < ONLINE_WINDOW})
            return sorted(rows, key=lambda r: (not r["current"], -r["last_active"]))
        return self._store.read(build)

    def update_device(self, uid: str, did: str, *, name=None, dtype=None, reviewed=None) -> dict:
        changes = {}
        if name is not None:
            changes["name"] = clean_device_name(name)
        if dtype is not None:
            changes["type"] = clean_device_type(dtype)
        if reviewed is not None:
            changes["reviewed"] = bool(reviewed)
        with self._store.transaction() as d:
            dev = d["devices"].get(did) if ID_RE.match(did or "") else None
            if not dev or dev["uid"] != uid:
                raise AccountError("Device not found.", 404)
            dev.update(changes)
            return self._public_device(dev)

    def revoke_device(self, uid: str, did: str) -> bool:
        """Delete the device: its credential stops working and every session on it ends at once."""
        with self._store.transaction() as d:
            dev = d["devices"].get(did) if ID_RE.match(did or "") else None
            if not dev or dev["uid"] != uid:
                return False
            del d["devices"][did]
            d["sessions"] = {k: s for k, s in d["sessions"].items() if s["did"] != did}
            return True

    # ------------------------------------------------------------ invites and registration policy

    def registration_mode(self) -> str:
        return self._store.read(lambda d: d["registration"])

    def set_registration_mode(self, mode) -> str:
        if mode not in REGISTRATION_MODES:
            raise AccountError("Choose invite only, open or closed.", field="mode")
        with self._store.transaction() as d:
            d["registration"] = mode
        return mode

    def create_invite(self, uid: str) -> tuple[dict, str]:
        raw = "CMP-" + "-".join("".join(secrets.choice(INVITE_ALPHABET) for _ in range(4)) for _ in range(3))
        now = self.clock()
        with self._store.transaction() as d:
            for k in [k for k, v in d["invites"].items() if v["expires"] <= now]:
                del d["invites"][k]
            if len(d["invites"]) >= 50:
                raise AccountError("Too many unused invites. Revoke some first.", 409)
            iid = _new_id()
            d["invites"][iid] = {"id": iid, "hash": code_hash(raw), "by": uid, "created": now,
                                 "expires": now + INVITE_DAYS * 86400}
            return {"id": iid, "created": now, "expires": now + INVITE_DAYS * 86400}, raw

    def list_invites(self) -> list[dict]:
        now = self.clock()
        return self._store.read(lambda d: sorted(
            ({"id": i["id"], "created": i["created"], "expires": i["expires"]}
             for i in d["invites"].values() if i["expires"] > now), key=lambda i: i["created"], reverse=True))

    def revoke_invite(self, iid: str) -> bool:
        with self._store.transaction() as d:
            return d["invites"].pop(iid, None) is not None
