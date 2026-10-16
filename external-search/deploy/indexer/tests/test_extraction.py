"""Extraction: one message -> one document, never an exception."""
import base64
import hashlib
import unittest
from email.message import EmailMessage

from helpers import headers, part, structure

from extraction import choose_part, doc_id, extract, minimal_doc

FIELDS = {"id", "folder", "uidvalidity", "uid", "date", "from", "to", "cc", "subject", "body",
          "attachments", "has_attachments"}


def doc_from(msg, **kw):
    bs = structure(msg)
    number = choose_part(bs)
    text = part(msg, number) if number else None
    return extract("INBOX", 5, 42, headers(msg), bs, text, 1700000000, **kw)


def base(**h):
    m = EmailMessage()
    m["From"] = h.get("sender", "Alice Example <alice@example.com>")
    m["To"] = "mailbox@example.com"
    m["Subject"] = h.get("subject", "Hello")
    return m


class TestExtraction(unittest.TestCase):
    def test_id_is_sha1_of_folder_uidvalidity_uid(self):
        self.assertEqual(doc_id("INBOX", 5, 42), hashlib.sha1(b"INBOX|5|42").hexdigest())
        self.assertEqual(doc_id("Bestätigungen", 1, 2), hashlib.sha1("Bestätigungen|1|2".encode("utf-8")).hexdigest())

    def test_schema_fields_only(self):
        m = base()
        m.set_content("plain")
        self.assertEqual(set(doc_from(m)), FIELDS)
        self.assertEqual(set(minimal_doc("INBOX", 1, 1, 0)), FIELDS)

    def test_plain_text_utf8(self):
        m = base()
        m.set_content("Grüße aus München, schöne Tage", charset="utf-8")
        d = doc_from(m)
        self.assertEqual(d["body"].strip(), "Grüße aus München, schöne Tage")
        self.assertEqual(d["from"], "Alice Example <alice@example.com>")
        self.assertEqual(d["subject"], "Hello")
        self.assertEqual((d["folder"], d["uidvalidity"], d["uid"], d["date"]), ("INBOX", 5, 42, 1700000000))
        self.assertEqual(d["attachments"], [])
        self.assertFalse(d["has_attachments"])
        self.assertEqual(d["id"], doc_id("INBOX", 5, 42))

    def test_html_only(self):
        m = base()
        m.set_content("<html><head><style>p{color:red}</style><script>alert(1)</script></head>"
                      "<body><p>First</p><div>Second &amp; <b>bold</b><br>end</div></body></html>", subtype="html")
        d = doc_from(m)
        self.assertNotIn("<", d["body"])
        self.assertNotIn("color", d["body"])
        self.assertNotIn("alert", d["body"])
        self.assertIn("Second & bold", d["body"])
        self.assertIn("First\n", d["body"])

    def test_windows_1252_quoted_printable(self):
        m = base()
        m.set_content("Preis: 12 € — Größe", charset="cp1252", cte="quoted-printable")
        d = doc_from(m)
        self.assertIn("Größe", d["body"])
        self.assertIn("€", d["body"])

    def test_missing_charset(self):
        raw = "Bestätigung erhalten".encode("cp1252")
        d = extract("INBOX", 1, 1, b"Subject: x\r\n\r\n", '("TEXT" "PLAIN" NIL NIL NIL "8BIT" 20 1)', raw, 0)
        self.assertIn("Bestätigung erhalten", d["body"])
        d = extract("INBOX", 1, 1, b"Subject: x\r\n\r\n", '("TEXT" "PLAIN" NIL NIL NIL "8BIT" 21 1)',
                    "Bestätigung".encode("utf-8"), 0)
        self.assertIn("Bestätigung", d["body"])

    def test_unknown_charset(self):
        d = extract("INBOX", 1, 1, b"Subject: x\r\n\r\n",
                    '("TEXT" "PLAIN" ("CHARSET" "x-unknown") NIL NIL "7BIT" 5 1)', b"hello", 0)
        self.assertEqual(d["body"].strip(), "hello")

    def test_pdf_attachment(self):
        m = base()
        m.set_content("See the invoice")
        m.add_attachment(b"%PDF-1.4 " + bytes(range(256)) * 20, maintype="application", subtype="pdf",
                         filename="invoice.pdf")
        d = doc_from(m)
        self.assertEqual(d["attachments"], ["invoice.pdf"])
        self.assertTrue(d["has_attachments"])
        self.assertIn("See the invoice", d["body"])
        self.assertNotIn("JVBER", d["body"])

    def test_plain_preferred_over_html(self):
        m = base()
        m.set_content("text version")
        m.add_alternative("<p>html version</p>", subtype="html")
        self.assertIn("text version", doc_from(m)["body"])

    def test_html_without_plain_next_to_attachment(self):
        m = EmailMessage()
        m["Subject"] = "s"
        m.set_content("<p>only html</p>", subtype="html")
        m.add_attachment(b"zip", maintype="application", subtype="zip", filename="a.zip")
        d = doc_from(m)
        self.assertIn("only html", d["body"])
        self.assertEqual(d["attachments"], ["a.zip"])

    def test_rfc2047_subject(self):
        d = extract("INBOX", 1, 1, b"Subject: =?utf-8?q?Best=C3=A4tigung_Hotel?=\r\n\r\n", None, None, 0)
        self.assertEqual(d["subject"], "Bestätigung Hotel")

    def test_decoded_addresses(self):
        d = extract("INBOX", 1, 1,
                    b"From: =?utf-8?q?J=C3=BCrgen?= <juergen@example.com>\r\n"
                    b"To: a@example.com, b@example.com\r\nCc: c@example.com\r\n\r\n", None, None, 0)
        self.assertEqual(d["from"], "Jürgen <juergen@example.com>")
        self.assertIn("b@example.com", d["to"])
        self.assertEqual(d["cc"], "c@example.com")

    def test_body_capped_at_default(self):
        m = base()
        m.set_content("a" * 50000)
        self.assertEqual(len(doc_from(m)["body"]), 20000)

    def test_body_cap_configurable(self):
        m = base()
        m.set_content("a" * 500)
        self.assertEqual(len(doc_from(m, body_max_chars=100)["body"]), 100)

    def test_unreadable(self):
        d = extract("INBOX", 3, 4, b"\xff\xfe\x00 not headers", "((((", b"\x00\xff", 99)
        self.assertEqual((d["subject"], d["from"], d["to"], d["cc"]), ("", "", "", ""))
        self.assertEqual((d["uid"], d["date"], d["attachments"], d["has_attachments"]), (4, 99, [], False))

    def test_truncated_base64(self):
        raw = base64.b64encode("ü".encode() * 100)[:-3]  # cut in the middle
        d = extract("INBOX", 1, 1, b"Subject: x\r\n\r\n",
                    '("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "BASE64" 200 1)', raw, 0)
        self.assertTrue(d["body"].startswith("üü"))

    def test_rfc2047_attachment_name(self):
        bs = ('(("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "7BIT" 3 1)'
              '("APPLICATION" "PDF" ("NAME" "=?utf-8?q?offer_M=C3=A4rz=2Epdf?=") NIL NIL "BASE64" 10) "MIXED")')
        d = extract("INBOX", 1, 1, b"Subject: x\r\n\r\n", bs, b"abc", 0)
        self.assertEqual(d["attachments"], ["offer März.pdf"])

    def test_rfc2231_attachment_name(self):
        bs = ('(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 3 1)'
              '("APPLICATION" "PDF" NIL NIL NIL "BASE64" 10 NIL ("ATTACHMENT" ("FILENAME*" "utf-8\'\'Gr%C3%BC%C3%9Fe.pdf")) NIL) "MIXED")')
        d = extract("INBOX", 1, 1, b"Subject: x\r\n\r\n", bs, b"abc", 0)
        self.assertEqual(d["attachments"], ["Grüße.pdf"])

    def test_inline_logo_ignored_real_attachment_kept(self):
        m = EmailMessage()
        m["Subject"] = "s"
        m.set_content("<p>hello <img src='cid:logo'></p>", subtype="html")
        m.add_related(b"\x89PNG" * 50, maintype="image", subtype="png", cid="<logo@example.com>",
                      filename="logo.png", disposition="inline")
        m.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="x.pdf")
        d = doc_from(m)
        self.assertEqual(d["attachments"], ["x.pdf"])
        self.assertTrue(d["has_attachments"])

    def test_inline_logo_alone_is_no_attachment(self):
        m = EmailMessage()
        m["Subject"] = "s"
        m.set_content("<p>hello</p>", subtype="html")
        m.add_related(b"\x89PNG" * 50, maintype="image", subtype="png", cid="<logo@example.com>",
                      filename="logo.png", disposition="inline")
        d = doc_from(m)
        self.assertEqual(d["attachments"], [])
        self.assertFalse(d["has_attachments"])

    def test_image_without_disposition_or_cid_ignored(self):
        bs = '(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 3 1)("IMAGE" "GIF" ("NAME" "pixel.gif") NIL NIL "BASE64" 10) "MIXED")'
        d = extract("INBOX", 1, 1, b"Subject: x\r\n\r\n", bs, b"abc", 0)
        self.assertFalse(d["has_attachments"])

    def test_real_attachment_without_name(self):
        bs = '(("TEXT" "PLAIN" NIL NIL NIL "7BIT" 3 1)("APPLICATION" "OCTET-STREAM" NIL NIL NIL "BASE64" 10) "MIXED")'
        d = extract("INBOX", 1, 1, b"Subject: x\r\n\r\n", bs, b"abc", 0)
        self.assertTrue(d["has_attachments"])
        self.assertEqual(d["attachments"], [])

    def test_mixed_with_alternative_part_1_1(self):
        m = base()
        m.set_content("text")
        m.add_alternative("<p>html</p>", subtype="html")
        m.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="a.pdf")
        self.assertEqual(choose_part(structure(m)), "1.1")
        d = doc_from(m)
        self.assertIn("text", d["body"])
        self.assertEqual(d["attachments"], ["a.pdf"])

    def test_html_only_in_nested_alternative_part_1_2(self):
        bs = ('((("APPLICATION" "X-FOO" NIL NIL NIL "7BIT" 1)("TEXT" "HTML" ("CHARSET" "utf-8") NIL NIL "7BIT" 9 1) "ALTERNATIVE")'
              '("APPLICATION" "PDF" ("NAME" "a.pdf") NIL NIL "BASE64" 10) "MIXED")')
        self.assertEqual(choose_part(bs), "1.2")


if __name__ == "__main__":
    unittest.main()
