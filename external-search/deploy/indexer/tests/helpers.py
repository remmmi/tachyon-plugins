"""Test helpers: import path, a temporary settings set, and an EmailMessage cut the way IMAP returns it
(headers, BODYSTRUCTURE, one text part). Synthetic data only."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def make_env(tmp, **overrides):
    """A complete environment for config.load(), with secret files in `tmp`."""
    password = os.path.join(tmp, "imap_password")
    master = os.path.join(tmp, "meili_master_key")
    for path, value in ((password, "test-password\n"), (master, "test-master-key\n")):
        if not os.path.exists(path):
            with open(path, "w") as f:
                f.write(value)
    env = {
        "IMAP_HOST": "imap.example.com",
        "IMAP_USER": "mailbox@example.com",
        "IMAP_PASSWORD_FILE": password,
        "MEILI_MASTER_KEY_FILE": master,
        "STATE_DIR": os.path.join(tmp, "state"),
        "KEYS_DIR": os.path.join(tmp, "keys"),
        "BATCH_PAUSE": "0",
    }
    env.update(overrides)
    return env


# ---------- EmailMessage -> IMAP pieces ----------

def _q(v):
    return "NIL" if v is None else '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')


def _params(p):
    if not p:
        return "NIL"
    return "(" + " ".join(_q(k) + " " + _q(v) for k, v in p) + ")"


def _leaf(part):
    typ, sub = part.get_content_type().split("/")
    params = [(k.upper(), v) for k, v in part.get_params()[1:]]
    enc = (part.get("Content-Transfer-Encoding") or "7bit").upper()
    size = len(part.get_payload() if isinstance(part.get_payload(), str) else b"")
    cid = part.get("Content-ID")
    rest = " " + _q(str(cid) if cid else None) + " NIL " + _q(enc) + " %d" % size
    if typ == "text":
        rest += " 1"
    disp = part.get_content_disposition()
    if disp:
        fn = part.get_filename()
        rest += " NIL (%s %s)" % (_q(disp), _params([("FILENAME", fn)] if fn else None))
    return "(%s %s %s%s)" % (_q(typ.upper()), _q(sub.upper()), _params(params), rest)


def structure(msg):
    """BODYSTRUCTURE string of a message."""
    if msg.is_multipart():
        return "(" + "".join(structure(p) for p in msg.get_payload()) + " " + _q(msg.get_content_subtype().upper()) + ")"
    return _leaf(msg)


def headers(msg):
    raw = msg.as_bytes()
    if b"\r\n\r\n" in raw:
        return raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"
    return raw.split(b"\n\n", 1)[0] + b"\n\n"


def part(msg, number):
    """Raw (transfer-encoded) bytes of part `number` ('1', '2.1'...)."""
    p = msg
    if number != "1" or msg.is_multipart():
        for n in number.split("."):
            p = p.get_payload()[int(n) - 1]
    raw = p.as_bytes()
    return raw.split(b"\n\n", 1)[1] if b"\r\n\r\n" not in raw else raw.split(b"\r\n\r\n", 1)[1]
