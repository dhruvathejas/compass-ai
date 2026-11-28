import json
import tempfile
import unittest
from pathlib import Path

from compass.store import JsonStore, StoreError


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "doc.json"

    def tearDown(self):
        self.dir.cleanup()

    def make(self, **kw):
        return JsonStore(self.path, default=lambda: {"items": []}, **kw)

    def test_changes_persist_across_instances(self):
        with self.make().transaction() as d:
            d["items"].append("a")
        self.assertEqual(self.make().read(lambda d: d["items"]), ["a"])

    def test_missing_file_gives_default_with_schema(self):
        self.assertEqual(self.make(version=3).read(lambda d: dict(d)), {"items": [], "schema": 3})

    def test_failed_transaction_discards_edits(self):
        store = self.make()
        with store.transaction() as d:
            d["items"].append("keep")
        with self.assertRaises(RuntimeError):
            with store.transaction() as d:
                d["items"].append("lost")
                raise RuntimeError("boom")
        self.assertEqual(store.read(lambda d: d["items"]), ["keep"])

    def test_corrupt_file_is_quarantined_not_overwritten(self):
        self.path.write_text("{ this is not json", encoding="utf-8")
        self.assertEqual(self.make().read(lambda d: d["items"]), [])
        broken = list(self.path.parent.glob("doc.json.broken-*"))
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0].read_text(encoding="utf-8"), "{ this is not json")

    def test_non_object_top_level_is_treated_as_corrupt(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        self.assertEqual(self.make().read(lambda d: d["items"]), [])
        self.assertEqual(len(list(self.path.parent.glob("doc.json.broken-*"))), 1)

    def test_migration_runs_in_order_and_backs_up(self):
        self.path.write_text(json.dumps({"items": ["x"]}), encoding="utf-8")   # old files have no schema
        order = []

        def v1(d): order.append(1); d["items"].append("v2")
        def v2(d): order.append(2); d["items"].append("v3")

        store = self.make(version=3, migrations={1: v1, 2: v2})
        self.assertEqual(store.read(lambda d: d["items"]), ["x", "v2", "v3"])
        self.assertEqual(order, [1, 2])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["schema"], 3)
        backup = self.path.with_name("doc.json.bak-v1")
        self.assertEqual(json.loads(backup.read_text(encoding="utf-8")), {"items": ["x"]})

    def test_newer_schema_is_refused(self):
        self.path.write_text(json.dumps({"items": [], "schema": 99}), encoding="utf-8")
        with self.assertRaises(StoreError):
            self.make(version=2).read(lambda d: d)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["schema"], 99)

    def test_no_temp_files_left_behind(self):
        store = self.make()
        for i in range(5):
            with store.transaction() as d:
                d["items"].append(i)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["doc.json"])

    def test_external_edit_is_picked_up(self):
        store = self.make()
        with store.transaction() as d:
            d["items"].append("a")
        self.path.write_text(json.dumps({"items": ["edited by hand"], "schema": 1}), encoding="utf-8")
        self.assertEqual(store.read(lambda d: d["items"]), ["edited by hand"])


if __name__ == "__main__":
    unittest.main()
