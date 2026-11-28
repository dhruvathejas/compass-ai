"""Server mode: a shared public host where anyone can sign up and use it for free, within limits."""
from __future__ import annotations

import datetime as dt
import unittest
from dataclasses import replace

from compass.limits import Policy, PolicyError, ServerSettings, Usage

from tests.helpers import BOB_PASSWORD, PASSWORD, AppTestCase, Browser


class ServerCase(AppTestCase):
    config_overrides = {"mode": "server", "busy_wait": 0}

    def owner(self) -> Browser:
        return self.signed_in("alice", PASSWORD)

    def add_member(self, username="bob", password=BOB_PASSWORD) -> dict:
        return self.svc.accounts.register(username, username.title(), "", password)      # sign-up is open here

    def member(self, name="bob") -> Browser:
        self.add_member(name)
        return self.signed_in(name, BOB_PASSWORD)

    def limits(self, owner: Browser, **values):
        resp = owner.api("PUT", "/api/server/limits", values)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))

    def say(self, who: Browser, chat_id: str, text="hi"):
        return who.api("POST", f"/api/chats/{chat_id}/message", {"content": text})

    def chat(self, who: Browser) -> str:
        return who.api("POST", "/api/chats").get_json()["id"]


class Basics(ServerCase):
    def test_signup_is_open_by_default(self):
        self.assertEqual(self.svc.accounts.registration_mode(), "open")

    def test_signing_in_registers_the_browser_as_a_device(self):
        bob = self.member()
        self.assertEqual(bob.get("/").status_code, 200)             # no "authorise this device" step
        devices = bob.api("GET", "/api/devices").get_json()
        self.assertEqual(len(devices), 1)
        self.assertTrue(devices[0]["current"])

    def test_devices_at_the_cap_make_room_instead_of_blocking_sign_in(self):
        self.add_member()
        for i in range(25):
            self.svc.accounts.create_device(self.svc.accounts.find_user("bob")["id"], f"d{i}", "other", "x")
        bob = Browser(self.app)
        self.assertEqual(bob.login("bob", BOB_PASSWORD).status_code, 302)
        self.assertEqual(bob.get("/").status_code, 200)

    def test_visitors_go_straight_to_sign_in_not_the_welcome_page(self):
        anon = Browser(self.app)
        self.assertEqual(anon.get("/").location, "/login")
        self.assertEqual(anon.get("/welcome").location, "/login")
        self.assertIn("Free to use", anon.get("/login").get_data(as_text=True))

    def test_the_signed_in_page_has_no_code_button(self):
        page = self.owner().get("/").get_data(as_text=True)
        self.assertNotIn('id="open-code"', page)
        self.assertIn('id="usage"', page)

    def test_nothing_is_local_even_from_the_server_itself(self):
        alice = self.owner()                                         # loopback, no proxy headers
        self.assertEqual(alice.api("POST", "/api/workspace/open-in-code").status_code, 404)
        self.assertFalse(alice.api("GET", "/api/workspace").get_json()["local"])
        self.assertEqual(alice.api("GET", "/api/account").get_json()["connection"]["kind"], "internet")

    def test_web_sign_up_works_and_signs_you_in(self):
        anon = Browser(self.app, addr="203.0.113.9")
        token = anon.form_token("/register")
        resp = anon.request("POST", "/register", data={"username": "carol", "password": "a fine long passphrase",
                                                       "confirm": "a fine long passphrase", "csrf_token": token})
        self.assertEqual(resp.status_code, 302, resp.get_data(as_text=True))
        anon.csrf = anon.page_token("/")
        self.assertEqual(anon.api("GET", "/api/account").get_json()["user"]["username"], "carol")
        self.assertEqual(len(anon.api("GET", "/api/devices").get_json()), 1)

    def test_the_account_page_text_matches_a_shared_server(self):
        page = self.owner().get("/account/privacy").get_data(as_text=True)
        self.assertIn("Fair use", page)
        self.assertNotIn("main PC that hosts", page)


class NoOwnerYet(AppTestCase):
    config_overrides = {"mode": "server"}
    create_owner = False

    def test_the_owner_can_not_be_created_from_a_web_page(self):
        anon = Browser(self.app)                                     # even from loopback
        page = anon.get("/register")
        self.assertEqual(page.status_code, 503)
        self.assertIn("set-password", page.get_data(as_text=True))
        self.assertEqual(anon.get("/login").status_code, 503)
        self.assertEqual(anon.get("/api/chats").status_code, 503)
        self.assertEqual(self.svc.accounts.count_users(), 0)

    def test_the_command_line_creates_the_owner(self):
        user = self.svc.accounts.register("root", "Root", "", PASSWORD, is_local=True)
        self.assertEqual(user["role"], "owner")
        self.assertEqual(Browser(self.app).get("/login").status_code, 200)


class FreeTier(ServerCase):
    def test_daily_limit_then_refusal_with_a_reset_time(self):
        owner = self.owner()
        bob = self.member()
        self.limits(owner, daily_messages=2, per_minute=0)
        chat = self.chat(bob)
        self.assertEqual(self.say(bob, chat).status_code, 200)
        self.assertEqual(self.say(bob, chat).status_code, 200)
        refused = self.say(bob, chat)
        self.assertEqual(refused.status_code, 429)
        self.assertEqual(refused.get_json()["kind"], "daily")
        self.assertIn("2 free messages", refused.get_json()["error"])
        self.assertGreater(int(refused.headers["Retry-After"]), 0)
        usage = bob.api("GET", "/api/usage").get_json()
        self.assertEqual((usage["used"], usage["remaining"], usage["daily_limit"]), (2, 0, 2))
        self.assertEqual(len(self.ollama.requests), 2)               # the refused one never reached the model

    def test_the_owner_is_never_limited(self):
        owner = self.owner()
        self.limits(owner, daily_messages=1, per_minute=1)
        chat = self.chat(owner)
        for _ in range(3):
            self.assertEqual(self.say(owner, chat).status_code, 200)
        self.assertEqual(owner.api("GET", "/api/usage").get_json(), {"limited": False})

    def test_zero_means_no_limit(self):
        owner = self.owner()
        bob = self.member()
        self.limits(owner, daily_messages=0, per_minute=0)
        chat = self.chat(bob)
        for _ in range(4):
            self.assertEqual(self.say(bob, chat).status_code, 200)

    def test_a_message_without_an_answer_is_refunded(self):
        owner = self.owner()
        bob = self.member()
        self.limits(owner, daily_messages=3, per_minute=0)
        self.ollama.missing_model = self.config.default_model
        chat = self.chat(bob)
        self.assertEqual(self.say(bob, chat).status_code, 503)
        self.assertEqual(bob.api("GET", "/api/usage").get_json()["used"], 0)

    def test_a_message_to_a_missing_chat_is_refunded(self):
        bob = self.member()
        self.assertEqual(self.say(bob, "0123456789ab").status_code, 404)
        self.assertEqual(bob.api("GET", "/api/usage").get_json()["used"], 0)

    def test_per_minute_limit(self):
        owner = self.owner()
        bob = self.member()
        self.limits(owner, daily_messages=0, per_minute=2)
        chat = self.chat(bob)
        self.assertEqual(self.say(bob, chat).status_code, 200)
        self.assertEqual(self.say(bob, chat).status_code, 200)
        refused = self.say(bob, chat)
        self.assertEqual((refused.status_code, refused.get_json()["kind"]), (429, "rate"))
        self.assertEqual(bob.api("GET", "/api/usage").get_json()["used"], 2)    # the refused one wasn't counted

    def test_people_are_counted_separately(self):
        owner = self.owner()
        bob, carol = self.member("bob"), self.member("carol")
        self.limits(owner, daily_messages=1, per_minute=0)
        self.assertEqual(self.say(bob, self.chat(bob)).status_code, 200)
        self.assertEqual(self.say(bob, self.chat(bob)).status_code, 429)
        self.assertEqual(self.say(carol, self.chat(carol)).status_code, 200)

    def test_one_reply_at_a_time_per_person(self):
        owner = self.owner()
        bob = self.member()
        self.limits(owner, daily_messages=0, per_minute=0)
        chat = self.chat(bob)
        held = self.svc.policy.admit(self.svc.accounts.find_user("bob"))       # a reply is "in progress"
        refused = self.say(bob, chat)
        self.assertEqual((refused.status_code, refused.get_json()["kind"]), (429, "busy-self"))
        held.release()
        self.assertEqual(self.say(bob, chat).status_code, 200)

    def test_a_full_server_says_busy_and_does_not_charge_for_it(self):
        owner = self.owner()
        bob = self.member()
        self.limits(owner, daily_messages=5, per_minute=0)
        slots = [self.svc.policy.admit(self.svc.accounts.find_user("alice"))]
        # make the pretend replies hold every slot
        while True:
            try:
                slots.append(self.svc.policy.admit(self.svc.accounts.find_user("alice")))
            except PolicyError:
                break
        refused = self.say(bob, self.chat(bob))
        self.assertEqual((refused.status_code, refused.get_json()["kind"]), (503, "capacity"))
        self.assertEqual(bob.api("GET", "/api/usage").get_json()["used"], 0)
        for ticket in slots:
            ticket.release()
        self.assertEqual(self.say(bob, self.chat(bob)).status_code, 200)

    def test_saved_chat_limit(self):
        owner = self.owner()
        bob = self.member()
        self.limits(owner, max_chats=2)
        self.assertEqual(bob.api("POST", "/api/chats").status_code, 201)
        self.assertEqual(bob.api("POST", "/api/chats").status_code, 201)
        refused = bob.api("POST", "/api/chats")
        self.assertEqual(refused.status_code, 403)
        self.assertIn("2 saved chats", refused.get_json()["error"])
        for _ in range(3):
            self.assertEqual(owner.api("POST", "/api/chats").status_code, 201)


class ModelList(ServerCase):
    config_overrides = {"mode": "server", "busy_wait": 0, "models": ("llama3.2:latest",)}

    def test_models_are_limited_to_the_allow_list(self):
        bob = self.member()
        refused = bob.api("PUT", "/api/settings", {"model": "qwen2.5-coder:7b"})
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(bob.api("GET", "/api/models").get_json()["models"], ["llama3.2:latest"])
        # a model chosen before the list existed is quietly replaced by the default when sending
        with self.svc.userdata.settings(self.svc.accounts.find_user("bob")["id"]).transaction() as d:
            d["model"] = "qwen2.5-coder:7b"
        self.say(bob, self.chat(bob))
        self.assertEqual(self.ollama.requests[-1]["model"], "llama3.2:latest")


class ChatSize(ServerCase):
    config_overrides = {"mode": "server", "busy_wait": 0, "max_chat_messages": 2}

    def test_a_chat_can_only_grow_so_far(self):
        owner = self.owner()
        bob = self.member()
        self.limits(owner, daily_messages=10, per_minute=0)
        chat = self.chat(bob)
        self.assertEqual(self.say(bob, chat).status_code, 200)       # two messages now: question + answer
        full = self.say(bob, chat)
        self.assertEqual(full.status_code, 409)
        self.assertEqual(bob.api("GET", "/api/usage").get_json()["used"], 1)     # the refused one was refunded


class LongChats(ServerCase):
    config_overrides = {"mode": "server", "busy_wait": 0, "context_messages": 4}

    def test_only_the_latest_part_is_sent_to_the_model(self):
        owner = self.owner()
        bob = self.member()
        self.limits(owner, daily_messages=0, per_minute=0)
        chat = self.chat(bob)
        for i in range(6):
            self.say(bob, chat, f"message {i}")
        sent = self.ollama.requests[-1]["messages"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertEqual(len(sent) - 1, 4)
        self.assertEqual(sent[-1]["content"], "message 5")
        stored = bob.api("GET", f"/api/chats/{chat}").get_json()["messages"]
        self.assertEqual(len(stored), 12)                            # nothing is deleted, only not re-sent


class OwnerControls(ServerCase):
    def test_only_the_owner_sees_and_changes_limits(self):
        bob = self.member()
        self.assertEqual(bob.api("GET", "/api/server").status_code, 403)
        self.assertEqual(bob.api("PUT", "/api/server/limits", {"daily_messages": 99999}).status_code, 403)
        info = self.owner().api("GET", "/api/server").get_json()
        self.assertEqual(info["limits"]["daily_messages"], 50)
        self.assertEqual(info["accounts"], 2)

    def test_bad_limit_values_are_refused(self):
        owner = self.owner()
        for body in ({"daily_messages": -1}, {"daily_messages": "ten"}, {"daily_messages": True},
                     {"daily_messages": 1.5}, {"per_minute": 10 ** 9}, {"nonsense": 1}, {}, []):
            self.assertEqual(owner.api("PUT", "/api/server/limits", body).status_code, 400, body)
        self.assertEqual(owner.api("GET", "/api/server").get_json()["limits"]["daily_messages"], 50)

    def test_the_people_panel_shows_todays_usage(self):
        owner = self.owner()
        bob = self.member()
        self.limits(owner, per_minute=0)
        self.say(bob, self.chat(bob))
        people = owner.api("GET", "/api/people").get_json()
        self.assertTrue(people["server"])
        self.assertEqual({m["username"]: m["today"] for m in people["members"]}, {"alice": 0, "bob": 1})

    def test_suspending_signs_someone_out_and_blocks_sign_in(self):
        owner = self.owner()
        bob = self.member()
        bob_id = self.svc.accounts.find_user("bob")["id"]
        resp = owner.api("PUT", f"/api/people/members/{bob_id}/suspended", {"suspended": True})
        self.assertTrue(resp.get_json()["suspended"])
        self.assertEqual(bob.api("GET", "/api/chats").status_code, 401)           # signed out at once
        again = Browser(self.app)
        token = again.form_token("/login")
        blocked = again.request("POST", "/login", data={"username": "bob", "password": BOB_PASSWORD,
                                                        "csrf_token": token})
        self.assertEqual(blocked.status_code, 403)
        self.assertIn("suspended", blocked.get_data(as_text=True))
        owner.api("PUT", f"/api/people/members/{bob_id}/suspended", {"suspended": False})
        self.assertEqual(self.signed_in("bob", BOB_PASSWORD).api("GET", "/api/chats").status_code, 200)

    def test_suspend_is_owner_only_and_never_hits_the_owner(self):
        owner = self.owner()
        bob = self.member()
        alice_id, bob_id = self.alice["id"], self.svc.accounts.find_user("bob")["id"]
        self.assertEqual(bob.api("PUT", f"/api/people/members/{alice_id}/suspended", {"suspended": True})
                         .status_code, 403)
        self.assertEqual(owner.api("PUT", f"/api/people/members/{alice_id}/suspended", {"suspended": True})
                         .status_code, 404)
        self.assertEqual(owner.api("PUT", f"/api/people/members/{bob_id}/suspended", {"suspended": "yes"})
                         .status_code, 400)
        self.assertEqual(owner.api("PUT", "/api/people/members/0123456789abcdef/suspended", {"suspended": True})
                         .status_code, 404)

    def test_deleting_an_account_erases_its_usage(self):
        owner = self.owner()
        bob = self.member()
        self.limits(owner, per_minute=0)
        self.say(bob, self.chat(bob))
        bob_id = self.svc.accounts.find_user("bob")["id"]
        self.assertEqual(owner.api("DELETE", f"/api/people/members/{bob_id}").status_code, 200)
        self.assertNotIn(bob_id, self.svc.policy.usage.today())


class Capacity(ServerCase):
    def test_the_account_cap_refuses_new_sign_ups(self):
        owner = self.owner()
        self.limits(owner, max_users=2)
        self.add_member("bob")
        anon = Browser(self.app, addr="203.0.113.7")
        token = anon.form_token("/register")
        resp = anon.request("POST", "/register", data={"username": "carol", "password": "a fine long passphrase",
                                                       "confirm": "a fine long passphrase", "csrf_token": token})
        self.assertEqual(resp.status_code, 403)
        self.assertIn("full", resp.get_data(as_text=True))
        self.assertIsNone(self.svc.accounts.find_user("carol"))

    def test_a_flood_of_sign_ups_from_many_addresses_is_slowed_down(self):
        self.svc.signups_all.limit = 1
        first = Browser(self.app, addr="203.0.113.1")
        token = first.form_token("/register")
        self.assertEqual(first.request("POST", "/register", data={
            "username": "dave", "password": "a fine long passphrase", "confirm": "a fine long passphrase",
            "csrf_token": token}).status_code, 302)
        second = Browser(self.app, addr="203.0.113.2")
        token = second.form_token("/register")
        resp = second.request("POST", "/register", data={
            "username": "erin", "password": "a fine long passphrase", "confirm": "a fine long passphrase",
            "csrf_token": token})
        self.assertEqual(resp.status_code, 429)


class Proxies(ServerCase):
    def key(self, headers, proxy_header):
        from compass import security
        self.svc.config = replace(self.svc.config, proxy_header=proxy_header)
        with self.app.test_request_context("/", headers=headers, environ_base={"REMOTE_ADDR": "10.0.0.1"}):
            return security.client_key()

    def test_the_address_comes_from_the_proxy_header_only_when_configured(self):
        self.assertEqual(self.key({"X-Forwarded-For": "1.2.3.4"}, ""), "10.0.0.1")
        self.assertEqual(self.key({"X-Forwarded-For": "1.2.3.4"}, "X-Forwarded-For"), "1.2.3.4")
        self.assertEqual(self.key({"Cf-Connecting-Ip": "2001:db8::1"}, "Cf-Connecting-Ip"), "2001:db8::1")

    def test_a_forged_forwarded_for_list_cannot_pick_the_address(self):
        # the proxy appends the real address at the END; whatever the visitor put before it is ignored
        self.assertEqual(self.key({"X-Forwarded-For": "6.6.6.6, 1.2.3.4"}, "X-Forwarded-For"), "1.2.3.4")

    def test_garbage_in_the_header_is_one_shared_bucket(self):
        self.assertEqual(self.key({"X-Forwarded-For": "not an ip"}, "X-Forwarded-For"), "unknown")

    def test_guessing_one_account_from_many_addresses_gets_locked(self):
        self.svc.config = replace(self.svc.config, proxy_header="X-Real-Ip")
        self.add_member()
        statuses = []
        for i in range(7):
            b = Browser(self.app, headers={"X-Real-Ip": f"198.51.100.{i + 1}"})
            token = b.form_token("/login")
            statuses.append(b.request("POST", "/login", data={"username": "bob", "password": "wrong password!",
                                                              "csrf_token": token}).status_code)
        self.assertEqual(statuses[:5], [401] * 5)
        self.assertEqual(statuses[5], 429)                           # a new address, but the account is locked
        # a different account is unaffected
        b = Browser(self.app, headers={"X-Real-Ip": "198.51.100.99"})
        self.assertEqual(b.login("alice", PASSWORD).status_code, 302)


class Hosts(AppTestCase):
    config_overrides = {"mode": "server", "public_url": "https://chat.example.com",
                        "allowed_hosts": frozenset({"chat.example.com"})}

    def test_only_the_public_name_is_answered(self):
        self.assertEqual(Browser(self.app).get("/login", headers={"Host": "chat.example.com"}).status_code, 200)
        self.assertEqual(Browser(self.app).get("/login", headers={"Host": "evil.example"}).status_code, 400)
        self.assertEqual(Browser(self.app).get("/login", headers={"Host": "localhost:5000"}).status_code, 200)

    def test_https_public_url_makes_cookies_secure_and_sends_hsts(self):
        b = Browser(self.app, headers={"Host": "chat.example.com"})
        resp = b.get("/login")
        self.assertIn("max-age=", resp.headers.get("Strict-Transport-Security", ""))
        token = b.form_token("/login")
        resp = b.request("POST", "/login",
                         data={"username": "alice", "password": PASSWORD, "csrf_token": token})
        cookies = [c for c in resp.headers.getlist("Set-Cookie")]
        self.assertTrue(cookies)
        for cookie in cookies:
            self.assertIn("Secure", cookie, cookie)
            self.assertIn("HttpOnly", cookie, cookie)


class PersonalHostUnchanged(AppTestCase):
    def test_no_limits_and_no_server_endpoints(self):
        alice = self.signed_in()
        self.assertEqual(alice.api("GET", "/api/usage").get_json(), {"limited": False})
        self.assertEqual(alice.api("GET", "/api/server").status_code, 404)
        self.assertEqual(alice.api("PUT", "/api/server/limits", {"daily_messages": 1}).status_code, 404)

    def test_a_member_of_a_personal_host_is_not_limited_either(self):
        self.add_member()
        bob = self.signed_in("bob", BOB_PASSWORD)
        chat = bob.api("POST", "/api/chats").get_json()["id"]
        for _ in range(60):
            self.assertEqual(bob.api("POST", f"/api/chats/{chat}/message", {"content": "x"}).status_code, 200)

    def test_the_code_button_and_welcome_page_are_still_there(self):
        alice = self.signed_in()
        self.assertIn('id="open-code"', alice.get("/").get_data(as_text=True))
        self.assertEqual(Browser(self.app).get("/welcome").status_code, 200)
        self.assertEqual(self.svc.accounts.registration_mode(), "invite")

    def test_the_owner_panel_hides_server_things(self):
        page = self.signed_in().get("/account/people").get_data(as_text=True)
        self.assertNotIn("Free-tier limits", page)


class UsageUnit(unittest.TestCase):
    """The counters on their own, with a clock we control."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from compass.config import Config
        self.tmp = tempfile.mkdtemp(prefix="compass-usage-")
        self.cfg = Config(home=Path(self.tmp), workspace=Path(self.tmp) / "w", ollama_host="http://x",
                          default_model="m", mode="server")
        self.now = dt.datetime(2026, 11, 27, 23, 59, 0, tzinfo=dt.timezone.utc).timestamp()
        self.usage = Usage(self.cfg, clock=lambda: self.now)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_counts_reset_at_midnight_utc(self):
        self.assertTrue(self.usage.consume("u", 1))
        self.assertFalse(self.usage.consume("u", 1))
        self.assertEqual(self.usage.resets_at() - self.now, 60)
        self.now += 120                                              # past midnight
        self.assertEqual(self.usage.used("u"), 0)
        self.assertTrue(self.usage.consume("u", 1))

    def test_old_days_are_forgotten(self):
        for day in range(20):
            self.now += 86400
            self.usage.consume("u", 0)
        self.assertLessEqual(len(self.usage._store.read(lambda d: list(d["days"]))), 14)

    def test_refund_never_goes_negative(self):
        self.usage.refund("u")
        self.assertEqual(self.usage.used("u"), 0)

    def test_settings_validate_and_persist(self):
        settings = ServerSettings(self.cfg)
        self.assertEqual(settings.get()["daily_messages"], 50)
        settings.update({"daily_messages": 7})
        self.assertEqual(ServerSettings(self.cfg).get()["daily_messages"], 7)
        with self.assertRaises(ValueError):
            settings.update({"daily_messages": -5})

    def test_personal_mode_policy_does_nothing(self):
        from dataclasses import replace
        policy = Policy(replace(self.cfg, mode="personal"))
        ticket = policy.admit({"id": "u", "role": "member"})
        ticket.refund()
        ticket.release()
        self.assertEqual(policy.usage_for({"id": "u", "role": "member"}), {"limited": False})


if __name__ == "__main__":
    unittest.main()
