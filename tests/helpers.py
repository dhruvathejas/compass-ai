"""Shared test helpers: a fake Ollama server and a signed-in test client."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from compass import create_app
from compass.config import Config

PASSWORD = "correct horse battery"


class FakeOllama:
    """Just enough of Ollama's HTTP API: /api/tags and /api/chat (non-streaming)."""

    def __init__(self):
        self.requests: list[dict] = []
        self.models = ["llama3.2:latest", "qwen2.5-coder:7b"]
        self.missing_model: str | None = None
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._json(200, {"models": [{"name": n} for n in outer.models]})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                if body["model"] == outer.missing_model:
                    return self._json(404, {"error": f"model '{body['model']}' not found"})
                last = body["messages"][-1]["content"]
                self._json(200, {"message": {"role": "assistant", "content": f"Echo: {last}"}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Browser:
    """A Flask test client that behaves like a browser: keeps cookies, remembers the CSRF token."""

    def __init__(self, app, headers: dict | None = None):
        self.app = app
        self.http = app.test_client()
        self.headers = headers or {}
        self.csrf = ""

    def request(self, method, path, **kw):
        headers = {**self.headers, **kw.pop("headers", {})}
        return self.http.open(path, method=method, headers=headers, **kw)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def login_form_token(self) -> str:
        page = self.get("/login").get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

    def login(self, password=PASSWORD):
        token = self.login_form_token()
        resp = self.request("POST", "/login", data={"password": password, "csrf_token": token})
        if resp.status_code == 302:
            page = self.get("/").get_data(as_text=True)
            self.csrf = re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)
        return resp

    def api(self, method, path, body=None, csrf=True, headers=None):
        h = dict(headers or {})
        if csrf:
            h["X-CSRF-Token"] = self.csrf
        kw = {"headers": h}
        if body is not None:
            kw["json"] = body
        return self.request(method, path, **kw)


class AppTestCase(unittest.TestCase):
    """Fresh data folder, fake Ollama and a password-protected app for every test."""

    set_password = True

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="compass-test-"))
        self.ollama = FakeOllama()
        self.config = Config(home=self.tmp / "memory", workspace=self.tmp / "workspace",
                             ollama_host=self.ollama.host, default_model="llama3.2:latest")
        self.app = create_app(self.config)
        self.svc = self.app.extensions["compass"]
        if self.set_password:
            self.svc.auth.set_password(PASSWORD)
        self.browser = Browser(self.app)

    def tearDown(self):
        self.ollama.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def signed_in(self) -> Browser:
        b = Browser(self.app)
        self.assertEqual(b.login().status_code, 302)
        return b
