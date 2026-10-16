"""setup_index: fake Meilisearch server injected, no network."""
import os
import tempfile
import unittest

from helpers import make_env

import setup_index


class FakeServer:
    """Imitates the routes setup_index uses and records the writes."""

    def __init__(self):
        self.indexes = set()
        self.keys = []
        self.writes = []
        self.task_status = {}
        self.settings = {}
        self.healthy = True

    def __call__(self, method, path, body=None):
        if method != "GET":
            self.writes.append((method, path, body))
        if method == "GET" and path == "/health":
            return (200, {"status": "available"}) if self.healthy else (503, {})
        if method == "GET" and path.startswith("/tasks/"):
            uid = int(path.rsplit("/", 1)[1])
            state = self.task_status.get(uid, "succeeded")
            resp = {"uid": uid, "status": state}
            if state == "failed":
                resp["error"] = {"message": "invalid setting"}
            return 200, resp
        if method == "GET" and path.endswith("/stats"):
            uid = path.split("/")[2]
            return (200, {"numberOfDocuments": 12, "isIndexing": False}) if uid in self.indexes else (404, {})
        if method == "GET" and path.endswith("/settings"):
            return 200, self.settings
        if method == "GET" and path.startswith("/indexes/"):
            uid = path.split("/")[2]
            return (200, {"uid": uid}) if uid in self.indexes else (404, {})
        if method == "POST" and path == "/indexes":
            self.indexes.add(body["uid"])
            return 202, {"taskUid": 1}
        if method == "PATCH" and path.endswith("/settings"):
            self.settings = body
            return 202, {"taskUid": 2}
        if method == "GET" and path.startswith("/keys"):
            return 200, {"results": list(self.keys)}
        if method == "POST" and path == "/keys":
            key = dict(body, key="secret-%d" % (len(self.keys) + 1))
            self.keys.append(key)
            return 201, key
        return 500, {}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = make_env(self.tmp.name)
        self.keys_dir = self.env["KEYS_DIR"]
        self.server = FakeServer()
        self.out = []

    def tearDown(self):
        self.tmp.cleanup()

    def main(self, *argv):
        return setup_index.main(list(argv), http=self.server, environ=self.env, out=self.out.append)

    def read_key(self, name):
        path = os.path.join(self.keys_dir, name)
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path) as f:
            return f.read().strip()


class TestSetup(Base):
    def test_first_run(self):
        self.assertEqual(self.main(), 0)
        self.assertEqual([(m, p) for m, p, _ in self.server.writes],
                         [("POST", "/indexes"), ("PATCH", "/indexes/messages/settings"),
                          ("POST", "/keys"), ("POST", "/keys")])
        self.assertEqual(self.server.writes[0][2], {"uid": "messages", "primaryKey": "id"})
        settings = self.server.writes[1][2]
        self.assertEqual(settings["rankingRules"][0], "sort")
        self.assertEqual(settings["sortableAttributes"], ["date"])
        self.assertEqual(settings["filterableAttributes"], ["folder", "date", "from", "has_attachments"])
        self.assertEqual(settings["searchableAttributes"], ["subject", "from", "attachments", "body", "to", "cc"])
        self.assertIsNone(settings["localizedAttributes"])
        search, status = self.server.writes[2][2], self.server.writes[3][2]
        self.assertEqual((search["description"], search["actions"], search["indexes"], search["expiresAt"]),
                         ("external-search plugin", ["search"], ["messages"], None))
        self.assertEqual((status["description"], status["actions"], status["indexes"]),
                         ("external-search status", ["indexes.get", "stats.get", "settings.get"], ["messages"]))
        self.assertEqual(self.read_key("search.key"), "secret-1")
        self.assertEqual(self.read_key("status.key"), "secret-2")

    def test_key_values_never_printed(self):
        self.main()
        text = "\n".join(self.out)
        self.assertNotIn("secret-", text)
        self.assertIn("wrote " + os.path.join(self.keys_dir, "search.key"), text)

    def test_locales(self):
        self.env["INDEX_LOCALES"] = "eng,deu"
        self.main()
        self.assertEqual(self.server.writes[1][2]["localizedAttributes"],
                         [{"attributePatterns": ["*"], "locales": ["eng", "deu"]}])

    def test_second_run_idempotent(self):
        self.main()
        self.server.writes.clear()
        self.assertEqual(self.main(), 0)
        self.assertEqual([(m, p) for m, p, _ in self.server.writes], [("PATCH", "/indexes/messages/settings")])
        self.assertTrue(any("already present" in line for line in self.out))

    def test_lost_key_file_reuses_existing_key(self):
        self.main()
        os.remove(os.path.join(self.keys_dir, "search.key"))
        self.server.writes.clear()
        self.main()
        self.assertNotIn(("POST", "/keys"), [(m, p) for m, p, _ in self.server.writes])
        self.assertEqual(self.read_key("search.key"), "secret-1")

    def test_other_index_gets_its_own_keys(self):
        self.env["MEILI_INDEX"] = "mail_test"
        self.main()
        key = self.server.writes[2][2]
        self.assertEqual(key["description"], "external-search plugin (mail_test)")
        self.assertEqual(key["indexes"], ["mail_test"])

    def test_failed_settings_task_exit_1(self):
        self.server.task_status[2] = "failed"
        self.assertEqual(self.main(), 1)
        self.assertTrue(any("invalid setting" in line for line in self.out))
        self.assertFalse(os.path.exists(os.path.join(self.keys_dir, "search.key")))

    def test_config_error_exit_2(self):
        os.remove(self.env["MEILI_MASTER_KEY_FILE"])
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.server.writes, [])

    def test_imap_settings_not_needed(self):
        del self.env["IMAP_HOST"]
        self.assertEqual(self.main(), 0)


class TestCheck(Base):
    def test_matching_after_setup(self):
        self.main()
        # the engine returns full objects and its own order: still a match
        s = dict(self.server.settings)
        s["filterableAttributes"] = sorted(s["filterableAttributes"])
        s["typoTolerance"] = {"enabled": True, "minWordSizeForTypos": {"oneTypo": 4, "twoTypos": 8},
                              "disableOnWords": [], "disableOnAttributes": []}
        s["localizedAttributes"] = None
        self.server.settings = s
        self.server.writes.clear()
        self.out.clear()
        self.assertEqual(self.main("--check"), 0)
        self.assertEqual(self.server.writes, [])
        self.assertIn("index messages: 12 document(s)", self.out)
        self.assertIn("settings: match", self.out)

    def test_mismatch_exit_1(self):
        self.main()
        self.server.settings = dict(self.server.settings, rankingRules=["words", "sort"])
        self.assertEqual(self.main("--check"), 1)
        self.assertTrue(any("MISMATCH in rankingRules" in line for line in self.out))

    def test_locales_mismatch(self):
        self.main()
        self.env["INDEX_LOCALES"] = "eng"
        self.assertEqual(self.main("--check"), 1)

    def test_missing_index(self):
        self.assertEqual(self.main("--check"), 1)
        self.assertIn("index messages: missing", self.out)

    def test_unhealthy(self):
        self.server.healthy = False
        self.assertEqual(self.main("--check"), 1)


if __name__ == "__main__":
    unittest.main()
