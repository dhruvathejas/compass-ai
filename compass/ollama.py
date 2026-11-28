"""Thin client for a local Ollama server (HTTP only: this app never installs or launches Ollama)."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,99}$")


class OllamaError(Exception):
    """A problem talking to Ollama, phrased so it can be shown to the user as-is."""


def valid_model_name(name: object) -> bool:
    return isinstance(name, str) and bool(MODEL_RE.match(name))


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        return str(json.load(exc).get("error", ""))
    except Exception:
        return ""


def list_models(host: str, timeout: float = 3) -> list[str]:
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise OllamaError(f"Ollama returned an error ({exc.code}).")
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        raise OllamaError(f"Can't reach Ollama at {host}. Is it running?")
    except ValueError:
        raise OllamaError("Ollama sent a response I couldn't read.")
    return sorted(m["name"] for m in body.get("models", []) if isinstance(m, dict) and "name" in m)


def chat(host: str, model: str, messages: list[dict], *, timeout: float = 300, options: dict | None = None) -> str:
    """Send ``messages`` (system prompt included) and return the full reply text."""
    payload = {"model": model, "messages": messages, "stream": False,
               "options": options or {"temperature": 0.6, "top_p": 0.9}}
    req = urllib.request.Request(f"{host}/api/chat", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc)
        if exc.code == 404:
            raise OllamaError(f"Ollama doesn't have the model '{model}'. {detail}".strip())
        raise OllamaError(f"Ollama returned an error ({exc.code}). {detail}".strip())
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
        raise OllamaError(f"Can't reach Ollama at {host}. Is it running?")
    except ValueError:
        raise OllamaError("Ollama sent a response I couldn't read.")
    reply = ((body.get("message") or {}).get("content") or "").strip()
    if not reply:
        raise OllamaError("Ollama sent an empty reply.")
    return reply
