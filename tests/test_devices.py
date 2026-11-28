import time
import unittest

from compass.accounts import MAX_DEVICES, AccountError, describe_agent, guess_device_type
from tests.helpers import BOB_PASSWORD, PASSWORD, AppTestCase, Browser

PHONE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
            "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
EDGE_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0")


class AuthorizeFlowTests(AppTestCase):
    def pending(self, **kw):
        b = Browser(self.app, **kw)
        self.assertEqual(b.login(authorize=False).status_code, 302)
        return b

    def test_a_new_browser_must_authorize_before_anything_else(self):
        b = self.pending()
        home = b.get("/")
        self.assertEqual((home.status_code, home.location), (302, "/device"))
        api = b.get("/api/chats")
        self.assertEqual(api.status_code, 403)
        self.assertEqual(api.get_json()["code"], "device_required")
        self.assertEqual(b.api("POST", "/api/chats").status_code, 403)
        self.assertEqual(b.get("/api/devices").status_code, 403)
        self.assertEqual(b.get("/api/account").status_code, 403)
        self.assertEqual(b.get("/account/devices").location, "/device")
        self.assertEqual(b.get("/device").status_code, 200)

    def test_authorizing_registers_the_device_and_sets_a_protected_cookie(self):
        b = self.pending(agent=EDGE_UA)
        page = b.get("/device").get_data(as_text=True)
        self.assertIn("Edge on Windows", page)                       # suggested name
        self.assertIn('value="desktop" checked', page)
        resp = b.request("POST", "/device", data={"name": "Studio PC", "type": "laptop", "csrf_token": b.form_token("/device")})
        self.assertEqual(resp.location, "/")
        cookie = next(c for c in resp.headers.getlist("Set-Cookie") if c.startswith(f"compass_dev_{self.alice['id']}="))
        for flag in ("HttpOnly", "SameSite=Strict", "Max-Age=31536000", "Path=/"):
            self.assertIn(flag, cookie)
        b.csrf = b.page_token("/")
        devices = b.api("GET", "/api/devices").get_json()
        self.assertEqual(len(devices), 1)
        self.assertEqual((devices[0]["name"], devices[0]["type"], devices[0]["current"]), ("Studio PC", "laptop", True))
        self.assertTrue(devices[0]["reviewed"])                      # an account's first device is trusted
        self.assertEqual(b.get("/api/chats").status_code, 200)

    def test_the_device_secret_is_only_stored_hashed(self):
        b = self.signed_in()
        credential = b.cookie(f"compass_dev_{self.alice['id']}")
        did, _, token = credential.partition(".")
        self.assertEqual(len(did), 16)
        raw = (self.config.home / "accounts.json").read_text(encoding="utf-8")
        self.assertNotIn(token, raw)
        self.assertIn(did, raw)

    def test_a_recognized_browser_skips_authorization_next_time(self):
        b = self.signed_in()
        b.request("POST", "/logout", data={"csrf_token": b.csrf})
        resp = b.login()
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(b.get("/").status_code, 200)                # straight in, no /device step
        self.assertEqual(len(b.api("GET", "/api/devices").get_json()), 1)

    def test_authorize_validates_input(self):
        b = self.pending()
        token = b.form_token("/device")
        for name, kind in (("", "desktop"), ("x" * 41, "desktop"), ("ok", "toaster"), ("bad\x00name", "desktop"), ("   ", "phone")):
            resp = b.request("POST", "/device", data={"name": name, "type": kind, "csrf_token": token})
            self.assertEqual(resp.status_code, 400, (name, kind))
        self.assertEqual(self.svc.accounts.list_devices(self.alice["id"], None), [])

    def test_authorizing_needs_csrf_and_a_signed_in_session(self):
        b = self.pending()
        self.assertEqual(b.request("POST", "/device", data={"name": "x", "type": "phone"}).status_code, 403)
        anon = Browser(self.app)
        token = anon.form_token("/login")
        resp = anon.request("POST", "/device", data={"name": "x", "type": "phone", "csrf_token": token})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.location, "/login")

    def test_the_device_cap(self):
        for i in range(MAX_DEVICES):
            self.svc.accounts.create_device(self.alice["id"], f"d{i}", "phone", "x")
        with self.assertRaises(AccountError):
            self.svc.accounts.create_device(self.alice["id"], "one too many", "phone", "x")

    def test_agent_helpers(self):
        self.assertEqual(describe_agent(EDGE_UA), "Edge on Windows")
        self.assertEqual(describe_agent(PHONE_UA), "Safari on iPhone")
        self.assertEqual(guess_device_type(PHONE_UA), "phone")
        self.assertEqual(guess_device_type("Mozilla/5.0 (Linux; Android 13; Pixel Tablet) Chrome/120"), "tablet")
        self.assertEqual(guess_device_type(EDGE_UA), "desktop")
        self.assertEqual(describe_agent(""), "Browser")


class DeviceCredentialTests(AppTestCase):
    def test_a_device_credential_alone_grants_nothing(self):
        owner = self.signed_in()
        credential = owner.cookie(f"compass_dev_{self.alice['id']}")
        thief = Browser(self.app)
        thief.http.set_cookie(f"compass_dev_{self.alice['id']}", credential)
        self.assertEqual(thief.get("/api/chats").status_code, 401)
        self.assertEqual(thief.get("/api/devices").status_code, 401)
        self.assertEqual(thief.get("/").status_code, 302)
        self.assertEqual(thief.get("/").location, "/welcome")
        self.assertEqual(thief.login(password="wrong password").status_code, 401)   # still needs the password

    def test_a_guessed_or_half_right_credential_is_not_recognized(self):
        owner = self.signed_in()
        did, _, token = owner.cookie(f"compass_dev_{self.alice['id']}").partition(".")
        for forged in (f"{did}.{'A' * len(token)}", f"{did}.", did, ".x", "zz.zz", f"{did}.{token}x", "../../etc.passwd"):
            b = Browser(self.app)
            b.http.set_cookie(f"compass_dev_{self.alice['id']}", forged)
            b.login(authorize=False)
            self.assertEqual(b.get("/").location, "/device", forged)               # treated as an unknown device

    def test_a_device_cookie_belongs_to_one_account(self):
        bob = self.add_member()
        owner = self.signed_in()
        alice_cred = owner.cookie(f"compass_dev_{self.alice['id']}")
        b = Browser(self.app)
        b.http.set_cookie(f"compass_dev_{bob['id']}", alice_cred)                   # alice's device presented as bob's
        b.login("bob", BOB_PASSWORD, authorize=False)
        self.assertEqual(b.get("/").location, "/device")

    def test_two_people_can_share_a_browser(self):
        self.add_member()
        b = Browser(self.app)
        b.login("alice", PASSWORD)
        b.request("POST", "/logout", data={"csrf_token": b.csrf})
        b.login("bob", BOB_PASSWORD)
        b.request("POST", "/logout", data={"csrf_token": b.csrf})
        b.login("alice", PASSWORD)
        self.assertEqual(b.get("/").status_code, 200)                              # alice is still recognized


class DeviceManagementTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.a = Browser(self.app, agent=EDGE_UA)
        self.a.login(device_name="Desk")
        self.phone = Browser(self.app, agent=PHONE_UA)
        self.phone.login(device_name="Pocket", device_type="phone")

    def listing(self, b=None):
        return (b or self.a).api("GET", "/api/devices").get_json()

    def test_listing_marks_the_current_device_and_unfamiliar_ones(self):
        rows = self.listing()
        self.assertEqual([r["name"] for r in rows], ["Desk", "Pocket"])               # current first
        self.assertEqual([r["current"] for r in rows], [True, False])
        self.assertEqual([r["reviewed"] for r in rows], [True, False])               # the second device needs review
        self.assertTrue(all(r["signed_in"] and r["connected"] for r in rows))
        for r in rows:
            self.assertEqual(set(r), {"id", "name", "type", "created", "last_active", "reviewed", "agent",
                                      "current", "signed_in", "connected"})
        self.assertEqual(rows[1]["agent"], "Safari on iPhone")

    def test_rename_retype_and_review(self):
        phone_id = self.listing()[1]["id"]
        resp = self.a.api("PATCH", f"/api/devices/{phone_id}", {"name": "  My   phone ", "type": "tablet", "reviewed": True})
        self.assertEqual((resp.get_json()["name"], resp.get_json()["type"], resp.get_json()["reviewed"]), ("My phone", "tablet", True))
        for body in ({"name": ""}, {"name": "x" * 41}, {"type": "fridge"}, {"uid": "1"}, {"token_hash": "x"}):
            self.assertEqual(self.a.api("PATCH", f"/api/devices/{phone_id}", body).status_code, 400, body)
        self.assertEqual(self.listing()[1]["name"], "My phone")

    def test_removing_a_device_cuts_it_off_immediately(self):
        phone_id = self.listing()[1]["id"]
        self.assertEqual(self.phone.get("/api/chats").status_code, 200)
        resp = self.a.api("DELETE", f"/api/devices/{phone_id}")
        self.assertEqual((resp.status_code, resp.get_json()["signed_out"]), (200, False))
        self.assertEqual(self.phone.get("/api/chats").status_code, 401)               # its session died with it
        self.assertEqual(len(self.listing()), 1)
        self.assertEqual(self.a.api("DELETE", f"/api/devices/{phone_id}").status_code, 404)

    def test_a_removed_device_must_authorize_again_and_its_old_secret_is_dead(self):
        phone_id = self.listing()[1]["id"]
        old = self.phone.cookie(f"compass_dev_{self.alice['id']}")
        self.a.api("DELETE", f"/api/devices/{phone_id}")
        self.phone.login(authorize=False)
        self.assertEqual(self.phone.get("/").location, "/device")                     # no longer recognized
        self.assertIsNone(self.svc.accounts.verify_device(self.alice["id"], old))

    def test_removing_your_own_device_signs_you_out(self):
        mine = self.listing()[0]["id"]
        resp = self.a.api("DELETE", f"/api/devices/{mine}")
        self.assertTrue(resp.get_json()["signed_out"])
        self.assertEqual(self.a.get("/api/chats").status_code, 401)
        self.assertEqual(self.phone.get("/api/chats").status_code, 200)

    def test_last_active_follows_use(self):
        base = time.time()
        self.svc.accounts.clock = lambda: base + 3 * 3600
        self.a.get("/api/chats")
        rows = {r["name"]: r for r in self.listing()}
        self.assertGreater(rows["Desk"]["last_active"], base + 3 * 3600 - 5)
        self.assertLess(rows["Pocket"]["last_active"], base + 60)
        self.assertTrue(rows["Desk"]["connected"])
        self.assertFalse(rows["Pocket"]["connected"])

    def test_a_device_with_no_session_reads_as_signed_out(self):
        self.phone.request("POST", "/logout", data={"csrf_token": self.phone.csrf})
        row = next(r for r in self.listing() if r["name"] == "Pocket")
        self.assertEqual((row["signed_in"], row["connected"]), (False, False))

    def test_malformed_device_ids_are_plain_404s(self):
        for bad in ("nope", "../../x", "0" * 16, "G" * 16):
            self.assertEqual(self.a.api("PATCH", f"/api/devices/{bad}", {"name": "x"}).status_code, 404, bad)
            self.assertEqual(self.a.api("DELETE", f"/api/devices/{bad}").status_code, 404, bad)


if __name__ == "__main__":
    unittest.main()
