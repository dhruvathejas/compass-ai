"""Shared test helpers: a fake Ollama server and a signed-in test client."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from dataclasses import replace
from pathlib import Path

from compass import create_app
from compass.config import Config

PASSWORD = "correct horse battery"
BOB_PASSWORD = "another long passphrase"


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

    def __init__(self, app, headers: dict | None = None, addr: str | None = None, agent: str | None = None):
        self.app = app
        self.http = app.test_client()
        self.headers = dict(headers or {})
        if agent:
            self.headers["User-Agent"] = agent
        self.addr = addr
        self.csrf = ""

    def request(self, method, path, **kw):
        headers = {**self.headers, **kw.pop("headers", {})}
        if self.addr:
            kw.setdefault("environ_overrides", {"REMOTE_ADDR": self.addr})
        return self.http.open(path, method=method, headers=headers, **kw)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def form_token(self, path="/login") -> str:
        page = self.get(path).get_data(as_text=True)
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)

    def page_token(self, path="/") -> str:
        page = self.get(path).get_data(as_text=True)
        return re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)

    def login(self, username="alice", password=PASSWORD, authorize=True, device_name="Test PC", device_type="desktop"):
        token = self.form_token("/login")
        resp = self.request("POST", "/login", data={"username": username, "password": password, "csrf_token": token})
        if resp.status_code != 302:
            return resp
        home = self.get("/")
        if home.status_code == 302 and home.location == "/device":
            token = self.form_token("/device")
            if authorize:
                self.request("POST", "/device", data={"name": device_name, "type": device_type, "csrf_token": token})
            else:
                self.csrf = token
                return resp
        self.csrf = self.page_token("/")
        return resp

    def api(self, method, path, body=None, csrf=True, headers=None):
        h = dict(headers or {})
        if csrf:
            h["X-CSRF-Token"] = self.csrf
        kw = {"headers": h}
        if body is not None:
            kw["json"] = body
        return self.request(method, path, **kw)

    def cookie(self, name):
        cookie = self.http.get_cookie(name)
        return cookie.value if cookie else None


class AppTestCase(unittest.TestCase):
    """Fresh data folder, fake Ollama and an app whose owner is 'alice' for every test."""

    create_owner = True
    config_overrides: dict = {}            # e.g. {"mode": "server"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="compass-test-"))
        self.ollama = FakeOllama()
        self.config = Config(home=self.tmp / "memory", workspace=self.tmp / "workspace",
                             ollama_host=self.ollama.host, default_model="llama3.2:latest",
                             allowed_hosts=frozenset({"192.168.1.5"}))   # a pretend LAN address of this PC
        if self.config_overrides:
            self.config = replace(self.config, **self.config_overrides)
        self.app = create_app(self.config)
        self.svc = self.app.extensions["compass"]
        self.alice = None
        if self.create_owner:
            self.alice = self.svc.accounts.register("alice", "Alice", "alice@example.com", PASSWORD, is_local=True)
        self.browser = Browser(self.app)

    def tearDown(self):
        self.ollama.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add_member(self, username="bob", password=BOB_PASSWORD) -> dict:
        self.svc.accounts.set_registration_mode("open")
        user = self.svc.accounts.register(username, username.title(), "", password)
        self.svc.accounts.set_registration_mode("invite")
        return user

    def signed_in(self, username="alice", password=PASSWORD, **kw) -> Browser:
        b = Browser(self.app, **kw)
        self.assertEqual(b.login(username, password).status_code, 302)
        return b
