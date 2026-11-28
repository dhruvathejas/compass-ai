"""First-launch pages: Welcome, Install, Connect to a main PC, and the public host check.

Honest architecture note: Compass AI is a web app. A "device connected to a main PC" is simply a browser that opens
the host's address; the host holds the accounts, chats and workspaces. So "connecting" means: confirm the address
really is a Compass AI host (``/api/hello``, asked by the visitor's own browser, so this server never fetches
addresses on a stranger's behalf), then sign in there and authorise the device.
"""
from __future__ import annotations

import sys

from flask import Blueprint, Response, current_app, g, jsonify, redirect, render_template, request, url_for

from .auth import csrf_token, mark_welcomed

bp = Blueprint("onboarding", __name__)
API_VERSION = 1


def _svc():
    return current_app.extensions["compass"]


def _page(template: str, **ctx):
    g.csp_connect = "'self' http: https:" if template == "connect.html" else "'self'"   # only this page probes hosts
    state = _svc().accounts.registration_state()
    resp = Response(render_template(template, csrf=csrf_token(), first_run=state["first"],
                                    can_register=state["first"] or state["mode"] != "closed", **ctx),
                    mimetype="text/html")
    return mark_welcomed(resp)


@bp.get("/welcome")
def welcome():
    if _svc().config.server:                 # visitors of a public server just sign in or sign up
        return redirect(url_for("auth.login_page"))
    return _page("welcome.html")


@bp.get("/install")
def install():
    return _page("install.html", repo_url=_svc().config.repo_url, py="python" if sys.platform == "win32" else "python3")


@bp.get("/connect")
def connect():
    return _page("connect.html")


@bp.get("/api/hello")
def hello():
    """Public, cookie-free and read-only: lets a browser check that an address is a Compass AI host."""
    resp = jsonify({"app": "compass-ai", "api": API_VERSION, "setup_needed": not _svc().accounts.has_users()})
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cache-Control"] = "no-store"
    return resp
