#!/usr/bin/env python3
"""IMAP -> Meilisearch indexer: per-folder incremental sync and nightly reconciliation.

Usage:
  python3 indexer.py --incremental            index the messages whose UID is above the last indexed one
  python3 indexer.py --reconcile              compare IMAP and the index: remove what is gone, add what is missing,
                                              drop folders that no longer exist (or are excluded)
  python3 indexer.py --incremental --limit N  stop after N documents (to measure)
  python3 indexer.py --env-file F ...         read KEY=value settings from F first (environment variables win)

IMAP is read only (EXAMINE, BODY.PEEK); attachments are never downloaded.
Exit codes: 0 ok, 1 at least one folder (or the run) failed, 2 configuration error, 3 another run holds the lock.
Writes STATE_DIR/folders.json (per-folder UIDVALIDITY and last UID) and STATE_DIR/status.json (health of the last run).
"""
import argparse
import base64
import datetime
import errno
import fcntl
import imaplib
import json
import os
import re
import ssl
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from extraction import BODY_MAX_CHARS, choose_part, doc_id, extract, minimal_doc  # noqa: E402
from meili import Meili  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_LOCKED = 0, 1, 2, 3
SKIPPED_FLAGS = ("\\noselect", "\\nonexistent")


def log(message):
    """One line per event on stdout. Never message content (no subject, no address)."""
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def now_iso():
    return datetime.datetime.now().astimezone().replace(microsecond=0).isoformat()


def imap_utf7_to_utf8(name):
    """IMAP modified UTF-7 (RFC 3501 section 5.1.3) -> UTF-8, as webmails decode folder names.
    `&-` is a literal "&"; `&...-` is UTF-16BE in base64 (with "," instead of "/")."""
    def block(m):
        if m.group(1) == "":
            return "&"
        b64 = m.group(1).replace(",", "/")
        try:
            return base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("utf-16-be")
        except (ValueError, UnicodeDecodeError):
            return m.group(0)  # invalid sequence: left as is
    return re.sub(r"&([^-]*)-", block, name)


def _write_json(path, data):
    """Atomic write: temporary file, fsync, rename."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class FolderState:
    """Per-folder state {folder: {"uidvalidity": n, "last_uid": n}}, written atomically."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)

    def get(self, folder):
        entry = self.data.get(folder) or {}
        return entry.get("uidvalidity"), entry.get("last_uid", 0)

    def set(self, folder, uidvalidity, last_uid):
        self.data[folder] = {"uidvalidity": uidvalidity, "last_uid": last_uid}

    def forget(self, folder):
        self.data.pop(folder, None)

    def save(self):
        _write_json(self.path, self.data)


def _docs(folder, uidvalidity, batch, body_max_chars):
    """Extract a batch; a message that raises gives a minimal document and the batch goes on."""
    docs = []
    for uid, headers, bodystructure, text, internaldate in batch:
        try:
            docs.append(extract(folder, uidvalidity, uid, headers, bodystructure, text, internaldate,
                                body_max_chars=body_max_chars))
        except Exception as e:
            log("%s: extraction failed for uid %s (%s), indexed without content" % (folder, uid, type(e).__name__))
            docs.append(minimal_doc(folder, uidvalidity, uid, internaldate))
    return docs


def sync_folder(imap, meili, state, folder, raw=None, limit=None, batch_size=1000, pause=0.5,
                body_max_chars=BODY_MAX_CHARS):
    """Index the UIDs above the last indexed one. `folder` is the UTF-8 name (index, state), `raw` the IMAP name
    for EXAMINE/FETCH (default: `folder`). Returns the number of documents indexed."""
    raw = folder if raw is None else raw
    uidvalidity, _ = imap.examine(raw)
    previous, last = state.get(folder)
    if previous is not None and previous != uidvalidity:
        log("%s: UIDVALIDITY changed %s -> %s, reindexing the folder" % (folder, previous, uidvalidity))
        meili.delete_folder(folder)
        last = 0
        state.set(folder, uidvalidity, 0)
        state.save()
    uids = imap.uids(raw, last)
    if limit is not None:
        uids = uids[:limit]
    total = 0
    for i in range(0, len(uids), batch_size):
        batch = uids[i:i + batch_size]
        received = list(imap.fetch(raw, batch))
        missing = sorted(set(batch) - {r[0] for r in received})
        if missing:
            log("%s: %d UID(s) not returned by FETCH, left to the next reconciliation: %s"
                % (folder, len(missing), ",".join(map(str, missing[:20])) + (",..." if len(missing) > 20 else "")))
        docs = _docs(folder, uidvalidity, received, body_max_chars)
        meili.add(docs)  # waits for the task: an error raises before the state moves on
        state.set(folder, uidvalidity, max(batch))
        state.save()
        total += len(docs)
        if pause and i + batch_size < len(uids):
            time.sleep(pause)
    return total


def reconcile_folder(imap, meili, folder, raw=None, batch_size=1000, pause=0.5, body_max_chars=BODY_MAX_CHARS):
    """Align the index on IMAP for one folder. Returns (removed, added)."""
    raw = folder if raw is None else raw
    uidvalidity, _ = imap.examine(raw)
    expected = {doc_id(folder, uidvalidity, u): u for u in imap.uids(raw, 0)}
    present = meili.folder_ids(folder)
    extra = sorted(present - set(expected))
    missing = sorted(expected[i] for i in set(expected) - present)
    meili.delete(extra)
    for i in range(0, len(missing), batch_size):
        meili.add(_docs(folder, uidvalidity, imap.fetch(raw, missing[i:i + batch_size]), body_max_chars))
        if pause and i + batch_size < len(missing):
            time.sleep(pause)
    return len(extra), len(missing)


def purge_vanished_folders(meili, state, listed, keep):
    """Remove from the index (and the state) every folder not in `keep`.
    Guard: the LIST result `listed` must contain INBOX, or nothing is removed (a truncated LIST must not wipe the
    index)."""
    if "INBOX" not in listed:
        log("LIST did not return INBOX: purge of vanished folders skipped")
        return
    meili.delete_outside_folders(keep)
    for folder in [f for f in list(state.data) if f not in keep]:
        state.forget(folder)
        log("%s: folder gone from IMAP or excluded, removed from the index" % folder)
    state.save()


def run(cfg, reconcile, imap, meili, limit=None):
    """One pass over every folder. Returns (failed folder names, other error count, documents indexed)."""
    failed, errors, indexed = [], 0, 0
    opts = {"batch_size": cfg.batch_size, "pause": cfg.batch_pause, "body_max_chars": cfg.body_max_chars}
    excluded = set(cfg.exclude_folders)
    state = FolderState(os.path.join(cfg.state_dir, "folders.json"))
    folders = [(imap_utf7_to_utf8(raw), raw, flags) for raw, flags in imap.list_folders()]
    remaining = limit
    for folder, raw, flags in folders:
        if any(f.lower() in SKIPPED_FLAGS for f in flags) or folder in excluded:
            continue
        if remaining is not None and remaining <= 0:
            break
        try:
            if reconcile:
                removed, added = reconcile_folder(imap, meili, folder, raw, **opts)
                indexed += added
                if removed or added:
                    log("%s: %d removed, %d added" % (folder, removed, added))
            else:
                n = sync_folder(imap, meili, state, folder, raw, limit=remaining, **opts)
                indexed += n
                if n:
                    log("%s: %d indexed" % (folder, n))
                if remaining is not None:
                    remaining -= n
        except Exception as e:
            failed.append(folder)
            log("FAILED %s: %s: %s" % (folder, type(e).__name__, e))
    if reconcile:
        try:
            listed = [f for f, _, _ in folders]
            purge_vanished_folders(meili, state, listed, [f for f in listed if f not in excluded])
        except Exception as e:
            errors += 1
            log("FAILED purge of vanished folders: %s: %s" % (type(e).__name__, e))
    return failed, errors, indexed


def _read_status(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_status(state_dir, mode, started, ok, failed, indexed):
    """STATE_DIR/status.json: health of the last run, readable without parsing logs."""
    path = os.path.join(state_dir, "status.json")
    previous = _read_status(path)
    try:
        failures = int(previous.get("consecutive_failures", 0))
    except (TypeError, ValueError):
        failures = 0
    finished = now_iso()
    status = {
        "mode": mode,
        "started": started,
        "finished": finished,
        "ok": ok,
        "failed_folders": failed,
        "indexed": indexed,
        "consecutive_failures": 0 if ok else failures + 1,
        "last_ok": finished if ok else previous.get("last_ok"),
    }
    _write_json(path, status)
    return status


def _acquire_lock(state_dir):
    """Exclusive non-blocking lock on STATE_DIR/.lock. Returns the open file, or None if another run holds it."""
    f = open(os.path.join(state_dir, ".lock"), "a")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        f.close()
        if e.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
            return None
        raise
    return f


def main(argv=None, imap=None, meili=None, environ=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--incremental", action="store_true", help="index new messages (UID above the last indexed)")
    mode.add_argument("--reconcile", action="store_true",
                      help="remove documents whose message is gone, add missing ones, drop vanished folders")
    ap.add_argument("--limit", type=int, metavar="N", help="incremental only: stop after N documents")
    ap.add_argument("--env-file", metavar="F", help="read KEY=value settings from F (environment variables win)")
    args = ap.parse_args(argv)
    mode_name = "reconcile" if args.reconcile else "incremental"

    env = os.environ if environ is None else environ
    try:
        if args.env_file:
            config.load_env_file(args.env_file, env)
        cfg = config.load(env)
        os.makedirs(cfg.state_dir, exist_ok=True)
    except config.ConfigError as e:
        log("configuration error: %s" % e)
        return EXIT_CONFIG
    except OSError as e:
        log("configuration error: STATE_DIR %s is not usable (%s)" % (env.get("STATE_DIR", ""), e.strerror))
        return EXIT_CONFIG

    try:
        lock = _acquire_lock(cfg.state_dir)
    except OSError as e:
        log("configuration error: cannot create the lock file in STATE_DIR (%s)" % e.strerror)
        return EXIT_CONFIG
    if lock is None:
        log("%s skipped: another run holds the lock" % mode_name)
        return EXIT_LOCKED

    started = now_iso()
    failed, errors, indexed = [], 0, 0
    try:
        log("%s started" % mode_name)
        try:
            if imap is None:
                imap = ImapClient(cfg.imap_host, cfg.imap_port, cfg.imap_tls, cfg.imap_user, cfg.imap_password,
                                  part_bytes=max(60000, 3 * cfg.body_max_chars))
            if meili is None:
                meili = Meili(cfg.meili_url, cfg.meili_master_key, index=cfg.meili_index)
            failed, errors, indexed = run(cfg, args.reconcile, imap, meili, limit=args.limit)
        except Exception as e:
            errors += 1
            log("FAILED %s: %s: %s" % (mode_name, type(e).__name__, e))
        finally:
            if imap is not None and hasattr(imap, "logout"):
                try:
                    imap.logout()
                except Exception:
                    pass
        ok = not failed and not errors
        try:
            write_status(cfg.state_dir, mode_name, started, ok, failed, indexed)
        except OSError as e:
            ok = False
            log("FAILED writing status.json: %s" % e.strerror)
        log("%s finished: %d document(s) indexed, %d folder(s) failed%s"
            % (mode_name, indexed, len(failed), ", %d other error(s)" % errors if errors else ""))
        return EXIT_OK if ok else EXIT_FAILED
    finally:
        lock.close()


# ---------- real IMAP adapter ----------

_LIST = re.compile(rb'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.*)$', re.S)


def _quote(name):
    return '"%s"' % name.replace("\\", "\\\\").replace('"', '\\"')


class ImapClient:
    """Read only: select(readonly=True) (EXAMINE) and BODY.PEEK. Reconnects once if the connection drops."""

    def __init__(self, host, port, tls, user, password, timeout=120, part_bytes=60000):
        self.host, self.port, self.tls, self.user = host, port, tls, user
        self._password = password
        self.timeout = timeout
        self.part_bytes = part_bytes
        self.m = None
        self.current = None

    def __repr__(self):
        return "ImapClient(%s:%s)" % (self.host, self.port)

    def _connect(self):
        if self.tls:
            self.m = imaplib.IMAP4_SSL(self.host, self.port, ssl_context=ssl.create_default_context(),
                                       timeout=self.timeout)
        else:
            self.m = imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
        self.m.login(self.user, self._password)
        self.current = None

    def _select(self, folder):
        if self.m is None:
            self._connect()
        typ, data = self.m.select(_quote(folder), readonly=True)
        if typ != "OK":
            raise RuntimeError("EXAMINE refused")
        self.current = folder
        return data

    def _retry(self, folder, fn):
        try:
            if self.current != folder:
                self._select(folder)
            return fn()
        except (imaplib.IMAP4.abort, OSError):
            self._connect()
            self._select(folder)
            return fn()

    def logout(self):
        if self.m is not None:
            self.m.logout()
            self.m = None

    def list_folders(self):
        """[(raw IMAP name, [flags])]."""
        if self.m is None:
            self._connect()
        typ, data = self.m.list()
        if typ != "OK":
            raise RuntimeError("LIST refused")
        res = []
        for line in data:
            if isinstance(line, tuple):  # name sent as a literal
                flags = re.search(rb"\(([^)]*)\)", line[0])
                res.append((line[1].decode("ascii", "replace"),
                            flags.group(1).decode().split() if flags else []))
                continue
            if not line or line == b")":
                continue
            m = _LIST.match(line)
            if not m:
                continue
            name = m.group("name").strip()
            if name.startswith(b'"') and name.endswith(b'"'):
                name = re.sub(rb"\\(.)", rb"\1", name[1:-1])
            res.append((name.decode("ascii", "replace"), m.group("flags").decode().split()))
        return res

    def examine(self, folder):
        """-> (UIDVALIDITY, UIDNEXT)."""
        self._select(folder)
        uidvalidity = int(self.m.response("UIDVALIDITY")[1][0])
        uidnext = int(self.m.response("UIDNEXT")[1][0])
        return uidvalidity, uidnext

    def uids(self, folder, since):
        """Sorted UIDs above `since`."""
        def fn():
            typ, data = self.m.uid("SEARCH", None, "UID", "%d:*" % (since + 1))
            if typ != "OK":
                raise RuntimeError("SEARCH refused")
            return sorted(int(x) for x in data[0].split() if int(x) > since)
        return self._retry(folder, fn)

    def fetch(self, folder, uids):
        """Yield (uid, headers, bodystructure, text, internaldate); never downloads an attachment."""
        for i in range(0, len(uids), 200):
            group = uids[i:i + 200]
            for uid, headers, bs, date in self._retry(folder, lambda g=group: self._structures(g)):
                text = None
                number = choose_part(bs) if bs else None
                if number:
                    text = self._retry(folder, lambda u=uid, n=number: self._part(u, n))
                yield uid, headers, bs, text, date

    def _structures(self, group):
        typ, data = self.m.uid("FETCH", ",".join(map(str, group)),
                               "(UID INTERNALDATE BODYSTRUCTURE BODY.PEEK[HEADER])")
        if typ != "OK":
            raise RuntimeError("FETCH refused")
        res, current, headers = [], b"", b""
        for el in data:
            if isinstance(el, tuple):
                current += el[0]
                headers = el[1]
            elif isinstance(el, bytes):
                current += el
                if el.rstrip().endswith(b")"):
                    r = self._parse_response(current, headers)
                    if r:
                        res.append(r)
                    current, headers = b"", b""
        return res

    @staticmethod
    def _parse_response(raw, headers):
        mu = re.search(rb"UID (\d+)", raw)
        if not mu:
            return None
        md = re.search(rb'INTERNALDATE "([^"]+)"', raw)
        date = 0
        if md:
            tup = imaplib.Internaldate2tuple(b'INTERNALDATE "' + md.group(1) + b'"')
            date = int(time.mktime(tup)) if tup else 0  # Internaldate2tuple gives local time
        i = raw.find(b"BODYSTRUCTURE ")
        bs = None
        if i >= 0:
            start, depth, quoted = i + len(b"BODYSTRUCTURE "), 0, False
            for j in range(start, len(raw)):
                c = raw[j:j + 1]
                if c == b'"' and raw[j - 1:j] != b"\\":
                    quoted = not quoted
                elif not quoted and c == b"(":
                    depth += 1
                elif not quoted and c == b")":
                    depth -= 1
                    if depth == 0:
                        bs = raw[start:j + 1].decode("utf-8", "replace")
                        break
        return int(mu.group(1)), headers, bs, date

    def _part(self, uid, number):
        typ, data = self.m.uid("FETCH", str(uid), "(BODY.PEEK[%s]<0.%d>)" % (number, self.part_bytes))
        if typ != "OK":
            raise RuntimeError("FETCH of a part refused")
        for el in data:
            if isinstance(el, tuple):
                return el[1]
        return None


if __name__ == "__main__":
    sys.exit(main())
