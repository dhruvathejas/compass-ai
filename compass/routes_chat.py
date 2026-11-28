"""Chat history API. Each person's chats live in memory/users/<id>/chats.json, rewritten on each message."""
from __future__ import annotations

import re
import time
import uuid

from flask import Blueprint, current_app, g, jsonify, request

from . import ollama
from .limits import PolicyError
from .prompts import BASE_SYSTEM_PROMPT

bp = Blueprint("chat", __name__)
CHAT_ID_RE = re.compile(r"^[0-9a-f]{12}$")
TITLE_MAX = 40


def _svc():
    return current_app.extensions["compass"]


def _chats():
    """The signed-in person's own chat file. There is no way to name anyone else's."""
    return _svc().userdata.chats(g.user["id"])


def _settings():
    return _svc().userdata.settings(g.user["id"])


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
    return jsonify(_chats().read(build))


def _policy_error(err: PolicyError):
    resp = jsonify({"error": str(err), "kind": err.kind})
    resp.status_code = err.status
    if err.retry_after:
        resp.headers["Retry-After"] = str(err.retry_after)
    return resp


@bp.post("/api/chats")
def create_chat():
    now = time.time()
    chat = {"id": uuid.uuid4().hex[:12], "title": "New chat", "created": now, "updated": now, "messages": []}
    with _chats().transaction() as d:
        try:
            _svc().policy.check_new_chat(g.user, len(d["chats"]))
        except PolicyError as err:
            return _policy_error(err)
        d["chats"].append(chat)
    return jsonify(chat), 201


@bp.get("/api/chats/<chat_id>")
def get_chat(chat_id):
    def build(d):
        chat = _find(d, chat_id)
        return None if chat is None else {**chat, "messages": [dict(m) for m in chat["messages"]]}
    chat = _chats().read(build)
    return jsonify(chat) if chat else _not_found()


@bp.delete("/api/chats/<chat_id>")
def delete_chat(chat_id):
    with _chats().transaction() as d:
        if _find(d, chat_id) is None:
            return _not_found()
        d["chats"] = [c for c in d["chats"] if c["id"] != chat_id]
    return "", 204


def _context(cfg, messages: list[dict]) -> list[dict]:
    """What the model sees. A shared server only sends the latest part of a long chat, so replies stay fast."""
    history = [{"role": m["role"], "content": m["content"]} for m in messages]
    if not cfg.server:
        return history
    history = history[-cfg.context_messages:]
    while len(history) > 1 and sum(len(m["content"]) for m in history) > cfg.context_chars:
        history.pop(0)
    return history


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

    try:
        ticket = svc.policy.admit(g.user)          # free-tier limits and capacity (does nothing on a personal host)
    except PolicyError as err:
        return _policy_error(err)
    try:
        return _converse(svc, chat_id, text, ticket)
    finally:
        ticket.release()


def _converse(svc, chat_id: str, text: str, ticket):
    model = svc.policy.pick_model(_settings().read(lambda d: d["model"]))

    # 1) save the user's message first, so it survives even if Ollama is down
    with _chats().transaction() as d:
        chat = _find(d, chat_id)
        if chat is None:
            ticket.refund()
            return _not_found()
        if svc.config.server and len(chat["messages"]) >= svc.config.max_chat_messages:
            ticket.refund()
            return jsonify({"error": "This chat is full. Start a new chat to keep going."}), 409
        if not chat["messages"]:
            chat["title"] = _title_from(text)
        chat["messages"].append({"role": "user", "content": text, "ts": time.time()})
        chat["updated"] = time.time()
        history = _context(svc.config, chat["messages"])
        summary = _summary(chat)

    # 2) ask the model without holding any lock (it can take a while)
    try:
        reply = ollama.chat(svc.config.ollama_host, model,
                            [{"role": "system", "content": BASE_SYSTEM_PROMPT}] + history,
                            timeout=svc.config.reply_timeout)
    except ollama.OllamaError as exc:
        ticket.refund()                            # no answer, so it doesn't count against today's messages
        return jsonify({"error": str(exc), "chat": summary}), 503

    # 3) save the reply (the chat may have been deleted while the model was thinking)
    with _chats().transaction() as d:
        chat = _find(d, chat_id)
        if chat is not None:
            chat["messages"].append({"role": "assistant", "content": reply, "ts": time.time(), "model": model})
            chat["updated"] = time.time()
            summary = _summary(chat)
        else:
            summary = None
    return jsonify({"reply": reply, "chat": summary, "usage": svc.policy.usage_for(g.user)})
