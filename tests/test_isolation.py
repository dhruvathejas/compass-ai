"""The most important tests: two people on one host must never see or touch each other's things."""
import json
import unittest

from tests.helpers import BOB_PASSWORD, PASSWORD, AppTestCase, Browser


class IsolationTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.bob_user = self.add_member()
        self.a = self.signed_in()
        self.b = self.signed_in("bob", BOB_PASSWORD)
        self.chat = self.a.api("POST", "/api/chats").get_json()["id"]
        self.a.api("POST", f"/api/chats/{self.chat}/message", {"content": "alice's secret plan"})

    def test_chats_are_private(self):
        self.assertEqual(self.b.api("GET", "/api/chats").get_json(), [])
        self.assertEqual(self.b.api("GET", f"/api/chats/{self.chat}").status_code, 404)
        self.assertEqual(self.b.api("POST", f"/api/chats/{self.chat}/message", {"content": "hi"}).status_code, 404)
        self.assertEqual(self.b.api("DELETE", f"/api/chats/{self.chat}").status_code, 404)
        chat = self.a.api("GET", f"/api/chats/{self.chat}").get_json()           # untouched
        self.assertEqual([m["content"] for m in chat["messages"]][0], "alice's secret plan")
        self.assertEqual(len(chat["messages"]), 2)

    def test_a_missing_chat_and_someone_elses_chat_are_indistinguishable(self):
        theirs = self.b.api("GET", f"/api/chats/{self.chat}")
        nothing = self.b.api("GET", "/api/chats/000000000000")
        self.assertEqual((theirs.status_code, theirs.get_json()), (nothing.status_code, nothing.get_json()))

    def test_chats_live_in_separate_files(self):
        self.b.api("POST", "/api/chats")
        a_file = (self.svc.userdata.home(self.alice["id"]) / "chats.json").read_text(encoding="utf-8")
        b_file = (self.svc.userdata.home(self.bob_user["id"]) / "chats.json").read_text(encoding="utf-8")
        self.assertIn("alice's secret plan", a_file)
        self.assertNotIn("alice's secret plan", b_file)
        self.assertEqual(len(json.loads(b_file)["chats"]), 1)

    def test_settings_are_per_person(self):
        self.a.api("PUT", "/api/settings", {"theme": "dark", "model": "qwen2.5-coder:7b"})
        self.assertEqual(self.b.api("GET", "/api/settings").get_json(), {"model": "llama3.2:latest", "theme": "auto"})
        self.assertIn('data-theme="dark"', self.a.get("/").get_data(as_text=True))
        self.assertNotIn("data-theme", self.b.get("/").get_data(as_text=True))
        cid = self.b.api("POST", "/api/chats").get_json()["id"]
        self.b.api("POST", f"/api/chats/{cid}/message", {"content": "hello"})
        self.assertEqual(self.ollama.requests[-1]["model"], "llama3.2:latest")        # bob's own model, not alice's

    def test_devices_are_private(self):
        a_device = self.a.api("GET", "/api/devices").get_json()[0]["id"]
        b_rows = self.b.api("GET", "/api/devices").get_json()
        self.assertEqual(len(b_rows), 1)
        self.assertNotEqual(b_rows[0]["id"], a_device)
        self.assertEqual(self.b.api("PATCH", f"/api/devices/{a_device}", {"name": "pwned"}).status_code, 404)
        self.assertEqual(self.b.api("DELETE", f"/api/devices/{a_device}").status_code, 404)
        self.assertEqual(self.a.api("GET", "/api/devices").get_json()[0]["name"], "Test PC")
        self.assertEqual(self.a.get("/api/chats").status_code, 200)                    # alice is still signed in

    def test_sessions_are_private(self):
        a_sessions = self.a.api("GET", "/api/sessions").get_json()
        self.assertEqual(len(self.b.api("GET", "/api/sessions").get_json()), 1)
        self.assertEqual(self.b.api("DELETE", f"/api/sessions/{a_sessions[0]['id']}").status_code, 404)
        self.assertEqual(self.a.get("/api/chats").status_code, 200)
        self.b.api("POST", "/api/sessions/revoke")                                     # bob signing out everywhere...
        self.assertEqual(self.a.get("/api/chats").status_code, 200)                    # ...doesn't touch alice

    def test_people_management_is_owner_only(self):
        for method, path, body in (("GET", "/api/people", None), ("PUT", "/api/people/registration", {"mode": "open"}),
                                   ("POST", "/api/people/invites", None),
                                   ("DELETE", f"/api/people/members/{self.alice['id']}", None)):
            self.assertEqual(self.b.api(method, path, body).status_code, 403, path)
        self.assertEqual(self.b.get("/account/people").status_code, 404)
        self.assertEqual(self.svc.accounts.registration_mode(), "invite")
        self.assertIsNotNone(self.svc.accounts.find_user("alice"))
        self.assertNotIn("people", self.b.get("/account/profile").get_data(as_text=True).lower().split("<nav")[1].split("</nav>")[0])

    def test_client_supplied_identity_is_ignored(self):
        resp = self.b.api("POST", f"/api/chats?uid={self.alice['id']}&user={self.alice['id']}",
                          headers={"X-User-Id": self.alice["id"], "X-Device-Id": "x"})
        self.assertEqual(resp.status_code, 201)
        mine = self.svc.userdata.chats(self.bob_user["id"]).read(lambda d: len(d["chats"]))
        theirs = self.svc.userdata.chats(self.alice["id"]).read(lambda d: len(d["chats"]))
        self.assertEqual((mine, theirs), (1, 1))
        resp = self.b.api("POST", "/api/account/delete", {"username": "alice", "password": PASSWORD})
        self.assertEqual(resp.status_code, 400)                                        # can't delete alice by naming her
        self.assertIsNotNone(self.svc.accounts.find_user("alice"))

    def test_user_ids_cannot_be_used_to_reach_other_folders(self):
        for bad in ("../x", "..", "", "A" * 16, "g" * 16, "0" * 15, None, 5, self.alice["id"] + "/../" + self.bob_user["id"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.svc.userdata.chats(bad)
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.svc.userdata.workspace(bad)

    def test_workspaces_are_separate_folders(self):
        a_ws = self.svc.userdata.ensure_workspace(self.alice["id"])
        b_ws = self.svc.userdata.ensure_workspace(self.bob_user["id"])
        self.assertNotEqual(a_ws, b_ws)
        self.assertEqual(a_ws.parent, b_ws.parent)
        self.assertFalse(a_ws.is_relative_to(b_ws) or b_ws.is_relative_to(a_ws))

    def test_the_account_page_only_ever_shows_my_own_details(self):
        page = self.b.get("/account/profile").get_data(as_text=True)
        self.assertIn("bob", page)
        self.assertNotIn(self.alice["id"], page)
        self.assertNotIn("alice@example.com", page)
        self.assertNotIn(self.alice["id"], json.dumps(self.b.api("GET", "/api/account").get_json()))

    def test_nobody_can_read_account_files_over_http(self):
        for path in ("/memory/accounts.json", "/static/../memory/accounts.json", "/static/%2e%2e/memory/auth.json",
                     "/accounts.json", "/users/x", "/workspace/x"):
            self.assertIn(self.b.get(path).status_code, (404, 400), path)
        self.assertEqual(Browser(self.app).get("/static/css/app.css").status_code, 200)


if __name__ == "__main__":
    unittest.main()
