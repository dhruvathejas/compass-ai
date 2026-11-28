import json
import re
import unittest
from unittest import mock

from tests.helpers import AppTestCase, Browser

LAN = {"Host": "192.168.1.5:5000"}
TUNNEL = {"Cf-Connecting-Ip": "203.0.113.7", "X-Forwarded-Proto": "https"}


class WelcomeTests(AppTestCase):
    def test_a_first_visit_lands_on_the_welcome_page_with_the_three_paths(self):
        b = Browser(self.app)
        self.assertEqual(b.get("/").location, "/welcome")
        page = b.get("/welcome").get_data(as_text=True)
        self.assertIn("Welcome to Compass AI", page)
        self.assertIn("Your goal. Your route. Your decision.", page)
        for title, href in (("Install Compass AI", "/install"), ("I already have it installed", "/login"),
                            ("I have it running on my main PC", "/connect")):
            self.assertRegex(page, rf'(?s)<a class="path" href="{href}">.*?<h2>{title}</h2>', title)
        self.assertEqual(b.get("/").location, "/login")                # later visits go straight to sign-in

    def test_the_welcome_pages_are_reachable_when_signed_in_too(self):
        b = self.signed_in()
        for path in ("/welcome", "/install", "/connect"):
            self.assertEqual(b.get(path).status_code, 200, path)

    def test_install_page_is_honest_and_gives_real_commands(self):
        page = Browser(self.app).get("/install").get_data(as_text=True)
        self.assertIn("no packaged installer", page)
        self.assertIn("git clone https://github.com/dhruvathejas/compass-ai.git", page)
        self.assertIn("pip install -r requirements.txt", page)
        self.assertIn("never installs, starts or replaces Ollama", page)
        for os_id in ("os-windows", "os-mac", "os-linux"):
            self.assertIn(f'id="{os_id}"', page)

    def test_connect_page_gives_troubleshooting_and_may_probe_hosts_but_nothing_else_can(self):
        resp = Browser(self.app).get("/connect")
        page = resp.get_data(as_text=True)
        self.assertIn("localhost", page)
        self.assertIn("--lan", page)
        self.assertIn("connect-src 'self' http: https:", resp.headers["Content-Security-Policy"])
        for path in ("/welcome", "/install", "/login", "/register"):
            csp = Browser(self.app).get(path).headers["Content-Security-Policy"]
            self.assertIn("connect-src 'self';", csp, path)

    def test_requirements_file_exists_for_the_install_steps(self):
        from compass.config import ROOT
        self.assertIn("Flask", (ROOT / "requirements.txt").read_text(encoding="utf-8"))


class HostCheckTests(AppTestCase):
    def test_hello_is_public_cookie_free_and_read_only(self):
        b = Browser(self.app)
        resp = b.get("/api/hello")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"app": "compass-ai", "api": 1, "setup_needed": False})
        self.assertEqual(resp.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(resp.headers.getlist("Set-Cookie"), [])
        self.assertIn(b.request("POST", "/api/hello").status_code, (403, 405))      # read-only

    def test_hello_reveals_nothing_about_the_people_or_machine(self):
        raw = Browser(self.app).get("/api/hello").get_data(as_text=True)
        for secret in ("alice", self.alice["id"], "Alice", "memory", "workspace"):
            self.assertNotIn(secret, raw)

    def test_no_other_endpoint_is_cross_origin_readable(self):
        b = self.signed_in()
        for path in ("/api/account", "/api/chats", "/api/devices", "/api/settings", "/api/workspace"):
            self.assertNotIn("Access-Control-Allow-Origin", b.get(path).headers, path)

    def test_hello_over_the_tunnel_and_lan(self):
        self.assertEqual(Browser(self.app, TUNNEL).get("/api/hello").status_code, 200)
        self.assertEqual(Browser(self.app, LAN, addr="192.168.1.20").get("/api/hello").status_code, 200)
        self.assertEqual(Browser(self.app, {"Host": "10.9.9.9"}, addr="192.168.1.20").get("/api/hello").status_code, 400)

class FreshHostTests(AppTestCase):
    create_owner = False

    def test_welcome_offers_to_create_the_owner_account(self):
        page = Browser(self.app).get("/welcome").get_data(as_text=True)
        self.assertIn("Create the owner account", page)
        self.assertEqual(Browser(self.app).get("/install").status_code, 200)
        self.assertEqual(Browser(self.app).get("/connect").status_code, 200)

    def test_hello_says_the_host_needs_setup(self):
        self.assertTrue(Browser(self.app).get("/api/hello").get_json()["setup_needed"])


class ConnectionKindTests(AppTestCase):
    def test_the_account_api_reports_how_each_device_is_connected(self):
        local = self.signed_in()
        self.assertEqual(local.api("GET", "/api/account").get_json()["connection"]["kind"], "local")
        lan = self.signed_in(headers=LAN, addr="192.168.1.20")
        self.assertEqual(lan.api("GET", "/api/account").get_json()["connection"]["label"], "Local network")
        tunnel = self.signed_in(headers=TUNNEL)
        self.assertEqual(tunnel.api("GET", "/api/account").get_json()["connection"]["label"], "Internet (tunnel)")
        origins = {s["origin"] for s in local.api("GET", "/api/sessions").get_json()}
        self.assertEqual(origins, {"local", "lan", "tunnel"})


class CodeButtonTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.local = self.signed_in()

    def press(self, browser, exe="C:/fake/code.cmd"):
        with mock.patch("compass.routes_workspace.find_code", return_value=exe), \
             mock.patch("compass.routes_workspace.subprocess.Popen") as popen:
            resp = browser.api("POST", "/api/workspace/open-in-code")
        return resp, popen

    def test_runs_code_dot_inside_my_own_workspace(self):
        resp, popen = self.press(self.local)
        self.assertEqual(resp.status_code, 200)
        popen.assert_called_once()
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["C:/fake/code.cmd", "."])                          # fixed command, no user input
        self.assertEqual(kwargs["cwd"], self.svc.userdata.workspace(self.alice["id"]))
        self.assertTrue(kwargs["cwd"].is_dir())                                       # "cd workspace" works first time
        self.assertNotIn("shell", kwargs)
        self.assertEqual(resp.get_json()["path"], str(self.svc.userdata.workspace(self.alice["id"])))

    def test_each_person_gets_their_own_folder(self):
        bob = self.add_member()
        _, popen = self.press(self.signed_in("bob", "another long passphrase"))
        self.assertEqual(popen.call_args.kwargs["cwd"], self.svc.userdata.workspace(bob["id"]))
        self.assertNotEqual(popen.call_args.kwargs["cwd"], self.svc.userdata.workspace(self.alice["id"]))

    def test_request_data_cannot_change_what_runs(self):
        with mock.patch("compass.routes_workspace.find_code", return_value="C:/fake/code.cmd"), \
             mock.patch("compass.routes_workspace.subprocess.Popen") as popen:
            self.local.request("POST", "/api/workspace/open-in-code?path=C:/Windows&args=--evil",
                               headers={"X-CSRF-Token": self.local.csrf},
                               json={"cmd": "calc.exe", "path": "..\\..", "args": ["--evil"]})
        self.assertEqual(popen.call_args.args[0], ["C:/fake/code.cmd", "."])
        self.assertEqual(popen.call_args.kwargs["cwd"], self.svc.userdata.workspace(self.alice["id"]))

    def test_refused_over_the_tunnel_and_the_lan(self):
        for kwargs in ({"headers": TUNNEL}, {"headers": LAN, "addr": "192.168.1.20"}):
            remote = self.signed_in(**kwargs)
            resp, popen = self.press(remote)
            self.assertEqual(resp.status_code, 403, kwargs)
            popen.assert_not_called()
            self.assertIn("computer that runs it", resp.get_json()["error"])

    def test_clear_error_when_vs_code_is_not_installed(self):
        resp, popen = self.press(self.local, exe=None)
        self.assertEqual(resp.status_code, 404)
        self.assertIn("Install code command in PATH", resp.get_json()["error"])
        popen.assert_not_called()

    def test_clear_error_when_launching_fails(self):
        with mock.patch("compass.routes_workspace.find_code", return_value="C:/fake/code.cmd"), \
             mock.patch("compass.routes_workspace.subprocess.Popen", side_effect=OSError(2, "No such file")):
            resp = self.local.api("POST", "/api/workspace/open-in-code")
        self.assertEqual(resp.status_code, 500)
        self.assertIn("Couldn't start VS Code", resp.get_json()["error"])

    def test_needs_sign_in_and_an_authorized_device(self):
        anon = Browser(self.app)
        self.assertEqual(anon.api("POST", "/api/workspace/open-in-code").status_code, 403)   # no CSRF/session at all
        pending = Browser(self.app)
        pending.login(authorize=False)
        resp, popen = self.press(pending)
        self.assertEqual(resp.status_code, 403)
        popen.assert_not_called()

    def test_the_button_is_in_the_page_and_says_when_it_cannot_work(self):
        page = self.local.get("/").get_data(as_text=True)
        self.assertIn('id="open-code"', page)
        self.assertIn('data-local="yes"', page)
        remote = self.signed_in(headers=TUNNEL).get("/").get_data(as_text=True)
        self.assertIn('data-local="no"', remote)
        self.assertIn("Available when you use Compass AI on that PC", remote)

    def test_workspace_info(self):
        with mock.patch("compass.routes_workspace.find_code", return_value=None):
            info = self.local.api("GET", "/api/workspace").get_json()
        self.assertEqual((info["local"], info["code_found"]), (True, False))


if __name__ == "__main__":
    unittest.main()
