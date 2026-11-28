"""Server mode: what a member has left today, and the owner's controls for the free tier."""
from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, request

from .limits import LIMIT_FIELDS
from .routes_account import owner_only

bp = Blueprint("server", __name__)


def _svc():
    return current_app.extensions["compass"]


@bp.get("/api/usage")
def my_usage():
    """Free messages left today. ``limited`` is false on a personal host and for the owner of a server."""
    return jsonify(_svc().policy.usage_for(g.user))


@bp.get("/api/server")
@owner_only
def server_info():
    svc = _svc()
    if not svc.config.server:
        return jsonify({"error": "This host isn't running in server mode."}), 404
    today = svc.policy.usage.today()
    return jsonify({
        "limits": svc.policy.settings.get(),
        "fields": {k: {"label": label, "max": top} for k, (label, top) in LIMIT_FIELDS.items()},
        "today": {"messages": sum(today.values()), "people": len(today)},
        "accounts": svc.accounts.count_users(),
        "models": list(svc.config.models),
        "max_concurrent": svc.config.max_concurrent,
    })


@bp.put("/api/server/limits")
@owner_only
def set_limits():
    svc = _svc()
    if not svc.config.server:
        return jsonify({"error": "This host isn't running in server mode."}), 404
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body:
        return jsonify({"error": "Send the limits to change as a JSON object."}), 400
    try:
        return jsonify({"limits": svc.policy.settings.update(body)})
    except ValueError as err:
        return jsonify({"error": str(err)}), 400
