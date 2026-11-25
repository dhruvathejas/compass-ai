"""Authentication and request hardening.

Threat model: the app is reachable from the internet through a Cloudflare tunnel and can (from milestone 3 on)
run commands on this PC, so every request must come from the one signed-in owner.

* Password: scrypt, set from the host's terminal (``python app.py set-password``), never over the web.
* Session: signed cookie (HttpOnly, SameSite=Strict, Secure over https) with idle + absolute expiry and a server-side
  "epoch" so a password change or "sign out everywhere" invalidates every cookie at once.
* CSRF: per-session token on every state-changing request, plus a Sec-Fetch-Site / Origin check.
* Brute force: per-address lockout with exponential back-off, plus a global brake for tunnel traffic.
* Tunnel detection: cloudflared always adds proxy headers, a direct local request has none. (The socket address
  cannot be used: through the tunnel it is always 127.0.0.1.)
"""
from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import ipaddress
import os
import secrets
import threading
import time
from urllib.parse import urlsplit

from flask import (Blueprint, Response, current_app, g, jsonify, redirect, render_template, request, session,
                   url_for)
from flask.sessions import SecureCookieSessionInterface

from .store import JsonStore

MIN_PASSWORD = 10
MAX_PASSWORD = 1024
SCRYPT_PARAMS = {"n": 2 ** 15, "r": 8, "p": 1}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PROXY_HEADERS = ("Cf-Connecting-Ip", "Cf-Ray", "Cf-Visitor", "X-Forwarded-For", "X-Forwarded-Host",
                 "X-Real-Ip", "Forwarded")
PUBLIC_ENDPOINTS = {"auth.login_page", "auth.login_post"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

bp = Blueprint("auth", __name__)


# ----------------------------------------------------------------------------- request classification

def is_tunnel_request(req=None) -> bool:
    """True when the request came through a reverse proxy / tunnel rather than straight from this PC."""
    req = req or request
    return any(h in req.headers for h in PROXY_HEADERS)


def client_key(req=None) -> str:
    """Identity used for throttling. Local requests share one bucket; tunnel requests use the visitor's IP."""
    req = req or request
    if not is_tunnel_request(req):
        return "local"
    raw = req.headers.get("Cf-Connecting-Ip") or req.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return "unknown"


def _is_https(req) -> bool:
    return (req.headers.get("X-Forwarded-Proto", "").lower() == "https"
            or '"https"' in req.headers.get("Cf-Visitor", ""))


def _host_allowed(cfg) -> bool:
    if is_tunnel_request():
        return True            # a proxy chose the Host; the password still guards everything
    host = urlsplit("//" + request.host).hostname or ""
    return host.lower() in LOCAL_HOSTS or host.lower() in cfg.allowed_hosts   # blocks DNS rebinding


def _same_origin() -> bool:
    site = request.headers.get("Sec-Fetch-Site")
    if site:
        return site in ("same-origin", "none")
    origin = request.headers.get("Origin")
    if origin is None:
        return True            # not a browser; the CSRF token is still required
    allowed = {request.host, request.headers.get("X-Forwarded-Host", "")}
    return urlsplit(origin).netloc in allowed


# ----------------------------------------------------------------------------- password + secret storage

class AuthState:
    def __init__(self, path):
        self.path = path
        self._store = JsonStore(
            path,
            default=lambda: {"secret": secrets.token_hex(32), "epoch": 1, "password": None},
            version=1,
        )
        with self._store.transaction():
            pass                                   # create the file (and the signing secret) on first run
        try:
            os.chmod(path, 0o600)                  # best effort; a no-op on Windows
        except OSError:
            pass

    @staticmethod
    def _derive(password: str, salt: bytes, params: dict) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, maxmem=128 * 1024 * 1024, **params)

    def has_password(self) -> bool:
        return self._store.read(lambda d: d["password"] is not None)

    def secret_key(self) -> str:
        return self._store.read(lambda d: d["secret"])

    def epoch(self) -> int:
        return self._store.read(lambda d: d["epoch"])

    def set_password(self, password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Use at least {MIN_PASSWORD} characters.")
        if len(password) > MAX_PASSWORD:
            raise ValueError("That password is too long.")
        salt = os.urandom(16)
        digest = self._derive(password, salt, SCRYPT_PARAMS)
        with self._store.transaction() as d:
            d["password"] = {"salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest).decode(),
                             **SCRYPT_PARAMS}
            d["epoch"] += 1                        # everyone is signed out

    def bump_epoch(self) -> None:
        with self._store.transaction() as d:
            d["epoch"] += 1

    def verify(self, password: str) -> bool:
        rec = self._store.read(lambda d: dict(d["password"]) if d["password"] else None)
        password = password[:MAX_PASSWORD]
        if rec is None:                            # keep timing similar whether or not a password exists
            self._derive(password, b"\0" * 16, SCRYPT_PARAMS)
            return False
        params = {k: rec[k] for k in ("n", "r", "p")}
        got = self._derive(password, base64.b64decode(rec["salt"]), params)
        return hmac.compare_digest(got, base64.b64decode(rec["hash"]))


# ----------------------------------------------------------------------------- brute-force throttle

class LoginThrottle:
    FREE_TRIES = 5
    MAX_LOCK = 15 * 60
    GLOBAL_LIMIT, GLOBAL_WINDOW, GLOBAL_LOCK = 30, 600, 60

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, list] = {}          # key -> [failures, locked_until]
        self._tunnel_fails: collections.deque = collections.deque()

    def wait_seconds(self, key: str) -> int:
        """Seconds the caller must still wait before another attempt (0 = go ahead)."""
        now = self._clock()
        with self._lock:
            wait = 0.0
            fails, until = self._state.get(key, (0, 0.0))
            wait = max(wait, until - now)
            if key != "local":
                self._trim(now)
                if len(self._tunnel_fails) >= self.GLOBAL_LIMIT:
                    wait = max(wait, self._tunnel_fails[-1] + self.GLOBAL_LOCK - now)
            return int(wait) + 1 if wait > 0 else 0

    def failed(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            fails, _ = self._state.get(key, (0, 0.0))
            fails += 1
            lock = 0 if fails < self.FREE_TRIES else min(self.MAX_LOCK, 30 * 2 ** (fails - self.FREE_TRIES))
            self._state[key] = [fails, now + lock]
            if key != "local":
                self._tunnel_fails.append(now)
            if len(self._state) > 10_000:           # bound memory
                self._state = dict(list(self._state.items())[-5_000:])

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    def _trim(self, now: float) -> None:
        while self._tunnel_fails and now - self._tunnel_fails[0] > self.GLOBAL_WINDOW:
            self._tunnel_fails.popleft()


# ----------------------------------------------------------------------------- sessions

class CompassSessionInterface(SecureCookieSessionInterface):
    def get_cookie_secure(self, app) -> bool:        # Secure only when the browser is on https (tunnel)
        return _is_https(request)


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _services():
    return current_app.extensions["compass"]


def _session_valid() -> bool:
    svc = _services()
    if not session.get("uid"):
        return False
    now = time.time()
    if session.get("epoch") != svc.auth.epoch():
        return False
    if now - session.get("iat", 0) > svc.config.session_max_seconds:
        return False
    last = session.get("last", 0)
    if now - last > svc.config.session_idle_seconds:
        return False
    if now - last > 60:
        session["last"] = now                       # sliding idle window, without rewriting the cookie each call
    return True


def _start_session() -> None:
    session.clear()                                  # new identifier material: prevents session fixation
    now = time.time()
    session.update(uid=1, epoch=_services().auth.epoch(), iat=now, last=now, csrf=secrets.token_urlsafe(32))
    session.permanent = True


def _wants_json() -> bool:
    return request.path.startswith("/api/")


def _deny(status: int, message: str):
    if _wants_json():
        return jsonify({"error": message}), status
    return Response(message, status=status, mimetype="text/plain")


# ----------------------------------------------------------------------------- guard + headers

def install(app) -> None:
    app.session_interface = CompassSessionInterface()
    app.config.update(
        SESSION_COOKIE_NAME="compass_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_REFRESH_EACH_REQUEST=False,
        PERMANENT_SESSION_LIFETIME=app.extensions["compass"].config.session_max_seconds,
    )
    app.register_blueprint(bp)
    app.before_request(_guard)
    app.after_request(_headers)


def _guard():
    svc = _services()
    if request.endpoint == "static":
        return None
    if not _host_allowed(svc.config):
        return _deny(400, "Unrecognised host name.")
    if not svc.auth.has_password():
        if _wants_json():
            return jsonify({"error": "Compass AI is not set up yet. Run: python app.py set-password"}), 503
        return render_template("setup.html"), 503
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("csrf", "")
        if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
            return _deny(403, "Your page is out of date. Reload and try again.")
        if not _same_origin():
            return _deny(403, "Cross-site request blocked.")
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not _session_valid():
        if _wants_json():
            return jsonify({"error": "Please sign in again."}), 401
        return redirect(url_for("auth.login_page"))
    return None


def _headers(resp):
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.endpoint != "static":
        resp.headers["Cache-Control"] = "no-store"
    if _is_https(request):
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    return resp


# ----------------------------------------------------------------------------- routes

@bp.get("/login")
def login_page():
    if _session_valid():
        return redirect(url_for("ui.home"))
    return render_template("login.html", csrf=csrf_token(), error=None)


@bp.post("/login")
def login_post():
    svc = _services()
    key = client_key()
    wait = svc.throttle.wait_seconds(key)
    if wait:
        resp = render_template("login.html", csrf=csrf_token(), error=f"Too many attempts. Try again in {wait} seconds.")
        return resp, 429, {"Retry-After": str(wait)}
    password = request.form.get("password", "")
    if svc.auth.verify(password):
        svc.throttle.succeeded(key)
        _start_session()
        return redirect(url_for("ui.home"))
    svc.throttle.failed(key)
    return render_template("login.html", csrf=csrf_token(), error="That password isn't right."), 401


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login_page"))


@bp.post("/api/sessions/revoke")
def revoke_all():
    """Sign out everywhere (every browser and phone), including this one."""
    _services().auth.bump_epoch()
    session.clear()
    return jsonify({"ok": True})
