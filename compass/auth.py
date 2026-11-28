"""Signing in: the request guard, session and device cookies, and the sign-in / sign-up / device pages.

Threat model: the app can be reached from the internet through a Cloudflare tunnel (and from the local network when
started with --lan), and it will run commands on this PC, so every private request must come from a signed-in
person whose browser is an authorised device, and must only ever touch that person's own data.

* Password: scrypt per account (see accounts.py). The first account is created on this PC only; after that,
  sign-ups need an invite unless the owner opens registration.
* Session: signed cookie holding a random id; the record lives on the server, so it can be ended at any time.
  HttpOnly, SameSite=Strict, Secure over https, idle + absolute expiry.
* Device: after signing in, a browser must be authorised once. Its credential lives in its own HttpOnly cookie and
  is stored hashed; deleting the device invalidates it and ends its sessions on the spot.
* CSRF: per-session token on every state-changing request, plus a Sec-Fetch-Site / Origin check.
* Brute force: per-address lockout with exponential back-off, a global brake for outside traffic, and a limit on
  sign-ups per address.
"""
from __future__ import annotations

import hmac
import secrets

from flask import (Blueprint, Response, current_app, g, jsonify, redirect, render_template, request, session,
                   url_for)
from flask.sessions import SecureCookieSessionInterface

from . import security
from .accounts import (DEVICE_TYPES, AccountError, describe_agent, guess_device_type)

PUBLIC_ENDPOINTS = {"auth.login_page", "auth.login_post", "auth.register_page", "auth.register_post",
                    "onboarding.welcome", "onboarding.install", "onboarding.connect", "onboarding.hello"}
PENDING_DEVICE_OK = PUBLIC_ENDPOINTS | {"auth.device_page", "auth.device_authorize", "auth.logout"}
SETUP_REDIRECT = {"auth.login_page", "auth.login_post"}       # with no accounts yet, sign-in means "create the owner"
WELCOME_COOKIE = "compass_seen"
YEAR = 365 * 24 * 3600
ORIGIN_LABELS = {"local": "This PC", "lan": "Local network", "tunnel": "Internet (tunnel)", "internet": "Internet"}

bp = Blueprint("auth", __name__)


def _svc():
    return current_app.extensions["compass"]


def device_cookie_name(uid: str) -> str:
    return f"compass_dev_{uid}"           # one per account, so two people can share one browser


class CompassSessionInterface(SecureCookieSessionInterface):
    def get_cookie_secure(self, app) -> bool:        # Secure only when the browser is on https (tunnel)
        return security.is_https(request)


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def _wants_json() -> bool:
    return request.path.startswith("/api/")


def _deny(status: int, message: str, **extra):
    if _wants_json():
        return jsonify({"error": message, **extra}), status
    return Response(message, status=status, mimetype="text/plain")


def _set_cookie(resp, name: str, value: str, max_age: int, samesite: str = "Strict"):
    resp.set_cookie(name, value, max_age=max_age, httponly=True, samesite=samesite,
                    secure=security.is_https(request), path="/")
    return resp


def mark_welcomed(resp):
    """Remember that this browser has seen the welcome page, so later visits go straight to sign-in."""
    return _set_cookie(resp, WELCOME_COOKIE, "1", YEAR, samesite="Lax")


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
    svc = _svc()
    g.user = g.session = g.device = None
    g.csp_connect = "'self'"
    if request.endpoint == "static":
        return None
    if not security.host_allowed(svc.config.allowed_hosts):
        return _deny(400, "Unrecognised host name.")
    if request.endpoint == "onboarding.hello":
        return None                                  # public, cookie-free, read-only

    if not svc.accounts.has_users() and request.endpoint not in PUBLIC_ENDPOINTS | {"auth.logout"}:
        return _setup_needed()
    if not svc.accounts.has_users() and request.endpoint in SETUP_REDIRECT:
        return _setup_needed()

    if request.method not in security.SAFE_METHODS:
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        expected = session.get("csrf", "")
        if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
            return _deny(403, "Your page is out of date. Reload and try again.")
        if not security.same_origin():
            return _deny(403, "Cross-site request blocked.")

    record = svc.accounts.lookup_session(session.get("sid"))
    if record:
        g.session, g.user = record, svc.accounts.get_user(record["uid"])
        g.device = svc.accounts.get_device(record["uid"], record["did"]) if record["did"] else None
    elif session.get("sid"):
        session.pop("sid", None)                     # ended, expired or revoked elsewhere

    if request.endpoint in PUBLIC_ENDPOINTS and request.endpoint not in ("auth.logout",):
        return None
    if g.user is None:
        if _wants_json():
            return jsonify({"error": "Please sign in again."}), 401
        # a first visit sees the welcome page, a returning browser goes straight to sign-in
        first_visit = (request.endpoint == "ui.home" and not request.cookies.get(WELCOME_COOKIE)
                       and not svc.config.server)          # a public server goes straight to sign-in
        return redirect(url_for("onboarding.welcome" if first_visit else "auth.login_page"))
    if g.device is None and request.endpoint not in PENDING_DEVICE_OK:
        if _wants_json():
            return jsonify({"error": "Authorize this device first.", "code": "device_required"}), 403
        return redirect(url_for("auth.device_page"))
    return None


def _setup_needed():
    if _wants_json():
        where = ("Create the owner account on the server with `python app.py set-password`." if _svc().config.server
                 else "Open it on the computer that runs it to create the owner account.")
        return jsonify({"error": f"Compass AI has no accounts yet. {where}"}), 503
    if security.is_local_request():                  # on this PC: welcome first, then "create the owner account"
        seen = request.cookies.get(WELCOME_COOKIE) or request.endpoint in SETUP_REDIRECT
        return redirect(url_for("auth.register_page" if seen else "onboarding.welcome"))
    return Response(render_template("setup.html"), status=503, mimetype="text/html")


def _headers(resp):
    resp.headers["Content-Security-Policy"] = security.CSP_BASE.format(connect=g.get("csp_connect", "'self'"))
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.endpoint != "static":
        resp.headers["Cache-Control"] = "no-store"
    if security.is_https(request):
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    return resp


# ----------------------------------------------------------------------------- session lifecycle

def begin_session(user: dict) -> None:
    """Sign ``user`` in on this browser. A device credential for them, if present and valid, is attached."""
    svc = _svc()
    svc.accounts.end_session(session.get("sid"))
    device = svc.accounts.verify_device(user["id"], request.cookies.get(device_cookie_name(user["id"])))
    raw = svc.accounts.create_session(user["id"], device["id"] if device else None,
                                      describe_agent(request.user_agent.string),
                                      security.connection_kind())
    session.clear()                                  # fresh identifiers: prevents session fixation
    session.update(sid=raw, csrf=secrets.token_urlsafe(32))
    session.permanent = True


def finish_login(user: dict):
    """Sign ``user`` in and send them home. On a public server their browser is registered as a device right away
    (it shows up under Devices, where they can rename or remove it); on a personal host they authorise it by hand."""
    begin_session(user)
    resp = redirect(url_for("ui.home"))
    svc = _svc()
    if svc.config.server and svc.accounts.lookup_session(session.get("sid"))["did"] is None:
        svc.accounts.make_device_room(user["id"])
        agent = request.user_agent.string
        device, credential = svc.accounts.create_device(user["id"], describe_agent(agent)[:40],
                                                        guess_device_type(agent), describe_agent(agent))
        svc.accounts.attach_device(session["sid"], device["id"])
        _set_cookie(resp, device_cookie_name(user["id"]), credential, YEAR)
    return mark_welcomed(resp)


def _form(name: str, limit: int = 2000) -> str:
    value = request.form.get(name, "")
    return value[:limit]


# ----------------------------------------------------------------------------- sign in

@bp.get("/login")
def login_page():
    if g.user:
        return redirect(url_for("ui.home"))
    return _render_login()


def _render_login(error=None, username="", status=200, headers=None):
    state = _svc().accounts.registration_state()
    body = render_template("login.html", csrf=csrf_token(), error=error, username=username,
                           can_register=state["mode"] != "closed")
    resp = Response(body, status=status, mimetype="text/html", headers=headers)
    return mark_welcomed(resp)


@bp.post("/login")
def login_post():
    svc = _svc()
    key = security.client_key()
    username = _form("username", 64)
    keys = [key]
    if svc.config.server:                        # on a public server also slow down guessing against ONE account
        keys.append("user:" + username.strip().lower())
    wait = max(svc.throttle.wait_seconds(k) for k in keys)
    if wait:
        return _render_login(f"Too many attempts. Try again in {wait} seconds.", username, 429,
                             {"Retry-After": str(wait)})
    user = svc.accounts.authenticate(username, _form("password", 1100))
    if user is None:
        for k in keys:
            svc.throttle.failed(k)
        return _render_login("That username and password don't match.", username, 401)
    for k in keys:
        svc.throttle.succeeded(k)
    if user["suspended"]:
        return _render_login("This account has been suspended. Contact the owner of this server.", username, 403)
    return finish_login(user)


@bp.post("/logout")
def logout():
    _svc().accounts.end_session(session.get("sid"))
    session.clear()
    return redirect(url_for("auth.login_page"))


# ----------------------------------------------------------------------------- sign up

def _render_register(error=None, field=None, values=None, status=200):
    svc = _svc()
    state = svc.accounts.registration_state()
    body = render_template("register.html", csrf=csrf_token(), error=error, error_field=field, values=values or {},
                           first=state["first"], mode=state["mode"],
                           invite=(values or {}).get("invite") or request.args.get("invite", "")[:40])
    return mark_welcomed(Response(body, status=status, mimetype="text/html"))


@bp.get("/register")
def register_page():
    if g.user:
        return redirect(url_for("ui.home"))
    state = _svc().accounts.registration_state()
    if state["first"] and not security.is_local_request():
        return _setup_needed()
    return _render_register()


@bp.post("/register")
def register_post():
    svc = _svc()
    state = svc.accounts.registration_state()
    local = security.is_local_request()
    if state["first"] and not local:
        return _setup_needed()
    key = security.client_key()
    wait = 0 if key == "local" else svc.signups.wait(key)       # the person at this PC is never rate-limited
    if wait:
        return _render_register(f"Too many sign-up attempts from here. Try again in {wait // 60 + 1} minutes.",
                                status=429)
    if svc.config.server:
        if svc.policy.accounts_full(svc.accounts.count_users()):
            return _render_register("This server isn't accepting new accounts right now (it's full).", status=403)
        busy = svc.signups_all.wait("all")
        if busy:
            return _render_register("Lots of people are signing up right now. Try again in a few minutes.",
                                    status=429)
    values = {"username": _form("username", 64), "display_name": _form("display_name", 100),
              "email": _form("email", 300), "invite": _form("invite", 40)}
    password = _form("password", 1100)
    if password != _form("confirm", 1100):
        return _render_register("The two passwords don't match.", "confirm", values, 400)
    try:
        user = svc.accounts.register(values["username"], values["display_name"], values["email"], password,
                                     invite_code=values["invite"], is_local=local)
    except AccountError as err:
        if err.status == 403:                                  # wrong invite / closed: counts against guessing
            svc.signups.record(key)
        return _render_register(str(err), err.field, values, err.status)
    svc.signups.record(key)
    svc.signups_all.record("all")
    if state["first"]:
        svc.userdata.adopt_legacy(user["id"])         # chats from before accounts existed belong to the owner
    return finish_login(user)


# ----------------------------------------------------------------------------- authorise this device

@bp.get("/device")
def device_page():
    if g.device:
        return redirect(url_for("ui.home"))
    return _render_device()


def _render_device(error=None, values=None, status=200):
    ua = request.user_agent.string
    values = values or {"name": describe_agent(ua), "type": guess_device_type(ua)}
    first = not _svc().accounts.list_devices(g.user["id"], None)
    return render_template("device.html", csrf=csrf_token(), error=error, values=values, types=DEVICE_TYPES,
                           first=first, origin=ORIGIN_LABELS[security.connection_kind()]), status


@bp.post("/device")
def device_authorize():
    svc = _svc()
    if g.device:
        return redirect(url_for("ui.home"))
    values = {"name": _form("name", 100), "type": _form("type", 20)}
    try:
        device, credential = svc.accounts.create_device(g.user["id"], values["name"], values["type"],
                                                        describe_agent(request.user_agent.string))
    except AccountError as err:
        return _render_device(str(err), values, err.status)
    svc.accounts.attach_device(session["sid"], device["id"])
    resp = redirect(url_for("ui.home"))
    return _set_cookie(resp, device_cookie_name(g.user["id"]), credential, YEAR)
