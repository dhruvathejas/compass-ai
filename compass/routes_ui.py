"""Pages for signed-in people: the chat app and the account area."""
from __future__ import annotations

from flask import Blueprint, abort, g, redirect, render_template, url_for

from . import security
from .auth import csrf_token

bp = Blueprint("ui", __name__)
SECTIONS = {"profile": "Profile", "security": "Security", "devices": "Devices", "privacy": "Privacy",
            "people": "People"}


@bp.get("/")
def home():
    return render_template("index.html", csrf=csrf_token(), local=security.is_local_request())


@bp.get("/account")
def account():
    return redirect(url_for("ui.account_section", section="profile"))


@bp.get("/devices")
def devices_alias():
    return redirect(url_for("ui.account_section", section="devices"))


@bp.get("/account/<section>")
def account_section(section):
    if section not in SECTIONS or (section == "people" and g.user["role"] != "owner"):
        abort(404)
    nav = [(key, label) for key, label in SECTIONS.items() if key != "people" or g.user["role"] == "owner"]
    return render_template("account.html", csrf=csrf_token(), section=section, nav=nav, title=SECTIONS[section])
