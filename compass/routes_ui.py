"""Pages."""
from __future__ import annotations

from flask import Blueprint, render_template

from .auth import csrf_token

bp = Blueprint("ui", __name__)


@bp.get("/")
def home():
    return render_template("index.html", csrf=csrf_token())
