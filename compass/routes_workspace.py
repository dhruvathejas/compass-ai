"""The person's workspace folder and the Code button.

The Code button does exactly what typing ``cd workspace`` then ``code .`` in a terminal would: it starts VS Code
in the signed-in person's own workspace folder. Starting a program on the host PC is only ever allowed for
requests that come from that PC itself, never over the tunnel or the network, and the command is fixed (the
request carries no arguments), so there is nothing to inject.
"""
from __future__ import annotations

import shutil
import subprocess
import sys

from flask import Blueprint, current_app, g, jsonify

from . import security

bp = Blueprint("workspace", __name__)


def _svc():
    return current_app.extensions["compass"]


def find_code() -> str | None:
    return shutil.which("code")


@bp.get("/api/workspace")
def info():
    return jsonify({"path": str(_svc().userdata.workspace(g.user["id"])),
                    "local": security.is_local_request(),
                    "code_found": find_code() is not None})


@bp.post("/api/workspace/open-in-code")
def open_in_code():
    if _svc().config.server:
        return jsonify({"error": "The Code button isn't available on a shared server."}), 404
    if not security.is_local_request():
        return jsonify({"error": "The Code button only works when you open Compass AI on the computer that runs "
                                 "it (open it there at localhost), not over the network or the tunnel."}), 403
    exe = find_code()
    if exe is None:
        return jsonify({"error": "VS Code's `code` command wasn't found. In VS Code press Ctrl+Shift+P, run "
                                 "'Shell Command: Install code command in PATH', then try again."}), 404
    workspace = _svc().userdata.ensure_workspace(g.user["id"])
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW   # no console flash; outlives us
    try:
        subprocess.Popen([exe, "."], cwd=workspace, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, creationflags=flags)
    except OSError as exc:
        return jsonify({"error": f"Couldn't start VS Code: {exc.strerror or exc}"}), 500
    return jsonify({"ok": True, "path": str(workspace)})
