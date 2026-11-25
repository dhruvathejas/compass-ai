"""Settings and model discovery."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from . import ollama

bp = Blueprint("settings", __name__)
THEMES = ("auto", "light", "dark")


def _svc():
    return current_app.extensions["compass"]


@bp.get("/api/settings")
def get_settings():
    return jsonify(_svc().settings.read(lambda d: {"model": d["model"], "theme": d["theme"]}))


@bp.put("/api/settings")
def put_settings():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Send a JSON object."}), 400
    unknown = set(payload) - {"model", "theme"}
    if unknown:
        return jsonify({"error": f"Unknown setting: {sorted(unknown)[0]}"}), 400
    changes = {}
    if "model" in payload:
        if not ollama.valid_model_name(payload["model"]):
            return jsonify({"error": "That is not a valid model name."}), 400
        changes["model"] = payload["model"]
    if "theme" in payload:
        if payload["theme"] not in THEMES:
            return jsonify({"error": "Theme must be auto, light or dark."}), 400
        changes["theme"] = payload["theme"]
    with _svc().settings.transaction() as d:
        d.update(changes)
        out = {"model": d["model"], "theme": d["theme"]}
    return jsonify(out)


@bp.get("/api/models")
def get_models():
    svc = _svc()
    current = svc.settings.read(lambda d: d["model"])
    try:
        models = ollama.list_models(svc.config.ollama_host)
        error = None
    except ollama.OllamaError as exc:
        models, error = [], str(exc)
    return jsonify({"models": models, "error": error, "current": current})
