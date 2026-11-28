import json
import re
import time
import unittest

from compass.security import LoginThrottle, RateLimiter
from tests.helpers import BOB_PASSWORD, PASSWORD, AppTestCase, Browser

TUNNEL = {"Cf-Connecting-Ip": "203.0.113.7", "X-Forwarded-Proto": "https"}


class SetupStateTests(AppTestCase):
    create_owner = False

    def test_locked_until_the_owner_exists(self):
        resp = self.browser.get("/")
        self.assertEqual((resp.status_code, resp.location), (302, "/welcome"))      # this PC: welcome first...
        self.browser.get("/welcome")
        self.assertEqual(self.browser.get("/").location, "/register")               # ...then create the owner
        resp = self.browser.get("/api/chats")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("no accounts yet", resp.get_json()["error"])

    def test_outside_visitors_see_setup_needed_and_cannot_claim_the_app(self):
        remote = Browser(self.app, TUNNEL)
        self.assertEqual(remote.get("/").status_code, 503)
        self.assertIn(b"Setup needed", remote.get("/register").data)
        self.assertEqual(remote.get("/register").status_code, 503)
        token = Browser(self.app).form_token("/register")           # a token from this PC is useless to them
        resp = remote.request("POST", "/register", data={"username": "mallory", "password": "attacker-chosen-pass",
                                                         "confirm": "attacker-chosen-pass", "csrf_token": token})
        self.assertEqual(resp.status_code, 403)            # refused (no CSRF token for them); nothing was created
        self.assertFalse(self.svc.accounts.has_users())

    def test_lan_visitors_cannot_claim_it_either(self):
        lan = Browser(self.app, addr="192.168.1.20", headers={"Host": "192.168.1.5:5000"})
        self.assertEqual(lan.get("/register").status_code, 503)
        self.assertFalse(self.svc.accounts.has_users())

    def test_this_pc_creates_the_owner_in_the_browser(self):
        b = self.browser
        token = b.form_token("/register")
        resp = b.request("POST", "/register", data={"display_name": "Dhruva", "username": "Dhruva", "email": "",
                                                    "password": PASSWORD, "confirm": PASSWORD, "csrf_token": token})
        self.assertEqual(resp.status_code, 302)
        owner = self.svc.accounts.find_user("dhruva")
        self.assertEqual(owner["role"], "owner")
        self.assertEqual(b.get("/").location, "/device")             # signed in, device still to authorize

    def test_login_with_no_accounts_points_to_registration(self):
        self.assertEqual(self.browser.get("/login").location, "/register")


class LoginTests(AppTestCase):
    def test_unauthenticated_requests_are_turned_away(self):
        self.assertEqual(self.browser.get("/api/chats").status_code, 401)
        first = self.browser.get("/")
        self.assertEqual((first.status_code, first.location), (302, "/welcome"))      # first visit
        self.browser.get("/login")                                                     # sets the "seen" cookie
        again = self.browser.get("/")
        self.assertEqual((again.status_code, again.location), (302, "/login"))

    def test_wrong_password_and_unknown_user_look_identical(self):
        a = self.browser.login("alice", "not the password")
        b = Browser(self.app).login("nobody", "not the password")
        self.assertEqual((a.status_code, b.status_code), (401, 401))
        self.assertEqual(re.findall(r'class="form-error"[^>]*>([^<]+)', a.get_data(as_text=True)),
                         re.findall(r'class="form-error"[^>]*>([^<]+)', b.get_data(as_text=True)))
        self.assertEqual(self.browser.get("/api/chats").status_code, 401)

    def test_username_is_case_insensitive_and_the_password_is_not(self):
        self.assertEqual(Browser(self.app).login("ALICE", PASSWORD).status_code, 302)
        self.assertEqual(Browser(self.app).login("alice", PASSWORD.upper()).status_code, 401)

    def test_right_password_opens_the_app(self):
        b = self.signed_in()
        self.assertEqual(b.get("/").status_code, 200)
        self.assertEqual(b.get("/api/chats").status_code, 200)

    def test_login_needs_the_csrf_token(self):
        self.browser.get("/login")
        resp = self.browser.request("POST", "/login", data={"username": "alice", "password": PASSWORD})
        self.assertEqual(resp.status_code, 403)
        resp = self.browser.request("POST", "/login", data={"username": "alice", "password": PASSWORD,
                                                             "csrf_token": "forged"})
        self.assertEqual(resp.status_code, 403)

    def test_passwords_are_hashed_and_nothing_secret_reaches_the_browser(self):
        raw = (self.config.home / "accounts.json").read_text(encoding="utf-8")
        self.assertNotIn(PASSWORD, raw)
        self.assertIn('"salt"', raw)
        self.assertEqual(json.loads(raw)["users"][self.alice["id"]]["password"]["n"], 2 ** 15)
        b = self.signed_in()
        secret = json.loads((self.config.home / "auth.json").read_text(encoding="utf-8"))["secret"]
        for path in ("/", "/account/profile", "/api/account", "/api/devices", "/api/sessions"):
            body = b.get(path).get_data(as_text=True)
            self.assertNotIn(secret, body)
            self.assertNotIn("token_hash", body)
            self.assertNotIn('"password"', body)

    def test_logout_ends_the_session_on_the_server(self):
        b = self.signed_in()
        stolen = b.cookie("compass_session")
        self.assertEqual(b.request("POST", "/logout", data={"csrf_token": b.csrf}).status_code, 302)
        self.assertEqual(b.get("/api/chats").status_code, 401)
        replay = Browser(self.app)                      # the old cookie must be dead, not merely forgotten
        replay.http.set_cookie("compass_session", stolen)
        self.assertEqual(replay.get("/api/chats").status_code, 401)

    def test_session_identifiers_change_after_login(self):
        before = Browser(self.app)
        before.get("/login")
        with before.http.session_transaction() as s:
            pre = s["csrf"]
        before.login()
        with before.http.session_transaction() as s:
            self.assertNotEqual(s["csrf"], pre)          # fresh session material (no fixation)

    def test_open_redirect_is_not_possible(self):
        b = self.signed_in()
        resp = b.request("POST", "/logout", data={"csrf_token": b.csrf, "next": "https://evil.example"})
        self.assertEqual(resp.location, "/login")


class CsrfAndOriginTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.b = self.signed_in()

    def test_state_changing_calls_need_the_token(self):
        self.assertEqual(self.b.api("POST", "/api/chats", csrf=False).status_code, 403)
        self.assertEqual(self.b.api("POST", "/api/chats", headers={"X-CSRF-Token": "wrong"}, csrf=False).status_code, 403)
        self.assertEqual(self.b.api("POST", "/api/chats").status_code, 201)

    def test_reads_do_not_need_the_token(self):
        self.assertEqual(self.b.api("GET", "/api/chats", csrf=False).status_code, 200)

    def test_cross_site_requests_are_blocked_even_with_the_token(self):
        for site in ("cross-site", "same-site"):
            resp = self.b.api("POST", "/api/chats", headers={"Sec-Fetch-Site": site})
            self.assertEqual(resp.status_code, 403, site)
        self.assertEqual(self.b.api("POST", "/api/chats", headers={"Sec-Fetch-Site": "same-origin"}).status_code, 201)

    def test_foreign_origin_header_is_blocked(self):
        resp = self.b.api("POST", "/api/chats", headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status_code, 403)
        resp = self.b.api("POST", "/api/chats", headers={"Origin": "http://localhost"})
        self.assertEqual(resp.status_code, 201)

    def test_every_account_and_device_mutation_is_csrf_protected(self):
        did = self.b.api("GET", "/api/devices").get_json()[0]["id"]
        for method, path, body in (("PATCH", "/api/account/profile", {"display_name": "x"}),
                                   ("POST", "/api/account/password", {"current": PASSWORD, "new": "x" * 12}),
                                   ("POST", "/api/account/delete", {"username": "alice", "password": PASSWORD}),
                                   ("PATCH", f"/api/devices/{did}", {"name": "x"}),
                                   ("DELETE", f"/api/devices/{did}", None),
                                   ("POST", "/api/sessions/revoke", None),
                                   ("PUT", "/api/people/registration", {"mode": "open"}),
                                   ("POST", "/api/people/invites", None),
                                   ("POST", "/api/workspace/open-in-code", None)):
            self.assertEqual(self.b.api(method, path, body, csrf=False).status_code, 403, path)
        self.assertEqual(self.b.api("GET", "/api/devices").status_code, 200)       # still alive: nothing happened


class HostAndTunnelTests(AppTestCase):
    def test_unknown_host_without_proxy_is_refused(self):
        resp = self.browser.get("/login", headers={"Host": "evil.example"})
        self.assertEqual(resp.status_code, 400)

    def test_lan_hosts_must_be_on_the_allow_list(self):
        self.assertEqual(Browser(self.app, addr="192.168.1.20", headers={"Host": "10.9.9.9:5000"}).get("/login").status_code, 400)

    def test_tunnel_requests_are_accepted_and_login_works(self):
        remote = Browser(self.app, {**TUNNEL, "Host": "random-words.trycloudflare.com"})
        self.assertEqual(remote.get("/login").status_code, 200)
        self.assertEqual(remote.login().status_code, 302)
        self.assertEqual(remote.get("/api/chats").status_code, 200)

    @staticmethod
    def cookie_header(resp, name):
        return next(c for c in resp.headers.getlist("Set-Cookie") if c.startswith(name + "="))

    def test_cookie_flags(self):
        local = self.browser.get("/login")
        cookie = self.cookie_header(local, "compass_session")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Secure", cookie)             # plain http on this PC

        remote = Browser(self.app, TUNNEL).get("/login")
        cookie = self.cookie_header(remote, "compass_session")
        self.assertIn("Secure", cookie)                # https through the tunnel
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Strict-Transport-Security", remote.headers)

    def test_security_headers(self):
        resp = self.signed_in().get("/")
        csp = resp.headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("connect-src 'self';", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")

    def test_pages_work_without_inline_script_or_style(self):
        b = self.signed_in()
        pages = ["/", "/welcome", "/install", "/connect", "/account/profile", "/account/security",
                 "/account/devices", "/account/privacy", "/account/people"]
        anon = Browser(self.app)
        for path in pages + ["/login", "/register"]:
            html = (b if path in pages and path not in ("/welcome", "/install", "/connect") else anon).get(path)
            html = html.get_data(as_text=True)
            self.assertIn("<html", html, path)
            self.assertFalse(re.search(r"<script(?![^>]*\bsrc=)", html), f"inline <script> on {path}")
            self.assertNotIn("<style", html)
            self.assertFalse(re.search(r"\sstyle=", html), f"inline style on {path}")
            self.assertFalse(re.search(r"\son[a-z]+=", html), f"inline event handler on {path}")


class SessionLifetimeTests(AppTestCase):
    def shift(self, seconds):
        base = time.time()
        self.svc.accounts.clock = lambda: base + seconds

    def test_idle_sessions_expire(self):
        b = self.signed_in()
        self.shift(self.config.session_idle_seconds + 5)
        self.assertEqual(b.get("/api/chats").status_code, 401)

    def test_activity_keeps_a_session_alive_but_there_is_an_absolute_limit(self):
        b = self.signed_in()
        step = self.config.session_idle_seconds - 100
        for hop in (1, 2, 3):
            self.shift(step * hop)
            self.assertEqual(b.get("/api/chats").status_code, 200, hop)    # each visit renews the idle window
        self.shift(self.config.session_max_seconds + 5)
        self.assertEqual(b.get("/api/chats").status_code, 401)

    def test_changing_the_password_signs_out_other_browsers_but_not_this_one(self):
        a, b = self.signed_in(), self.signed_in()
        resp = a.api("POST", "/api/account/password", {"current": PASSWORD, "new": "a brand new passphrase"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(a.get("/api/chats").status_code, 200)
        self.assertEqual(b.get("/api/chats").status_code, 401)
        self.assertEqual(Browser(self.app).login("alice", PASSWORD).status_code, 401)
        self.assertEqual(Browser(self.app).login("alice", "a brand new passphrase").status_code, 302)

    def test_sign_out_everywhere_ends_every_session_but_keeps_devices(self):
        a, b = self.signed_in(), self.signed_in()
        self.assertEqual(a.api("POST", "/api/sessions/revoke").status_code, 200)
        self.assertEqual(a.get("/api/chats").status_code, 401)
        self.assertEqual(b.get("/api/chats").status_code, 401)
        self.assertEqual(len(self.svc.accounts.list_devices(self.alice["id"], None)), 2)
        self.assertEqual(b.login().status_code, 302)       # the password itself still works

    def test_a_single_session_can_be_ended(self):
        a, b = self.signed_in(), self.signed_in()
        rows = a.api("GET", "/api/sessions").get_json()
        self.assertEqual(len(rows), 2)
        other = next(r for r in rows if not r["current"])
        self.assertEqual(a.api("DELETE", f"/api/sessions/{other['id']}").status_code, 200)
        self.assertEqual(b.get("/api/chats").status_code, 401)
        self.assertEqual(a.get("/api/chats").status_code, 200)

    def test_deleting_an_account_kills_its_sessions(self):
        bob = self.add_member()
        b = self.signed_in("bob", BOB_PASSWORD)
        self.svc.accounts.delete_user(bob["id"])
        self.assertEqual(b.get("/api/chats").status_code, 401)


class ThrottleTests(AppTestCase):
    def test_lockout_after_repeated_failures(self):
        remote = Browser(self.app, {"Cf-Connecting-Ip": "198.51.100.9"})
        for _ in range(5):
            self.assertEqual(remote.login("alice", "wrong password!").status_code, 401)
        resp = remote.login("alice", PASSWORD)           # even the right password is refused during lockout
        self.assertEqual(resp.status_code, 429)
        self.assertIn("Retry-After", resp.headers)

    def test_one_address_does_not_lock_out_another(self):
        for _ in range(6):
            Browser(self.app, {"Cf-Connecting-Ip": "198.51.100.9"}).login("alice", "wrong password!")
        other = Browser(self.app, {"Cf-Connecting-Ip": "198.51.100.10"})
        self.assertEqual(other.login().status_code, 302)

    def test_lan_addresses_are_throttled_individually(self):
        host = {"Host": "192.168.1.5:5000"}
        bad = Browser(self.app, host, addr="192.168.1.20")
        for _ in range(5):
            bad.login("alice", "wrong password!")
        self.assertEqual(bad.login().status_code, 429)
        self.assertEqual(Browser(self.app, host, addr="192.168.1.21").login().status_code, 302)

    def test_the_owner_at_the_pc_is_never_locked_out_by_the_internet(self):
        for i in range(40):
            Browser(self.app, {"Cf-Connecting-Ip": f"198.51.100.{i}"}).login("alice", "wrong password!")
        self.assertEqual(self.signed_in().get("/api/chats").status_code, 200)

    def test_throttle_unit(self):
        now = [0.0]
        t = LoginThrottle(clock=lambda: now[0])
        for _ in range(4):
            t.failed("k")
        self.assertEqual(t.wait_seconds("k"), 0)
        t.failed("k")
        first = t.wait_seconds("k")
        self.assertGreater(first, 0)
        now[0] += first + 1
        self.assertEqual(t.wait_seconds("k"), 0)
        t.failed("k")
        self.assertGreater(t.wait_seconds("k"), first)   # back-off grows
        t.succeeded("k")
        self.assertEqual(t.wait_seconds("k"), 0)

    def test_rate_limiter_unit(self):
        now = [0.0]
        r = RateLimiter(2, 60, clock=lambda: now[0])
        self.assertEqual((r.hit("a"), r.hit("a")), (0, 0))
        self.assertGreater(r.hit("a"), 0)
        self.assertEqual(r.hit("b"), 0)
        now[0] += 61
        self.assertEqual(r.hit("a"), 0)


if __name__ == "__main__":
    unittest.main()
