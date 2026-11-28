"""Account API: profile, password, sessions, devices, the owner's people panel, data export and deletion.

Every handler takes the person from ``g.user`` (set by the guard from a verified session) and never from the
request body, and every device / session lookup is scoped to that person. Asking for someone else's id is
indistinguishable from asking for one that doesn't exist (404).
"""
from __future__ import annotations

import functools
import json
import time

from flask import Blueprint, Response, current_app, g, jsonify, request, session

from . import security
from .accounts import ID_RE, AccountError
from .auth import ORIGIN_LABELS

bp = Blueprint("account", __name__)


def _svc():
    return current_app.extensions["compass"]


def _body() -> dict:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise AccountError("Send a JSON object.")
    return payload


@bp.errorhandler(AccountError)
def _account_error(err: AccountError):
    return jsonify({"error": str(err), "field": err.field}), err.status


def owner_only(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        if g.user["role"] != "owner":
            return jsonify({"error": "Only the owner of this host can do that."}), 403
        return fn(*a, **k)
    return wrapper


def _current_session_id():
    return g.session["id"] if g.session else None


# ----------------------------------------------------------------------------- profile

@bp.get("/api/account")
def get_account():
    svc = _svc()
    kind = security.connection_kind()
    return jsonify({"user": g.user, "device": g.device,
                    "connection": {"kind": kind, "label": ORIGIN_LABELS[kind], "host": request.host},
                    "registration": svc.accounts.registration_mode() if g.user["role"] == "owner" else None})


@bp.patch("/api/account/profile")
def patch_profile():
    body = _body()
    unknown = set(body) - {"display_name", "email"}
    if unknown:
        raise AccountError(f"Unknown field: {sorted(unknown)[0]}")
    return jsonify(_svc().accounts.update_profile(g.user["id"], body.get("display_name"), body.get("email")))


@bp.post("/api/account/password")
def change_password():
    body = _body()
    _svc().accounts.change_password(g.user["id"], body.get("current"), body.get("new"),
                                    keep_session=session.get("sid"))
    return jsonify({"ok": True, "message": "Password changed. Your other browsers were signed out."})


# ----------------------------------------------------------------------------- sessions

@bp.get("/api/sessions")
def list_sessions():
    return jsonify(_svc().accounts.list_sessions(g.user["id"], _current_session_id()))


@bp.post("/api/sessions/revoke")
def revoke_all():
    """Sign out everywhere (every browser and phone), including this one. Devices stay registered."""
    _svc().accounts.end_all_sessions(g.user["id"])
    session.clear()
    return jsonify({"ok": True})


@bp.delete("/api/sessions/<sid>")
def revoke_session(sid):
    if not ID_RE.match(sid) or not _svc().accounts.revoke_session(g.user["id"], sid):
        return jsonify({"error": "Session not found."}), 404
    if sid == _current_session_id():
        session.clear()
    return jsonify({"ok": True, "signed_out": sid == _current_session_id()})


# ----------------------------------------------------------------------------- devices

@bp.get("/api/devices")
def list_devices():
    did = g.device["id"] if g.device else None
    return jsonify(_svc().accounts.list_devices(g.user["id"], did))


@bp.patch("/api/devices/<did>")
def patch_device(did):
    body = _body()
    unknown = set(body) - {"name", "type", "reviewed"}
    if unknown:
        raise AccountError(f"Unknown field: {sorted(unknown)[0]}")
    device = _svc().accounts.update_device(g.user["id"], did, name=body.get("name"), dtype=body.get("type"),
                                           reviewed=body.get("reviewed"))
    return jsonify(device)


@bp.delete("/api/devices/<did>")
def remove_device(did):
    if not _svc().accounts.revoke_device(g.user["id"], did):
        return jsonify({"error": "Device not found."}), 404
    mine = bool(g.device) and g.device["id"] == did
    if mine:
        session.clear()
    return jsonify({"ok": True, "signed_out": mine})


# ----------------------------------------------------------------------------- data: export and delete

@bp.get("/api/account/export")
def export_data():
    svc = _svc()
    uid = g.user["id"]
    export = {
        "exported": time.time(),
        "profile": g.user,
        "settings": svc.userdata.settings(uid).read(lambda d: json.loads(json.dumps(d))),
        "chats": svc.userdata.chats(uid).read(lambda d: json.loads(json.dumps(d["chats"]))),
        "devices": svc.accounts.list_devices(uid, g.device["id"] if g.device else None),
    }
    resp = Response(json.dumps(export, indent=2, ensure_ascii=False), mimetype="application/json")
    resp.headers["Content-Disposition"] = f'attachment; filename="compass-ai-{g.user["username"]}.json"'
    return resp


def _erase(uid: str) -> None:
    svc = _svc()
    svc.accounts.delete_user(uid)
    svc.userdata.delete(uid)
    svc.policy.usage.forget(uid)


@bp.post("/api/account/delete")
def delete_account():
    svc = _svc()
    body = _body()
    if body.get("username") != g.user["username"]:
        raise AccountError("Type your username exactly to confirm.", field="username")
    if not svc.accounts.verify_password(g.user["id"], body.get("password")):
        raise AccountError("That password isn't right.", 403, "password")
    if g.user["role"] == "owner" and len(svc.accounts.list_users()) > 1:
        raise AccountError("You own this host and other people use it. Remove their accounts first.", 409)
    _erase(g.user["id"])
    session.clear()
    return jsonify({"ok": True})


# ----------------------------------------------------------------------------- people (owner only)

@bp.get("/api/people")
@owner_only
def people():
    svc = _svc()
    today = svc.policy.usage.today()
    members = [{**m, "today": today.get(m["id"], 0)} for m in svc.accounts.list_users()]
    return jsonify({"registration": svc.accounts.registration_mode(), "members": members,
                    "invites": svc.accounts.list_invites(), "server": svc.config.server})


@bp.put("/api/people/registration")
@owner_only
def set_registration():
    return jsonify({"registration": _svc().accounts.set_registration_mode(_body().get("mode"))})


@bp.post("/api/people/invites")
@owner_only
def make_invite():
    invite, code = _svc().accounts.create_invite(g.user["id"])
    return jsonify({**invite, "code": code}), 201          # the code is shown once; only its hash is kept


@bp.delete("/api/people/invites/<iid>")
@owner_only
def drop_invite(iid):
    if not ID_RE.match(iid) or not _svc().accounts.revoke_invite(iid):
        return jsonify({"error": "Invite not found."}), 404
    return jsonify({"ok": True})


@bp.delete("/api/people/members/<uid>")
@owner_only
def remove_member(uid):
    if not ID_RE.match(uid) or uid == g.user["id"] or _svc().accounts.get_user(uid) is None:
        return jsonify({"error": "Member not found."}), 404
    _erase(uid)
    return jsonify({"ok": True})


@bp.put("/api/people/members/<uid>/suspended")
@owner_only
def suspend_member(uid):
    """Block someone from signing in (they are signed out at once) or let them back in. Their data is kept."""
    if not ID_RE.match(uid) or uid == g.user["id"] or _svc().accounts.get_user(uid) is None:
        return jsonify({"error": "Member not found."}), 404
    flag = _body().get("suspended")
    if not isinstance(flag, bool):
        raise AccountError("Say whether to suspend (true) or reactivate (false).")
    return jsonify(_svc().accounts.set_suspended(uid, flag))
