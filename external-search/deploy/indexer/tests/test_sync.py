"""Synchronisation, reconciliation, status.json and exit codes: fake IMAP and fake Meilisearch in memory."""
import fcntl
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr

from helpers import make_env

import indexer
from extraction import doc_id
from meili import MeiliError

BS = '("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" 5 1)'


class FakeImap:
    def __init__(self, folders=None):
        # folders: {raw name: {"uidvalidity": int, "uids": [..], "flags": [..]}}
        self.folders = folders or {}
        self.fetches = []
        self.lost = set()
        self.logged_out = False
        self.list_error = None

    def list_folders(self):
        if self.list_error:
            raise self.list_error
        return [(n, f.get("flags", [])) for n, f in self.folders.items()]

    def logout(self):
        self.logged_out = True

    def examine(self, folder):
        f = self.folders[folder]
        return f["uidvalidity"], max(f["uids"], default=0) + 1

    def uids(self, folder, since):
        return sorted(u for u in self.folders[folder]["uids"] if u > since)

    def fetch(self, folder, uids):
        self.fetches.append(list(uids))
        for u in uids:
            if u in self.folders[folder]["uids"] and u not in self.lost:
                yield u, b"Subject: msg %d\r\nFrom: sender@example.com\r\n\r\n" % u, BS, b"body", 1700000000 + u


class FakeMeili:
    def __init__(self):
        self.docs = {}
        self.add_batches = []
        self.deletions = []
        self.fail = False

    def add(self, docs):
        if self.fail:
            raise MeiliError("task 1 failed: disk full")
        self.add_batches.append(len(docs))
        for d in docs:
            self.docs[d["id"]] = d

    def delete_folder(self, folder):
        self.docs = {i: d for i, d in self.docs.items() if d["folder"] != folder}

    def delete_outside_folders(self, folders):
        self.kept = list(folders)
        self.docs = {i: d for i, d in self.docs.items() if d["folder"] in folders}

    def delete(self, ids):
        self.deletions.append(sorted(ids))
        for i in ids:
            self.docs.pop(i, None)

    def folder_ids(self, folder):
        return {i for i, d in self.docs.items() if d["folder"] == folder}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_path = os.path.join(self.tmp.name, "state", "folders.json")
        self.state = indexer.FolderState(self.state_path)
        self.meili = FakeMeili()
        self.lines = []
        self._log = indexer.log
        indexer.log = self.lines.append

    def tearDown(self):
        indexer.log = self._log
        self.tmp.cleanup()

    def sync(self, imap, folder, **kw):
        kw.setdefault("pause", 0)
        return indexer.sync_folder(imap, self.meili, self.state, folder, **kw)


class TestFolderState(Base):
    def test_default_and_atomic_save(self):
        self.assertEqual(self.state.get("INBOX"), (None, 0))
        self.state.set("INBOX", 7, 12)
        self.state.save()
        self.assertEqual(indexer.FolderState(self.state_path).get("INBOX"), (7, 12))
        self.assertEqual(os.listdir(os.path.dirname(self.state_path)), ["folders.json"])
        with open(self.state_path) as f:
            self.assertEqual(json.load(f), {"INBOX": {"uidvalidity": 7, "last_uid": 12}})


class TestSyncFolder(Base):
    def test_first_fill(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 3, "uids": [1, 2, 5]}})
        self.assertEqual(self.sync(imap, "INBOX"), 3)
        self.assertEqual(self.state.get("INBOX"), (3, 5))
        self.assertIn(doc_id("INBOX", 3, 5), self.meili.docs)
        self.assertEqual(self.meili.docs[doc_id("INBOX", 3, 2)]["subject"], "msg 2")
        self.assertEqual(indexer.FolderState(self.state_path).get("INBOX"), (3, 5))  # persisted

    def test_incremental(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 3, "uids": [1, 2]}})
        self.sync(imap, "INBOX")
        imap.folders["INBOX"]["uids"] += [3, 4]
        imap.fetches.clear()
        self.assertEqual(self.sync(imap, "INBOX"), 2)
        self.assertEqual(imap.fetches, [[3, 4]])
        self.assertEqual(self.state.get("INBOX"), (3, 4))

    def test_nothing_new(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 3, "uids": [1]}})
        self.sync(imap, "INBOX")
        self.assertEqual(self.sync(imap, "INBOX"), 0)

    def test_uidvalidity_change_reindexes(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 3, "uids": [1, 2]}})
        self.sync(imap, "INBOX")
        old = set(self.meili.docs)
        imap.folders["INBOX"]["uidvalidity"] = 9
        self.sync(imap, "INBOX")
        self.assertTrue(old.isdisjoint(self.meili.docs))
        self.assertEqual(set(self.meili.docs), {doc_id("INBOX", 9, 1), doc_id("INBOX", 9, 2)})
        self.assertEqual(self.state.get("INBOX"), (9, 2))

    def test_batches(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": list(range(1, 2501))}})
        self.sync(imap, "INBOX")
        self.assertEqual(self.meili.add_batches, [1000, 1000, 500])
        self.assertEqual([len(f) for f in imap.fetches], [1000, 1000, 500])

    def test_batch_size_configurable(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": list(range(1, 8))}})
        self.sync(imap, "INBOX", batch_size=3)
        self.assertEqual(self.meili.add_batches, [3, 3, 1])

    def test_limit(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": list(range(1, 11))}})
        self.assertEqual(self.sync(imap, "INBOX", limit=4), 4)
        self.assertEqual(self.state.get("INBOX"), (1, 4))
        self.assertEqual(self.sync(imap, "INBOX"), 6)

    def test_message_that_raises_gives_minimal_document(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1, 2, 3]}})
        real = indexer.extract

        def trap(folder, uv, uid, *a, **kw):
            if uid == 2:
                raise RuntimeError("boom")
            return real(folder, uv, uid, *a, **kw)
        indexer.extract = trap
        try:
            n = self.sync(imap, "INBOX")
        finally:
            indexer.extract = real
        self.assertEqual(n, 3)
        minimal = self.meili.docs[doc_id("INBOX", 1, 2)]
        self.assertEqual((minimal["subject"], minimal["body"]), ("", ""))
        self.assertEqual(self.meili.docs[doc_id("INBOX", 1, 3)]["subject"], "msg 3")

    def test_meili_failure_does_not_move_the_state(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1, 2]}})
        self.meili.fail = True
        with self.assertRaises(MeiliError):
            self.sync(imap, "INBOX")
        self.assertEqual(self.state.get("INBOX"), (None, 0))

    def test_uids_missing_from_fetch_are_logged(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1, 2, 3]}})
        imap.lost = {2}
        self.sync(imap, "INBOX")
        self.assertTrue(any("not returned by FETCH" in line and ": 2" in line for line in self.lines))


class TestReconcile(Base):
    def test_removes_and_adds(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1, 2, 3]}})
        self.sync(imap, "INBOX")
        imap.folders["INBOX"]["uids"].remove(2)        # moved or deleted on the server
        del self.meili.docs[doc_id("INBOX", 1, 3)]       # missing from the index
        self.assertEqual(indexer.reconcile_folder(imap, self.meili, "INBOX", pause=0), (1, 1))
        self.assertEqual(set(self.meili.docs), {doc_id("INBOX", 1, 1), doc_id("INBOX", 1, 3)})

    def test_leaves_other_folders_alone(self):
        imap = FakeImap({"A": {"uidvalidity": 1, "uids": [1]}, "B": {"uidvalidity": 1, "uids": [1]}})
        for f in ("A", "B"):
            self.sync(imap, f)
        imap.folders["A"]["uids"] = []
        self.assertEqual(indexer.reconcile_folder(imap, self.meili, "A", pause=0), (1, 0))
        self.assertIn(doc_id("B", 1, 1), self.meili.docs)


class TestInternaldate(unittest.TestCase):
    def test_server_time_zone(self):
        previous = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Tokyo"
        time.tzset()
        try:
            raw = b'1 (UID 3 INTERNALDATE " 1-Jan-2026 12:00:00 +0900" BODYSTRUCTURE ("TEXT" "PLAIN" NIL NIL NIL "7BIT" 1 1))'
            uid, _, bs, date = indexer.ImapClient._parse_response(raw, b"")
            self.assertEqual((uid, date), (3, 1767236400))
            self.assertEqual(bs, '("TEXT" "PLAIN" NIL NIL NIL "7BIT" 1 1)')
        finally:
            if previous is None:
                del os.environ["TZ"]
            else:
                os.environ["TZ"] = previous
            time.tzset()


class TestFolderNames(unittest.TestCase):
    def test_modified_utf7_decoding(self):
        d = indexer.imap_utf7_to_utf8
        self.assertEqual(d("Best&AOQ-tigungen"), "Bestätigungen")
        self.assertEqual(d("R&- D"), "R& D")
        self.assertEqual(d("INBOX/Sub folder/&AOQA9gD8-"), "INBOX/Sub folder/äöü")
        self.assertEqual(d("&2D3eAA-"), "\U0001F600")
        self.assertEqual(d("INBOX"), "INBOX")


class MainBase(Base):
    def setUp(self):
        super().setUp()
        self.env = make_env(self.tmp.name)
        self.state_dir = self.env["STATE_DIR"]
        self.state_path = os.path.join(self.state_dir, "folders.json")

    def run_main(self, imap, *argv, env=None):
        return indexer.main(list(argv) or ["--incremental"], imap=imap, meili=self.meili,
                            environ=self.env if env is None else env)

    def status(self):
        with open(os.path.join(self.state_dir, "status.json")) as f:
            return json.load(f)

    def saved_state(self):
        return indexer.FolderState(self.state_path)


class TestMain(MainBase):
    def test_noselect_skipped_exit_0(self):
        imap = FakeImap({
            "INBOX": {"uidvalidity": 1, "uids": [1]},
            "Parent": {"uidvalidity": 1, "uids": [1], "flags": ["\\Noselect"]},
        })
        self.assertEqual(self.run_main(imap), 0)
        self.assertEqual({d["folder"] for d in self.meili.docs.values()}, {"INBOX"})

    def test_nonexistent_skipped_and_logout(self):
        imap = FakeImap({
            "INBOX": {"uidvalidity": 1, "uids": [1]},
            "Ghost": {"uidvalidity": 1, "uids": [1], "flags": ["\\NonExistent"]},
        })
        self.assertEqual(self.run_main(imap), 0)
        self.assertEqual({d["folder"] for d in self.meili.docs.values()}, {"INBOX"})
        self.assertTrue(imap.logged_out)

    def test_excluded_folders_skipped(self):
        self.env["EXCLUDE_FOLDERS"] = "Junk, Bestätigungen"
        imap = FakeImap({
            "INBOX": {"uidvalidity": 1, "uids": [1]},
            "Junk": {"uidvalidity": 1, "uids": [1]},
            "Best&AOQ-tigungen": {"uidvalidity": 1, "uids": [1]},
        })
        self.assertEqual(self.run_main(imap), 0)
        self.assertEqual({d["folder"] for d in self.meili.docs.values()}, {"INBOX"})

    def test_meili_failure_exit_1_and_next_folders_tried(self):
        imap = FakeImap({"A": {"uidvalidity": 1, "uids": [1]}, "B": {"uidvalidity": 1, "uids": [1]}})
        self.meili.fail = True
        self.assertEqual(self.run_main(imap), 1)
        self.assertEqual(sum(1 for line in self.lines if line.startswith("FAILED")), 2)

    def test_global_limit(self):
        imap = FakeImap({"A": {"uidvalidity": 1, "uids": [1, 2, 3]}, "B": {"uidvalidity": 1, "uids": [1, 2, 3]}})
        self.assertEqual(self.run_main(imap, "--incremental", "--limit", "4"), 0)
        self.assertEqual(len(self.meili.docs), 4)

    def test_reconcile_mode(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1, 2]}})
        self.run_main(imap)
        imap.folders["INBOX"]["uids"].remove(1)
        self.assertEqual(self.run_main(imap, "--reconcile"), 0)
        self.assertEqual(set(self.meili.docs), {doc_id("INBOX", 1, 2)})

    def test_reconcile_removes_vanished_folders(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1]}, "Old": {"uidvalidity": 1, "uids": [1]}})
        self.run_main(imap)
        del imap.folders["Old"]
        self.assertEqual(self.run_main(imap, "--reconcile"), 0)
        self.assertEqual({d["folder"] for d in self.meili.docs.values()}, {"INBOX"})
        self.assertEqual(self.saved_state().get("Old"), (None, 0))
        self.assertEqual(self.saved_state().get("INBOX"), (1, 1))

    def test_reconcile_removes_newly_excluded_folders(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1]}, "Junk": {"uidvalidity": 1, "uids": [1]}})
        self.run_main(imap)
        self.env["EXCLUDE_FOLDERS"] = "Junk"
        self.assertEqual(self.run_main(imap, "--reconcile"), 0)
        self.assertEqual({d["folder"] for d in self.meili.docs.values()}, {"INBOX"})
        self.assertEqual(self.saved_state().get("Junk"), (None, 0))

    def test_reconcile_list_without_inbox_removes_nothing(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1]}, "A": {"uidvalidity": 1, "uids": [1]}})
        self.run_main(imap)
        del imap.folders["INBOX"]
        self.assertEqual(self.run_main(imap, "--reconcile"), 0)
        self.assertEqual(len(self.meili.docs), 2)

    def test_reconcile_empty_list_removes_nothing(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1]}})
        self.run_main(imap)
        imap.folders.clear()
        self.run_main(imap, "--reconcile")
        self.assertEqual(len(self.meili.docs), 1)

    def test_utf8_folder_name_in_documents_and_state(self):
        raw = "Best&AOQ-tigungen"
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1]}, raw: {"uidvalidity": 7, "uids": [5]}})
        self.assertEqual(self.run_main(imap), 0)
        self.assertEqual({d["folder"] for d in self.meili.docs.values()}, {"INBOX", "Bestätigungen"})
        self.assertIn(doc_id("Bestätigungen", 7, 5), self.meili.docs)
        self.assertEqual(self.saved_state().get("Bestätigungen"), (7, 5))
        self.assertEqual(self.saved_state().get(raw), (None, 0))

    def test_reconcile_decodes_names_and_purges_raw_ones(self):
        raw = "A&-B &AOQ-/x"
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1]}, raw: {"uidvalidity": 2, "uids": [1, 2]}})
        self.meili.add([{"id": "stale", "folder": raw}])  # an old document under the raw name
        self.assertEqual(self.run_main(imap, "--reconcile"), 0)
        self.assertEqual({d["folder"] for d in self.meili.docs.values()}, {"INBOX", "A&B ä/x"})
        self.assertNotIn("stale", self.meili.docs)

    def test_logs_carry_no_message_content(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1, 2]}})
        self.run_main(imap)
        text = "\n".join(self.lines)
        self.assertNotIn("msg 1", text)
        self.assertNotIn("sender@example.com", text)
        self.assertNotIn("test-password", text)


class TestStatusAndExitCodes(MainBase):
    def test_ok_run_writes_status(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1, 2, 3]}})
        self.assertEqual(self.run_main(imap), 0)
        s = self.status()
        self.assertEqual(set(s), {"mode", "started", "finished", "ok", "failed_folders", "indexed",
                                  "consecutive_failures", "last_ok"})
        self.assertEqual((s["mode"], s["ok"], s["failed_folders"], s["indexed"], s["consecutive_failures"]),
                         ("incremental", True, [], 3, 0))
        self.assertEqual(s["last_ok"], s["finished"])

    def test_failures_counted_and_last_ok_kept(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1]}, "Bestätigungen": {"uidvalidity": 1, "uids": [1]}})
        self.run_main(imap)
        last_ok = self.status()["last_ok"]
        imap.folders["INBOX"]["uids"].append(2)
        imap.folders["Bestätigungen"]["uids"].append(2)
        self.meili.fail = True
        self.assertEqual(self.run_main(imap), 1)
        self.assertEqual(self.run_main(imap, "--reconcile"), 1)
        s = self.status()
        self.assertEqual((s["mode"], s["ok"], s["consecutive_failures"], s["last_ok"]),
                         ("reconcile", False, 2, last_ok))
        self.assertEqual(s["failed_folders"], ["INBOX", "Bestätigungen"])
        self.meili.fail = False
        self.assertEqual(self.run_main(imap), 0)
        self.assertEqual(self.status()["consecutive_failures"], 0)

    def test_imap_failure_before_folders_exit_1(self):
        imap = FakeImap()
        imap.list_error = OSError("connection refused")
        self.assertEqual(self.run_main(imap), 1)
        s = self.status()
        self.assertFalse(s["ok"])
        self.assertIsNone(s["last_ok"])
        self.assertEqual(s["consecutive_failures"], 1)

    def test_lock_held_exit_3_status_untouched(self):
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1]}})
        self.run_main(imap)
        before = self.status()
        with open(os.path.join(self.state_dir, ".lock"), "a") as held:
            fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_main(imap), 3)
        self.assertEqual(self.status(), before)
        self.assertEqual(self.run_main(imap), 0)  # lock released

    def test_config_error_exit_2(self):
        env = dict(self.env)
        del env["IMAP_HOST"]
        self.assertEqual(self.run_main(FakeImap(), env=env), 2)
        self.assertTrue(any("IMAP_HOST is required" in line for line in self.lines))
        self.assertFalse(os.path.exists(os.path.join(self.state_dir, "status.json")))

    def test_missing_secret_file_exit_2(self):
        os.remove(self.env["IMAP_PASSWORD_FILE"])
        self.assertEqual(self.run_main(FakeImap()), 2)

    def test_mode_is_required(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            indexer.main([], imap=FakeImap(), meili=self.meili, environ=self.env)
        self.assertEqual(cm.exception.code, 2)

    def test_env_file(self):
        env_file = os.path.join(self.tmp.name, "indexer.env")
        env = dict(self.env)
        host = env.pop("IMAP_HOST")
        with open(env_file, "w") as f:
            f.write("# settings\nIMAP_HOST=%s\nBATCH_SIZE=1\n" % host)
        imap = FakeImap({"INBOX": {"uidvalidity": 1, "uids": [1, 2]}})
        self.assertEqual(indexer.main(["--env-file", env_file, "--incremental"], imap=imap, meili=self.meili,
                                      environ=env), 0)
        self.assertEqual(self.meili.add_batches, [1, 1])


if __name__ == "__main__":
    unittest.main()
