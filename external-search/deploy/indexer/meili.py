"""Minimal Meilisearch HTTP client (standard library only). Keys are never printed."""
import json
import time
import urllib.error
import urllib.request

DEFAULT_INDEX = "messages"


class MeiliError(RuntimeError):
    """Request refused, or asynchronous task failed."""


def real_http(key, url):
    """Build the transport function http(method, path, body=None) -> (status, json)."""
    url = url.rstrip("/")

    def http(method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(url + path, data=data, method=method)
        if key:
            req.add_header("Authorization", "Bearer " + key)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}
    return http


def quote_value(value):
    """A filter string literal, with backslashes and double quotes escaped."""
    return '"%s"' % value.replace("\\", "\\\\").replace('"', '\\"')


def filter_equals(field, value):
    """Filter expression `field = "value"`, escaped."""
    return "%s = %s" % (field, quote_value(value))


class Meili:
    def __init__(self, url, key, http=None, pause=0.2, max_wait=600, index=DEFAULT_INDEX):
        self.http = http or real_http(key, url)
        self.index = index
        self.pause = pause
        self.max_wait = max_wait

    def _write(self, method, path, body):
        status, resp = self.http(method, path, body)
        if status >= 300:
            raise MeiliError("%s %s refused (HTTP %d)" % (method, path, status))
        return resp

    def wait(self, task_uid):
        """Wait for a task to finish; raise MeiliError if it fails."""
        start = time.monotonic()
        while True:
            status, task = self.http("GET", "/tasks/%s" % task_uid)
            if status != 200:
                raise MeiliError("cannot read task %s (HTTP %d)" % (task_uid, status))
            state = task.get("status")
            if state == "succeeded":
                return task
            if state in ("failed", "canceled"):
                err = (task.get("error") or {}).get("message", "no detail")
                raise MeiliError("task %s %s: %s" % (task_uid, state, err))
            if time.monotonic() - start > self.max_wait:
                raise MeiliError("task %s: timed out" % task_uid)
            time.sleep(self.pause)

    def add(self, docs):
        """Add or replace documents, and wait for the task."""
        if not docs:
            return
        resp = self._write("POST", "/indexes/%s/documents" % self.index, docs)
        self.wait(resp["taskUid"])

    def delete(self, ids):
        if not ids:
            return
        resp = self._write("POST", "/indexes/%s/documents/delete-batch" % self.index, list(ids))
        self.wait(resp["taskUid"])

    def _delete_by_filter(self, expression):
        resp = self._write("POST", "/indexes/%s/documents/delete" % self.index, {"filter": expression})
        self.wait(resp["taskUid"])

    def delete_folder(self, folder):
        """Delete every document of a folder (by filter, without listing ids)."""
        self._delete_by_filter(filter_equals("folder", folder))

    def delete_outside_folders(self, folders):
        """Delete the documents whose folder is not in `folders`."""
        self._delete_by_filter("NOT folder IN [%s]" % ", ".join(quote_value(f) for f in folders))

    def folder_ids(self, folder):
        """Set of the `id`s indexed for this folder (paged by 1 000)."""
        ids, offset = set(), 0
        while True:
            resp = self._write("POST", "/indexes/%s/documents/fetch" % self.index, {
                "filter": filter_equals("folder", folder),
                "fields": ["id"], "limit": 1000, "offset": offset,
            })
            batch = resp.get("results", [])
            ids.update(d["id"] for d in batch)
            offset += len(batch)
            if not batch or offset >= resp.get("total", 0):
                return ids
