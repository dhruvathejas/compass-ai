import json
import unittest

from compass import create_app
from compass.config import Config
from tests.helpers import AppTestCase


class ChatApiTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.b = self.signed_in()

    def disk(self):
        return json.loads((self.config.home / "chats.json").read_text(encoding="utf-8"))

    def new_chat(self):
        return self.b.api("POST", "/api/chats").get_json()["id"]

    def test_first_message_titles_the_chat_and_is_saved_to_the_json_file(self):
        cid = self.new_chat()
        resp = self.b.api("POST", f"/api/chats/{cid}/message", {"content": "What is 2+2?"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["reply"], "Echo: What is 2+2?")
        chat = self.disk()["chats"][0]
        self.assertEqual(chat["title"], "What is 2+2?")
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant"])
        self.assertEqual(chat["messages"][1]["model"], "llama3.2:latest")

    def test_ollama_receives_system_prompt_history_and_selected_model(self):
        cid = self.new_chat()
        self.b.api("PUT", "/api/settings", {"model": "qwen2.5-coder:7b"})
        self.b.api("POST", f"/api/chats/{cid}/message", {"content": "one"})
        self.b.api("POST", f"/api/chats/{cid}/message", {"content": "two"})
        sent = self.ollama.requests[-1]
        self.assertEqual(sent["model"], "qwen2.5-coder:7b")
        self.assertFalse(sent["stream"])
        self.assertEqual([m["role"] for m in sent["messages"]], ["system", "user", "assistant", "user"])

    def test_user_message_survives_when_ollama_is_down(self):
        cid = self.new_chat()
        self.ollama.close()
        resp = self.b.api("POST", f"/api/chats/{cid}/message", {"content": "anyone there?"})
        self.assertEqual(resp.status_code, 503)
        self.assertIn("Can't reach Ollama", resp.get_json()["error"])
        messages = self.disk()["chats"][0]["messages"]
        self.assertEqual([(m["role"], m["content"]) for m in messages], [("user", "anyone there?")])

    def test_missing_model_gives_a_clear_error(self):
        self.ollama.missing_model = "llama3.2:latest"
        cid = self.new_chat()
        resp = self.b.api("POST", f"/api/chats/{cid}/message", {"content": "hi"})
        self.assertEqual(resp.status_code, 503)
        self.assertIn("doesn't have the model", resp.get_json()["error"])

    def test_validation(self):
        cid = self.new_chat()
        for body in ({}, {"content": ""}, {"content": "   "}, {"content": 5}, {"content": None}):
            self.assertEqual(self.b.api("POST", f"/api/chats/{cid}/message", body).status_code, 400, body)
        too_long = "x" * (self.config.max_message_chars + 1)
        self.assertEqual(self.b.api("POST", f"/api/chats/{cid}/message", {"content": too_long}).status_code, 413)
        self.assertEqual(self.b.api("POST", "/api/chats/does-not-exist/message", {"content": "hi"}).status_code, 404)
        self.assertEqual(self.b.api("GET", "/api/chats/..%2f..%2fauth").status_code, 404)

    def test_list_is_newest_first_and_delete_works(self):
        first = self.new_chat()
        second = self.new_chat()
        self.b.api("POST", f"/api/chats/{first}/message", {"content": "bump the first chat"})
        ids = [c["id"] for c in self.b.api("GET", "/api/chats").get_json()]
        self.assertEqual(ids, [first, second])
        self.assertEqual(self.b.api("DELETE", f"/api/chats/{first}").status_code, 204)
        self.assertEqual(self.b.api("DELETE", f"/api/chats/{first}").status_code, 404)
        self.assertEqual([c["id"] for c in self.b.api("GET", "/api/chats").get_json()], [second])
        self.assertEqual([c["id"] for c in self.disk()["chats"]], [second])

    def test_chat_deleted_while_model_thinks_does_not_crash(self):
        cid = self.new_chat()
        import compass.routes_chat as rc
        real = rc.ollama.chat

        def chat_then_delete(*a, **k):
            with self.svc.chats.transaction() as d:
                d["chats"] = []
            return real(*a, **k)

        rc.ollama.chat = chat_then_delete
        try:
            resp = self.b.api("POST", f"/api/chats/{cid}/message", {"content": "hello"})
        finally:
            rc.ollama.chat = real
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.get_json()["chat"])


class SettingsApiTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.b = self.signed_in()

    def test_defaults_and_updates_persist(self):
        self.assertEqual(self.b.api("GET", "/api/settings").get_json(), {"model": "llama3.2:latest", "theme": "auto"})
        self.assertEqual(self.b.api("PUT", "/api/settings", {"theme": "dark"}).status_code, 200)
        fresh = create_app(self.config)                  # simulates a restart
        self.assertEqual(fresh.extensions["compass"].settings.read(lambda d: d["theme"]), "dark")

    def test_theme_is_rendered_server_side_to_avoid_a_flash(self):
        self.b.api("PUT", "/api/settings", {"theme": "dark"})
        self.assertIn('data-theme="dark"', self.b.get("/").get_data(as_text=True))
        self.b.api("PUT", "/api/settings", {"theme": "auto"})
        self.assertNotIn("data-theme", self.b.get("/").get_data(as_text=True))

    def test_validation(self):
        for body in ({"theme": "neon"}, {"model": "bad name; rm -rf"}, {"model": ""}, {"model": 7},
                     {"nope": 1}, {"model": "../../etc/passwd"}):
            self.assertEqual(self.b.api("PUT", "/api/settings", body).status_code, 400, body)
        self.assertEqual(self.b.api("PUT", "/api/settings", [1]).status_code, 400)

    def test_models_lists_what_ollama_has(self):
        data = self.b.api("GET", "/api/models").get_json()
        self.assertEqual(data["models"], ["llama3.2:latest", "qwen2.5-coder:7b"])
        self.assertIsNone(data["error"])
        self.assertEqual(data["current"], "llama3.2:latest")

    def test_models_reports_ollama_down_without_failing(self):
        self.ollama.close()
        resp = self.b.api("GET", "/api/models")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["models"], [])
        self.assertIn("Can't reach Ollama", resp.get_json()["error"])


class MigrationTests(AppTestCase):
    set_password = False

    def test_existing_v1_chats_are_upgraded_and_backed_up(self):
        self.config.home.mkdir(parents=True, exist_ok=True)
        old = {"chats": [{"id": "abcdef012345", "title": "Old chat", "created": 1.0, "updated": 2.0,
                          "messages": [{"role": "user", "content": "hello"}]}]}
        (self.config.home / "chats.json").write_text(json.dumps(old), encoding="utf-8")
        app = create_app(self.config)
        data = app.extensions["compass"].chats.read(lambda d: json.loads(json.dumps(d)))
        self.assertEqual(data["schema"], 2)
        self.assertEqual(data["chats"][0]["messages"][0]["ts"], 2.0)
        self.assertEqual(json.loads((self.config.home / "chats.json.bak-v1").read_text(encoding="utf-8")), old)


if __name__ == "__main__":
    unittest.main()
