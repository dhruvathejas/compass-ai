"""Chat history API. Every chat lives in memory/chats.json and is rewritten on each message."""
from __future__ import annotations

import re
import time
import uuid

from flask import Blueprint, current_app, jsonify, request

from . import ollama
from .prompts import BASE_SYSTEM_PROMPT

bp = Blueprint("chat", __name__)
CHAT_ID_RE = re.compile(r"^[0-9a-f]{12}$")
TITLE_MAX = 40


def _svc():
    return current_app.extensions["compass"]


def _find(data: dict, chat_id: str):
    if not CHAT_ID_RE.match(chat_id):
        return None
    return next((c for c in data["chats"] if c["id"] == chat_id), None)


def _summary(chat: dict) -> dict:
    return {"id": chat["id"], "title": chat["title"], "updated": chat["updated"]}


def _title_from(text: str) -> str:
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= TITLE_MAX else one_line[:TITLE_MAX].rstrip() + "…"


def _not_found():
    return jsonify({"error": "Chat not found."}), 404


@bp.get("/api/chats")
def list_chats():
    def build(d):
        chats = sorted(d["chats"], key=lambda c: c["updated"], reverse=True)
        return [_summary(c) for c in chats]
    return jsonify(_svc().chats.read(build))


@bp.post("/api/chats")
def create_chat():
    now = time.time()
    chat = {"id": uuid.uuid4().hex[:12], "title": "New chat", "created": now, "updated": now, "messages": []}
    with _svc().chats.transaction() as d:
        d["chats"].append(chat)
    return jsonify(chat), 201


@bp.get("/api/chats/<chat_id>")
def get_chat(chat_id):
    def build(d):
        chat = _find(d, chat_id)
        return None if chat is None else {**chat, "messages": [dict(m) for m in chat["messages"]]}
    chat = _svc().chats.read(build)
    return jsonify(chat) if chat else _not_found()


@bp.delete("/api/chats/<chat_id>")
def delete_chat(chat_id):
    with _svc().chats.transaction() as d:
        if _find(d, chat_id) is None:
            return _not_found()
        d["chats"] = [c for c in d["chats"] if c["id"] != chat_id]
    return "", 204


@bp.post("/api/chats/<chat_id>/message")
def send_message(chat_id):
    svc = _svc()
    payload = request.get_json(silent=True)
    text = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        return jsonify({"error": "Message is empty."}), 400
    text = text.strip()
    if len(text) > svc.config.max_message_chars:
        return jsonify({"error": f"Message is too long (limit {svc.config.max_message_chars:,} characters)."}), 413

    model = svc.settings.read(lambda d: d["model"])

    # 1) save the user's message first, so it survives even if Ollama is down
    with svc.chats.transaction() as d:
        chat = _find(d, chat_id)
        if chat is None:
            return _not_found()
        if not chat["messages"]:
            chat["title"] = _title_from(text)
        chat["messages"].append({"role": "user", "content": text, "ts": time.time()})
        chat["updated"] = time.time()
        history = [{"role": m["role"], "content": m["content"]} for m in chat["messages"]]
        summary = _summary(chat)

    # 2) ask the model without holding any lock (it can take a while)
    try:
        reply = ollama.chat(svc.config.ollama_host, model,
                            [{"role": "system", "content": BASE_SYSTEM_PROMPT}] + history)
    except ollama.OllamaError as exc:
        return jsonify({"error": str(exc), "chat": summary}), 503

    # 3) save the reply (the chat may have been deleted while the model was thinking)
    with svc.chats.transaction() as d:
        chat = _find(d, chat_id)
        if chat is not None:
            chat["messages"].append({"role": "assistant", "content": reply, "ts": time.time(), "model": model})
            chat["updated"] = time.time()
            summary = _summary(chat)
        else:
            summary = None
    return jsonify({"reply": reply, "chat": summary})
