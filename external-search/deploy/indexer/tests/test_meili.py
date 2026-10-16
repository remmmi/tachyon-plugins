"""Meilisearch client: fake in-memory HTTP transport, no network."""
import unittest

import helpers  # noqa: F401  (import path)

from meili import Meili, MeiliError, filter_equals


class FakeHttp:
    def __init__(self, docs=None, task_status="succeeded"):
        self.calls = []
        self.docs = docs or []
        self.task_status = task_status
        self.pending = 0  # number of "processing" answers before the end

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.startswith("/tasks/"):
            if self.pending > 0:
                self.pending -= 1
                return 200, {"status": "processing"}
            resp = {"status": self.task_status}
            if self.task_status == "failed":
                resp["error"] = {"message": "disk full"}
            return 200, resp
        if path.endswith("/documents/fetch"):
            start, lim = body["offset"], body["limit"]
            return 200, {"results": self.docs[start:start + lim], "total": len(self.docs)}
        return 202, {"taskUid": 7}


class TestMeili(unittest.TestCase):
    def test_index_is_configurable(self):
        h = FakeHttp()
        Meili("u", "k", http=h, pause=0, index="mail_test").add([{"id": "a"}])
        self.assertEqual(h.calls[0][:2], ("POST", "/indexes/mail_test/documents"))

    def test_add_waits_for_the_task(self):
        h = FakeHttp()
        h.pending = 2
        Meili("u", "k", http=h, pause=0).add([{"id": "a"}])
        self.assertEqual(h.calls[0][:2], ("POST", "/indexes/messages/documents"))
        self.assertEqual(len([c for c in h.calls if c[1] == "/tasks/7"]), 3)

    def test_failed_add_raises(self):
        with self.assertRaises(MeiliError) as cm:
            Meili("u", "k", http=FakeHttp(task_status="failed"), pause=0).add([{"id": "a"}])
        self.assertIn("disk full", str(cm.exception))

    def test_failed_delete_raises(self):
        with self.assertRaises(MeiliError):
            Meili("u", "k", http=FakeHttp(task_status="failed"), pause=0).delete(["a"])

    def test_delete_sends_ids(self):
        h = FakeHttp()
        Meili("u", "k", http=h, pause=0).delete(["a", "b"])
        self.assertEqual(h.calls[0], ("POST", "/indexes/messages/documents/delete-batch", ["a", "b"]))

    def test_empty_list_no_call(self):
        h = FakeHttp()
        m = Meili("u", "k", http=h, pause=0)
        m.add([])
        m.delete([])
        self.assertEqual(h.calls, [])

    def test_folder_ids_paged_and_escaped(self):
        h = FakeHttp(docs=[{"id": "i%d" % n} for n in range(2500)])
        ids = Meili("u", "k", http=h, pause=0).folder_ids('Lab"el\\x')
        self.assertEqual(len(ids), 2500)
        self.assertEqual(len(h.calls), 3)
        self.assertEqual(h.calls[0][2]["filter"], 'folder = "Lab\\"el\\\\x"')

    def test_http_error_raises(self):
        with self.assertRaises(MeiliError):
            Meili("u", "k", http=lambda *a: (500, {}), pause=0).add([{"id": "a"}])

    def test_task_timeout_raises(self):
        h = FakeHttp()
        h.pending = 10 ** 6
        with self.assertRaises(MeiliError):
            Meili("u", "k", http=h, pause=0, max_wait=0).add([{"id": "a"}])

    def test_filter_equals(self):
        self.assertEqual(filter_equals("folder", "INBOX"), 'folder = "INBOX"')

    def test_delete_outside_folders(self):
        h = FakeHttp()
        Meili("u", "k", http=h, pause=0).delete_outside_folders(["INBOX", 'A"b'])
        self.assertEqual(h.calls[0], ("POST", "/indexes/messages/documents/delete",
                                      {"filter": 'NOT folder IN ["INBOX", "A\\"b"]'}))
        self.assertTrue(any(c[1] == "/tasks/7" for c in h.calls))

    def test_delete_folder_by_filter(self):
        h = FakeHttp()
        Meili("u", "k", http=h, pause=0).delete_folder("INBOX")
        self.assertEqual(h.calls[0][2], {"filter": 'folder = "INBOX"'})

    def test_failed_delete_by_filter_raises(self):
        with self.assertRaises(MeiliError):
            Meili("u", "k", http=FakeHttp(task_status="failed"), pause=0).delete_folder("INBOX")


if __name__ == "__main__":
    unittest.main()
