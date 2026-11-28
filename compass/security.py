"""Request classification and abuse limits shared by the whole app.

Where a request comes from decides what it may do, so this is one small, well-tested place:

* ``local``  - straight from this PC (loopback socket, no proxy headers). The only kind allowed to launch programs
               on the host (the Code button, and later the terminal).
* ``lan``    - straight from another device on the network (only possible when the host was started with --lan).
* ``tunnel`` - came through a reverse proxy such as cloudflared. Through the tunnel the socket address is always
               127.0.0.1, so the proxy headers are the only signal; a remote client cannot strip them.
* ``internet`` - server mode only: this is a shared public host, so *every* request is treated as coming from
               outside. Nothing is ever "local", so nobody can launch programs on the server or claim the owner account
               from the browser.
"""
from __future__ import annotations

import collections
import ipaddress
import threading
import time
from urllib.parse import urlsplit

from flask import current_app, request

PROXY_HEADERS = ("Cf-Connecting-Ip", "Cf-Ray", "Cf-Visitor", "X-Forwarded-For", "X-Forwarded-Host",
                 "X-Real-Ip", "Forwarded")
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

CSP_BASE = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
            "connect-src {connect}; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


def server_config():
    """The Config when this app runs in server mode, else None."""
    cfg = current_app.extensions["compass"].config
    return cfg if cfg.server else None


def connection_kind(req=None) -> str:
    req = req or request
    if server_config():
        return "internet"
    if any(h in req.headers for h in PROXY_HEADERS):
        return "tunnel"
    return "local" if (req.remote_addr or "") in LOOPBACK else "lan"


def is_local_request(req=None) -> bool:
    return connection_kind(req) == "local"


def client_key(req=None) -> str:
    """Identity used for throttling. Requests from this PC share one bucket; others use the visitor's address."""
    req = req or request
    kind = connection_kind(req)
    if kind == "local":
        return "local"
    cfg = server_config()
    if cfg:
        return _address(_proxy_value(req, cfg.proxy_header) or req.remote_addr or "")
    raw = (req.headers.get("Cf-Connecting-Ip") or req.headers.get("X-Forwarded-For", "").split(",")[0].strip()
           if kind == "tunnel" else req.remote_addr or "")
    return _address(raw)


def _address(raw: str) -> str:
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return "unknown"


def _proxy_value(req, header: str) -> str:
    """The visitor address a trusted proxy put in ``header``. With a list (X-Forwarded-For) the proxy appends the
    address it saw to the END, so the last entry is the trustworthy one: earlier entries are whatever the client sent."""
    if not header:
        return ""
    return req.headers.get(header, "").split(",")[-1].strip()


def is_https(req=None) -> bool:
    req = req or request
    cfg = server_config()
    if cfg and cfg.public_url.startswith("https://"):
        return True
    return (req.headers.get("X-Forwarded-Proto", "").lower() == "https"
            or '"https"' in req.headers.get("Cf-Visitor", ""))


def host_allowed(allowed_hosts) -> bool:
    """Refuse unknown Host names on direct connections (DNS rebinding). A tunnel chooses the Host itself."""
    if connection_kind() == "tunnel":
        return True
    host = (urlsplit("//" + request.host).hostname or "").lower()
    if server_config():                      # a public host answers to the names it was given (any, if none given)
        return host in LOCAL_HOSTS or not allowed_hosts or host in allowed_hosts
    return host in LOCAL_HOSTS or host in allowed_hosts


def same_origin() -> bool:
    site = request.headers.get("Sec-Fetch-Site")
    if site:
        return site in ("same-origin", "none")
    origin = request.headers.get("Origin")
    if origin is None:
        return True            # not a browser; the CSRF token is still required
    allowed = {request.host, request.headers.get("X-Forwarded-Host", "")}
    return urlsplit(origin).netloc in allowed


# ----------------------------------------------------------------------------- brute-force throttle

class LoginThrottle:
    FREE_TRIES = 5
    MAX_LOCK = 15 * 60
    GLOBAL_LIMIT, GLOBAL_WINDOW, GLOBAL_LOCK = 30, 600, 60

    def __init__(self, clock=time.monotonic, global_limit: int | None = None):
        self._clock = clock
        self.GLOBAL_LIMIT = global_limit or self.GLOBAL_LIMIT      # a busy public server sees more honest typos
        self._lock = threading.Lock()
        self._state: dict[str, list] = {}          # key -> [failures, locked_until]
        self._remote_fails: collections.deque = collections.deque()

    def wait_seconds(self, key: str) -> int:
        """Seconds the caller must still wait before another attempt (0 = go ahead)."""
        now = self._clock()
        with self._lock:
            _, until = self._state.get(key, (0, 0.0))
            wait = max(0.0, until - now)
            if key != "local":
                self._trim(now)
                if len(self._remote_fails) >= self.GLOBAL_LIMIT:
                    wait = max(wait, self._remote_fails[-1] + self.GLOBAL_LOCK - now)
            return int(wait) + 1 if wait > 0 else 0

    def failed(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            fails, _ = self._state.get(key, (0, 0.0))
            fails += 1
            lock = 0 if fails < self.FREE_TRIES else min(self.MAX_LOCK, 30 * 2 ** (fails - self.FREE_TRIES))
            self._state[key] = [fails, now + lock]
            if key != "local":
                self._remote_fails.append(now)
            if len(self._state) > 10_000:           # bound memory
                self._state = dict(list(self._state.items())[-5_000:])

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)

    def _trim(self, now: float) -> None:
        while self._remote_fails and now - self._remote_fails[0] > self.GLOBAL_WINDOW:
            self._remote_fails.popleft()


class RateLimiter:
    """At most ``limit`` counted actions per ``window`` seconds per key (used for sign-ups)."""

    def __init__(self, limit: int, window: int, clock=time.monotonic):
        self.limit, self.window, self._clock = limit, window, clock
        self._lock = threading.Lock()
        self._hits: dict[str, collections.deque] = {}

    def _queue(self, key: str, now: float) -> collections.deque:
        q = self._hits.setdefault(key, collections.deque())
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def wait(self, key: str, limit: int | None = None) -> int:
        """Seconds until ``key`` may act again (0 = allowed now). Does not count as an action."""
        now = self._clock()
        with self._lock:
            q = self._queue(key, now)
            return int(q[0] + self.window - now) + 1 if len(q) >= (limit or self.limit) else 0

    def record(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            self._queue(key, now).append(now)
            if len(self._hits) > 10_000:
                self._hits = dict(list(self._hits.items())[-5_000:])

    def hit(self, key: str, limit: int | None = None) -> int:
        """Check and record in one step. Returns 0 when allowed, otherwise the seconds to wait."""
        wait = self.wait(key, limit)
        if not wait:
            self.record(key)
        return wait
