import time
import unittest

from compass.auth import LoginThrottle
from tests.helpers import PASSWORD, AppTestCase, Browser

TUNNEL = {"Cf-Connecting-Ip": "203.0.113.7", "X-Forwarded-Proto": "https"}


class SetupStateTests(AppTestCase):
    set_password = False

    def test_locked_until_a_password_exists(self):
        self.assertEqual(self.browser.get("/").status_code, 503)
        self.assertIn(b"set-password", self.browser.get("/login").data)
        resp = self.browser.get("/api/chats")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("set-password", resp.get_json()["error"])

    def test_tunnel_visitors_cannot_claim_the_app(self):
        # there is no web route that sets a password, so there is nothing to claim
        remote = Browser(self.app, TUNNEL)
        resp = remote.request("POST", "/login", data={"password": "attacker-chosen-pass"})
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(self.svc.auth.has_password())

    def test_short_passwords_rejected(self):
        with self.assertRaises(ValueError):
            self.svc.auth.set_password("short")


class LoginTests(AppTestCase):
    def test_unauthenticated_requests_are_turned_away(self):
        resp = self.browser.get("/")
        self.assertEqual((resp.status_code, resp.location), (302, "/login"))
        resp = self.browser.get("/api/chats")
        self.assertEqual(resp.status_code, 401)

    def test_wrong_password(self):
        resp = self.browser.login("not the password")
        self.assertEqual(resp.status_code, 401)
        self.assertEqual(self.browser.get("/api/chats").status_code, 401)

    def test_right_password_opens_the_app(self):
        self.assertEqual(self.browser.login().status_code, 302)
        self.assertEqual(self.browser.get("/").status_code, 200)
        self.assertEqual(self.browser.get("/api/chats").status_code, 200)

    def test_login_needs_the_csrf_token(self):
        self.browser.get("/login")
        resp = self.browser.request("POST", "/login", data={"password": PASSWORD})
        self.assertEqual(resp.status_code, 403)
        resp = self.browser.request("POST", "/login", data={"password": PASSWORD, "csrf_token": "forged"})
        self.assertEqual(resp.status_code, 403)

    def test_password_is_hashed_on_disk(self):
        raw = (self.config.home / "auth.json").read_text(encoding="utf-8")
        self.assertNotIn(PASSWORD, raw)
        self.assertIn("scrypt" if "scrypt" in raw else '"hash"', raw)

    def test_logout_ends_the_session(self):
        b = self.signed_in()
        self.assertEqual(b.request("POST", "/logout", data={"csrf_token": b.csrf}).status_code, 302)
        self.assertEqual(b.get("/api/chats").status_code, 401)

    def test_session_changes_after_login(self):
        before = Browser(self.app)
        before.get("/login")
        with before.http.session_transaction() as s:
            pre = s["csrf"]
        before.login()
        with before.http.session_transaction() as s:
            self.assertNotEqual(s["csrf"], pre)      # fresh session material (no fixation)

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


class HostAndTunnelTests(AppTestCase):
    def test_unknown_host_without_proxy_is_refused(self):
        resp = self.browser.get("/login", headers={"Host": "evil.example"})
        self.assertEqual(resp.status_code, 400)

    def test_tunnel_requests_are_accepted_and_login_works(self):
        remote = Browser(self.app, {**TUNNEL, "Host": "random-words.trycloudflare.com"})
        self.assertEqual(remote.get("/login").status_code, 200)
        self.assertEqual(remote.login().status_code, 302)
        self.assertEqual(remote.get("/api/chats").status_code, 200)

    def test_cookie_flags(self):
        local = self.browser.get("/login")
        cookie = local.headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Secure", cookie)             # plain http on this PC

        remote = Browser(self.app, TUNNEL).get("/login")
        cookie = remote.headers["Set-Cookie"]
        self.assertIn("Secure", cookie)                # https through the tunnel
        self.assertIn("Strict-Transport-Security", remote.headers)

    def test_security_headers(self):
        resp = self.signed_in().get("/")
        csp = resp.headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("unsafe-inline", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(resp.headers["Cache-Control"], "no-store")

    def test_pages_work_without_inline_script_or_style(self):
        import re
        b = self.signed_in()
        for path in ("/", "/login"):
            html = b.get(path).get_data(as_text=True)
            self.assertFalse(re.search(r"<script(?![^>]*\bsrc=)", html), f"inline <script> on {path}")
            self.assertNotIn("<style", html)
            self.assertFalse(re.search(r"\sstyle=", html))
            self.assertFalse(re.search(r"\son[a-z]+=", html), f"inline event handler on {path}")


class SessionLifetimeTests(AppTestCase):
    def test_idle_sessions_expire(self):
        b = self.signed_in()
        with b.http.session_transaction() as s:
            s["last"] = time.time() - self.config.session_idle_seconds - 5
        self.assertEqual(b.get("/api/chats").status_code, 401)

    def test_sessions_have_an_absolute_limit(self):
        b = self.signed_in()
        with b.http.session_transaction() as s:
            s["iat"] = time.time() - self.config.session_max_seconds - 5
            s["last"] = time.time()
        self.assertEqual(b.get("/api/chats").status_code, 401)

    def test_changing_the_password_signs_everyone_out(self):
        a, b = self.signed_in(), self.signed_in()
        self.svc.auth.set_password("a brand new passphrase")
        self.assertEqual(a.get("/api/chats").status_code, 401)
        self.assertEqual(b.get("/api/chats").status_code, 401)

    def test_sign_out_everywhere(self):
        a, b = self.signed_in(), self.signed_in()
        self.assertEqual(a.api("POST", "/api/sessions/revoke").status_code, 200)
        self.assertEqual(a.get("/api/chats").status_code, 401)
        self.assertEqual(b.get("/api/chats").status_code, 401)
        self.assertEqual(b.login().status_code, 302)       # the password itself still works


class ThrottleTests(AppTestCase):
    def test_lockout_after_repeated_failures(self):
        remote = Browser(self.app, {"Cf-Connecting-Ip": "198.51.100.9"})
        for _ in range(5):
            self.assertEqual(remote.login("wrong password!").status_code, 401)
        resp = remote.login(PASSWORD)                    # even the right password is refused during lockout
        self.assertEqual(resp.status_code, 429)
        self.assertIn("Retry-After", resp.headers)

    def test_one_address_does_not_lock_out_another(self):
        for _ in range(6):
            Browser(self.app, {"Cf-Connecting-Ip": "198.51.100.9"}).login("wrong password!")
        other = Browser(self.app, {"Cf-Connecting-Ip": "198.51.100.10"})
        self.assertEqual(other.login().status_code, 302)

    def test_the_owner_at_the_pc_is_never_locked_out_by_the_internet(self):
        for i in range(40):
            Browser(self.app, {"Cf-Connecting-Ip": f"198.51.100.{i}"}).login("wrong password!")
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


if __name__ == "__main__":
    unittest.main()
