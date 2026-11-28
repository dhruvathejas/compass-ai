import json
import time
import unittest

from compass.accounts import AccountError
from tests.helpers import BOB_PASSWORD, PASSWORD, AppTestCase, Browser


def register_via_web(app, username, password, invite="", headers=None, **extra):
    b = Browser(app, headers)
    token = b.form_token("/register") if b.get("/register").status_code == 200 and b"csrf_token" in b.get("/register").data         else b.form_token("/login")
    data = {"username": username, "display_name": extra.pop("display_name", ""), "email": extra.pop("email", ""),
            "password": password, "confirm": extra.pop("confirm", password), "invite": invite, "csrf_token": token,
            **extra}
    return b, b.request("POST", "/register", data=data)


class RegistrationRulesTests(AppTestCase):
    def test_the_first_account_is_the_owner_and_only_from_this_pc(self):
        self.assertEqual(self.alice["role"], "owner")
        from compass import create_app
        from compass.config import Config
        app = create_app(Config(home=self.tmp / "other", workspace=self.tmp / "ws", ollama_host=self.ollama.host,
                                default_model="m"))
        with self.assertRaises(AccountError) as cm:
            app.extensions["compass"].accounts.register("mallory", "", "", PASSWORD, is_local=False)
        self.assertEqual(cm.exception.status, 403)
        self.assertFalse(app.extensions["compass"].accounts.has_users())

    def test_invite_only_by_default(self):
        self.assertEqual(self.svc.accounts.registration_mode(), "invite")
        _, resp = register_via_web(self.app, "bob", BOB_PASSWORD)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("invite code", resp.get_data(as_text=True))
        self.assertIsNone(self.svc.accounts.find_user("bob"))

    def test_an_invite_works_once_and_expires(self):
        owner = self.signed_in()
        code = owner.api("POST", "/api/people/invites").get_json()["code"]
        self.assertRegex(code, r"^CMP-[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}$")
        _, resp = register_via_web(self.app, "bob", BOB_PASSWORD, invite=code.lower())     # case and spacing don't matter
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.svc.accounts.find_user("bob")["role"], "member")
        _, again = register_via_web(self.app, "carol", BOB_PASSWORD, invite=code)
        self.assertEqual(again.status_code, 403)                                           # single use

        late = owner.api("POST", "/api/people/invites").get_json()["code"]
        base = time.time()
        self.svc.accounts.clock = lambda: base + 8 * 86400
        _, expired = register_via_web(self.app, "dave", BOB_PASSWORD, invite=late)
        self.assertEqual(expired.status_code, 403)

    def test_invites_are_stored_hashed_and_a_failed_signup_does_not_burn_the_invite(self):
        owner = self.signed_in()
        code = owner.api("POST", "/api/people/invites").get_json()["code"]
        self.assertNotIn(code, (self.config.home / "accounts.json").read_text(encoding="utf-8"))
        _, taken = register_via_web(self.app, "alice", BOB_PASSWORD, invite=code)          # username already used
        self.assertEqual(taken.status_code, 409)
        _, ok = register_via_web(self.app, "bob", BOB_PASSWORD, invite=code)
        self.assertEqual(ok.status_code, 302)

    def test_open_and_closed_modes(self):
        owner = self.signed_in()
        self.assertEqual(owner.api("PUT", "/api/people/registration", {"mode": "open"}).status_code, 200)
        _, resp = register_via_web(self.app, "bob", BOB_PASSWORD)
        self.assertEqual(resp.status_code, 302)
        owner.api("PUT", "/api/people/registration", {"mode": "closed"})
        _, resp = register_via_web(self.app, "carol", BOB_PASSWORD)
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b"closed", Browser(self.app).get("/register").data)
        self.assertEqual(owner.api("PUT", "/api/people/registration", {"mode": "anything"}).status_code, 400)

    def test_registering_cannot_grant_a_role(self):
        self.svc.accounts.set_registration_mode("open")
        _, resp = register_via_web(self.app, "mallory", BOB_PASSWORD, role="owner", uid="1")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.svc.accounts.find_user("mallory")["role"], "member")

    def test_signups_from_one_outside_address_are_rate_limited(self):
        self.svc.accounts.set_registration_mode("open")
        net = {"Cf-Connecting-Ip": "198.51.100.77"}
        statuses = [register_via_web(self.app, f"user{i}", BOB_PASSWORD, headers=net)[1].status_code for i in range(7)]
        self.assertEqual(statuses, [302] * 5 + [429, 429])
        other = register_via_web(self.app, "elsewhere", BOB_PASSWORD, headers={"Cf-Connecting-Ip": "198.51.100.78"})[1]
        self.assertEqual(other.status_code, 302)

    def test_guessing_invite_codes_is_rate_limited(self):
        net = {"Cf-Connecting-Ip": "198.51.100.77"}
        statuses = [register_via_web(self.app, "guesser", BOB_PASSWORD, invite=f"CMP-AAAA-BBBB-{i:04d}", headers=net)[1].status_code
                    for i in range(7)]
        self.assertEqual(statuses, [403] * 5 + [429, 429])

    def test_typos_do_not_count_against_the_limit(self):
        net = {"Cf-Connecting-Ip": "198.51.100.77"}
        self.svc.accounts.set_registration_mode("open")
        for _ in range(8):
            self.assertEqual(register_via_web(self.app, "ab", BOB_PASSWORD, headers=net)[1].status_code, 400)
        self.assertEqual(register_via_web(self.app, "fine_name", BOB_PASSWORD, headers=net)[1].status_code, 302)

    def test_field_validation(self):
        self.svc.accounts.set_registration_mode("open")
        bad = [("ab", BOB_PASSWORD, {}), ("1abc", BOB_PASSWORD, {}), ("has space", BOB_PASSWORD, {}),
               ("x" * 40, BOB_PASSWORD, {}), ("../etc", BOB_PASSWORD, {}), ("fine_name", "short", {}),
               ("fine_name", BOB_PASSWORD, {"email": "not-an-email"}),
               ("fine_name", BOB_PASSWORD, {"display_name": "x" * 61}),
               ("fine_name", BOB_PASSWORD, {"confirm": "different"}), ("fine_name", "fine_name", {})]
        for username, password, extra in bad:
            _, resp = register_via_web(self.app, username, password, **extra)
            self.assertEqual(resp.status_code, 400, (username, password, extra))
        self.assertEqual(len(self.svc.accounts.list_users()), 1)

    def test_usernames_are_unique_regardless_of_case(self):
        self.svc.accounts.set_registration_mode("open")
        _, resp = register_via_web(self.app, "ALICE", BOB_PASSWORD)
        self.assertEqual(resp.status_code, 409)

    def test_form_values_are_escaped_when_the_page_is_shown_again(self):
        self.svc.accounts.set_registration_mode("open")
        _, resp = register_via_web(self.app, "ok_name", "short", display_name='"><script>alert(1)</script>')
        html = resp.get_data(as_text=True)
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;", html)


class ProfileAndPasswordTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.b = self.signed_in()

    def test_profile_update(self):
        resp = self.b.api("PATCH", "/api/account/profile", {"display_name": "  Alice  A ", "email": "new@example.com"})
        self.assertEqual(resp.get_json()["display_name"], "Alice A")
        self.assertEqual(self.svc.accounts.get_user(self.alice["id"])["email"], "new@example.com")
        self.assertEqual(self.b.api("PATCH", "/api/account/profile", {"email": ""}).status_code, 200)

    def test_profile_validation_and_no_mass_assignment(self):
        for body in ({"display_name": ""}, {"display_name": "x" * 61}, {"email": "nope"}, {"email": 5},
                     {"role": "owner"}, {"username": "other"}, {"id": "abc"}, {"password": "x"}):
            self.assertEqual(self.b.api("PATCH", "/api/account/profile", body).status_code, 400, body)
        self.assertEqual(self.b.api("PATCH", "/api/account/profile", [1]).status_code, 400)
        user = self.svc.accounts.get_user(self.alice["id"])
        self.assertEqual((user["username"], user["role"]), ("alice", "owner"))

    def test_password_change_rules(self):
        change = lambda cur, new: self.b.api("POST", "/api/account/password", {"current": cur, "new": new})  # noqa: E731
        self.assertEqual(change("wrong", "a brand new passphrase").status_code, 403)
        self.assertEqual(change(PASSWORD, "short").status_code, 400)
        self.assertEqual(change(PASSWORD, "alice").status_code, 400)
        self.assertEqual(change(PASSWORD, None).status_code, 400)
        self.assertEqual(change(PASSWORD, "a brand new passphrase").status_code, 200)

    def test_export_contains_only_my_data_and_no_secrets(self):
        self.b.api("POST", "/api/chats")
        bob = self.add_member()
        self.signed_in("bob", BOB_PASSWORD).api("POST", "/api/chats")
        resp = self.b.get("/api/account/export")
        self.assertIn("attachment", resp.headers["Content-Disposition"])
        data = resp.get_json()
        self.assertEqual(data["profile"]["username"], "alice")
        self.assertEqual(len(data["chats"]), 1)
        raw = resp.get_data(as_text=True)
        for forbidden in ("token_hash", "salt", bob["id"], "bob"):
            self.assertNotIn(forbidden, raw)


class DeleteAccountTests(AppTestCase):
    def test_deleting_an_account_erases_everything_and_frees_the_username(self):
        bob = self.add_member()
        b = self.signed_in("bob", BOB_PASSWORD)
        b.api("POST", "/api/chats")
        workspace = self.svc.userdata.ensure_workspace(bob["id"])
        (workspace / "notes.txt").write_text("hi", encoding="utf-8")
        home = self.svc.userdata.home(bob["id"])
        self.assertTrue(home.exists())

        body = {"username": "bob", "password": BOB_PASSWORD}
        self.assertEqual(b.api("POST", "/api/account/delete", {**body, "password": "wrong"}).status_code, 403)
        self.assertEqual(b.api("POST", "/api/account/delete", {**body, "username": "BOB?"}).status_code, 400)
        self.assertTrue(home.exists())                                   # nothing happened yet
        self.assertEqual(b.api("POST", "/api/account/delete", body).status_code, 200)

        self.assertFalse(home.exists())
        self.assertFalse(workspace.exists())
        self.assertIsNone(self.svc.accounts.find_user("bob"))
        self.assertNotIn(bob["id"], (self.config.home / "accounts.json").read_text(encoding="utf-8"))
        self.assertEqual(b.get("/api/chats").status_code, 401)
        self.assertEqual(Browser(self.app).login("bob", BOB_PASSWORD).status_code, 401)
        self.add_member("bob")                                           # the name can be used again
        self.assertEqual(self.svc.userdata.chats(self.svc.accounts.find_user("bob")["id"]).read(lambda d: d["chats"]), [])

    def test_alice_is_untouched_when_bob_leaves(self):
        a = self.signed_in()
        a.api("POST", "/api/chats")
        bob = self.add_member()
        self.svc.accounts.delete_user(bob["id"])
        self.svc.userdata.delete(bob["id"])
        self.assertEqual(len(a.api("GET", "/api/chats").get_json()), 1)

    def test_the_owner_cannot_delete_while_others_depend_on_the_host(self):
        self.add_member()
        a = self.signed_in()
        resp = a.api("POST", "/api/account/delete", {"username": "alice", "password": PASSWORD})
        self.assertEqual(resp.status_code, 409)
        self.assertIsNotNone(self.svc.accounts.find_user("alice"))

    def test_the_owner_can_remove_a_member_and_their_data(self):
        bob = self.add_member()
        self.svc.userdata.ensure_workspace(bob["id"])
        a = self.signed_in()
        self.assertEqual(a.api("DELETE", f"/api/people/members/{bob['id']}").status_code, 200)
        self.assertFalse(self.svc.userdata.workspace(bob["id"]).exists())
        self.assertEqual(a.api("DELETE", f"/api/people/members/{self.alice['id']}").status_code, 404)   # not yourself here
        self.assertEqual(a.api("DELETE", "/api/people/members/not-an-id").status_code, 404)


class PeoplePanelTests(AppTestCase):
    def test_owner_lists_and_revokes_invites(self):
        a = self.signed_in()
        made = a.api("POST", "/api/people/invites").get_json()
        listing = a.api("GET", "/api/people").get_json()
        self.assertEqual([i["id"] for i in listing["invites"]], [made["id"]])
        self.assertNotIn("code", json.dumps(listing))                       # the code is only ever shown once
        self.assertEqual(listing["members"][0]["username"], "alice")
        self.assertEqual(a.api("DELETE", f"/api/people/invites/{made['id']}").status_code, 200)
        self.assertEqual(a.api("DELETE", f"/api/people/invites/{made['id']}").status_code, 404)
        _, resp = register_via_web(self.app, "bob", BOB_PASSWORD, invite=made["id"])
        self.assertEqual(resp.status_code, 403)

    def test_unused_invites_are_capped(self):
        a = self.signed_in()
        for _ in range(50):
            self.assertEqual(a.api("POST", "/api/people/invites").status_code, 201)
        self.assertEqual(a.api("POST", "/api/people/invites").status_code, 409)


class HostCommandLineTests(AppTestCase):
    def test_reset_password_signs_the_person_out(self):
        b = self.signed_in()
        self.svc.accounts.reset_password("alice", "a brand new passphrase")
        self.assertEqual(b.get("/api/chats").status_code, 401)
        self.assertEqual(Browser(self.app).login("alice", "a brand new passphrase").status_code, 302)
        with self.assertRaises(AccountError):
            self.svc.accounts.reset_password("nobody", "a brand new passphrase")

    def test_end_everyone(self):
        a, b = self.signed_in(), self.signed_in()
        self.assertEqual(self.svc.accounts.end_everyone(), 2)
        self.assertEqual((a.get("/api/chats").status_code, b.get("/api/chats").status_code), (401, 401))


if __name__ == "__main__":
    unittest.main()
