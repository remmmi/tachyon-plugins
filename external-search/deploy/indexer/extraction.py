"""Extraction: one IMAP message (headers + BODYSTRUCTURE + one text part) -> one Meilisearch document.

Never raises: an unreadable message gives a minimal document (still findable by folder and date).
"""
import base64
import binascii
import hashlib
import quopri
import re
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from html.parser import HTMLParser
from urllib.parse import unquote

BODY_MAX_CHARS = 20000


def doc_id(folder, uidvalidity, uid):
    """Primary key: sha1("<folder>|<uidvalidity>|<uid>") in hex, folder in UTF-8."""
    return hashlib.sha1(("%s|%s|%s" % (folder, uidvalidity, uid)).encode("utf-8")).hexdigest()


# ---------- BODYSTRUCTURE: a small IMAP s-expression parser ----------

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def parse_structure(text):
    """'(("TEXT" "PLAIN" ...) ...)' -> nested lists (str, None for NIL). Raises ValueError if malformed."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    pos, stack, root = 0, [], None
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip():
                raise ValueError("unreadable BODYSTRUCTURE")
            break
        pos = m.end()
        opening, closing, quoted, atom = m.groups()
        if opening:
            stack.append([])
        elif closing:
            if not stack:
                raise ValueError("unbalanced closing parenthesis")
            done = stack.pop()
            if stack:
                stack[-1].append(done)
            else:
                root = done
        else:
            if not stack:
                raise ValueError("atom outside a list")
            if quoted is not None:
                stack[-1].append(re.sub(r"\\(.)", r"\1", quoted))
            else:
                stack[-1].append(None if atom.upper() == "NIL" else atom)
    if stack or root is None:
        raise ValueError("incomplete BODYSTRUCTURE")
    return root


def _decode_mime(value):
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _params(items):
    """Flat list [k, v, k, v] -> lower-case dict; handles simple RFC 2231 parameters (`name*`)."""
    res = {}
    if not isinstance(items, list):
        return res
    for i in range(0, len(items) - 1, 2):
        key, val = items[i], items[i + 1]
        if not isinstance(key, str) or not isinstance(val, str):
            continue
        key = key.lower()
        if key.endswith("*"):
            key = key[:-1]
            m = re.match(r"([^']*)'[^']*'(.*)$", val)
            if m:
                val = unquote(m.group(2), encoding=m.group(1) or "utf-8", errors="replace")
        res[key] = val
    return res


def _leaves(node, prefix=""):
    """Depth-first walk: (part number, type, subtype, params, encoding, disposition, disposition params, content-id)."""
    if node and isinstance(node[0], list):  # multipart
        i = 0
        while i < len(node) and isinstance(node[i], list):
            sub = "%s%d" % (prefix, i + 1)
            yield from _leaves(node[i], sub + ".")
            i += 1
        return
    if len(node) < 7 or not isinstance(node[0], str):
        return
    typ, sub = node[0].lower(), (node[1] or "").lower()
    # fields: type subtype params id description encoding size [lines (text) | envelope body lines (message)] md5 disp ...
    ext_start = 8 if typ == "text" else (10 if (typ, sub) == ("message", "rfc822") else 7)
    disp, disp_params = None, {}
    if len(node) > ext_start + 1 and isinstance(node[ext_start + 1], list):
        d = node[ext_start + 1]
        disp = (d[0] or "").lower() if d and isinstance(d[0], str) else None
        disp_params = _params(d[1]) if len(d) > 1 else {}
    number = prefix[:-1] if prefix else "1"
    encoding = (node[5] or "7bit").lower() if isinstance(node[5], str) else "7bit"
    yield number, typ, sub, _params(node[2]), encoding, disp, disp_params, node[3]


def _analyse(bodystructure):
    """-> (chosen text part (number, subtype, params, encoding) or None, attachment names, has real attachments)."""
    tree = parse_structure(bodystructure)
    plain = html = None
    names, real = [], False
    for number, typ, sub, params, enc, disp, disp_params, cid in _leaves(tree):
        name = disp_params.get("filename") or params.get("name")
        # signature logos and embedded images (Content-ID, or an image without an "attachment" disposition)
        # are not attachments
        embedded = disp != "attachment" and (cid is not None or typ == "image")
        is_attachment = (disp == "attachment" or typ != "text") and not embedded
        if typ == "text" and disp != "attachment":
            if sub == "plain" and plain is None:
                plain = (number, sub, params, enc)
            elif sub == "html" and html is None:
                html = (number, sub, params, enc)
            continue
        if is_attachment:
            real = True
            if name:
                names.append(_decode_mime(name))
    return (plain or html), names, real


def choose_part(bodystructure):
    """Number of the part to download (first text/plain, else first text/html), or None."""
    try:
        choice = _analyse(bodystructure)[0]
    except Exception:
        return "1" if bodystructure is None else None
    return choice[0] if choice else None


# ---------- body ----------

class _HtmlText(HTMLParser):
    BREAKS = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in self.BREAKS:
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BREAKS:
            self.chunks.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.chunks.append(data)


def html_to_text(html):
    p = _HtmlText()
    p.feed(html)
    p.close()
    return "".join(p.chunks)


def _normalise(text):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode_transfer(data, encoding):
    if encoding == "base64":
        clean = re.sub(rb"[^A-Za-z0-9+/]", b"", data)
        clean = clean[:len(clean) - len(clean) % 4]  # the part may be truncated by a partial FETCH
        try:
            return base64.b64decode(clean)
        except (binascii.Error, ValueError):
            return b""
    if encoding == "quoted-printable":
        return quopri.decodestring(data)
    return data


def _decode_charset(data, charset):
    if charset:
        try:
            return data.decode(charset, "replace")
        except LookupError:
            pass
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


def _body(text, bodystructure, max_chars):
    if not text:
        return ""
    sub, params, enc = "plain", {}, "7bit"
    if bodystructure is not None:
        try:
            choice = _analyse(bodystructure)[0]
            if choice:
                _, sub, params, enc = choice
        except Exception:
            pass
    raw = _decode_transfer(text, enc)
    decoded = _decode_charset(raw, params.get("charset"))
    if sub == "html":
        decoded = html_to_text(decoded)
    return _normalise(decoded)[:max_chars]


# ---------- document ----------

def _header(msg, name):
    try:
        v = msg[name]
        return " ".join(str(v).split()) if v is not None else ""
    except Exception:
        return ""


def minimal_doc(folder, uidvalidity, uid, internaldate):
    """Document without content: fallback when extraction fails (still findable by folder and date)."""
    return {
        "id": doc_id(folder, uidvalidity, uid), "folder": folder,
        "uidvalidity": uidvalidity, "uid": uid, "date": internaldate,
        "from": "", "to": "", "cc": "", "subject": "", "body": "",
        "attachments": [], "has_attachments": False,
    }


def extract(folder, uidvalidity, uid, raw_headers, bodystructure, text, internaldate, body_max_chars=BODY_MAX_CHARS):
    """Build the document of one message. `text` is the raw (transfer-encoded) bytes of the chosen part."""
    doc = minimal_doc(folder, uidvalidity, uid, internaldate)
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw_headers or b"", headersonly=True)
        for field in ("from", "to", "cc", "subject"):
            doc[field] = _header(msg, field)
    except Exception:
        pass
    try:
        if bodystructure is not None:
            _, names, real = _analyse(bodystructure)
            doc["attachments"] = names
            doc["has_attachments"] = real
    except Exception:
        pass
    try:
        doc["body"] = _body(text, bodystructure, body_max_chars)
    except Exception:
        pass
    return doc
