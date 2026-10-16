"""Configuration: defaults, validation, env file, secrets read from files only."""
import os
import tempfile
import unittest

from helpers import make_env

import config
from config import ConfigError


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = make_env(self.tmp.name)
        self.warnings = []

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, **kw):
        return config.load(self.env, warn=self.warnings.append, **kw)

    def assertInvalid(self, name, value, fragment=None):
        self.env[name] = value
        with self.assertRaises(ConfigError) as cm:
            self.load()
        self.assertIn(fragment or name, str(cm.exception))


class TestDefaults(Base):
    def test_defaults(self):
        env = {k: v for k, v in self.env.items()
               if k in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD_FILE", "MEILI_MASTER_KEY_FILE")}
        c = config.load(env)
        self.assertEqual((c.imap_port, c.imap_tls), (993, True))
        self.assertEqual((c.meili_url, c.meili_index), ("http://meilisearch:7700", "messages"))
        self.assertEqual((c.index_locales, c.exclude_folders), ([], []))
        self.assertEqual((c.body_max_chars, c.batch_size, c.batch_pause), (20000, 1000, 0.5))
        self.assertEqual((c.state_dir, c.keys_dir), ("/state", "/keys"))
        self.assertEqual((c.interval_seconds, c.reconcile_at), (300, "03:00"))
        self.assertEqual(config.DEFAULTS["IMAP_PASSWORD_FILE"], "/run/secrets/imap_password")
        self.assertEqual(config.DEFAULTS["MEILI_MASTER_KEY_FILE"], "/run/secrets/meili_master_key")

    def test_secrets_read_from_files(self):
        c = self.load()
        self.assertEqual((c.imap_password, c.meili_master_key), ("test-password", "test-master-key"))

    def test_secrets_not_in_repr(self):
        text = repr(self.load())
        self.assertNotIn("test-password", text)
        self.assertNotIn("test-master-key", text)

    def test_lists(self):
        self.env.update(INDEX_LOCALES=" eng, DEU ,", EXCLUDE_FOLDERS="Junk, Trash ,Bestätigungen")
        c = self.load()
        self.assertEqual(c.index_locales, ["eng", "deu"])
        self.assertEqual(c.exclude_folders, ["Junk", "Trash", "Bestätigungen"])

    def test_booleans_and_url(self):
        self.env.update(IMAP_TLS="No", MEILI_URL="https://search.example.com:7700/")
        c = self.load()
        self.assertFalse(c.imap_tls)
        self.assertEqual(c.meili_url, "https://search.example.com:7700")

    def test_reconcile_at_normalised(self):
        self.env["RECONCILE_AT"] = "4:05"
        self.assertEqual(self.load().reconcile_at, "04:05")


class TestValidation(Base):
    def test_required(self):
        for name in ("IMAP_HOST", "IMAP_USER"):
            env = dict(self.env)
            env[name] = " "
            with self.assertRaises(ConfigError) as cm:
                config.load(env)
            self.assertIn("%s is required" % name, str(cm.exception))

    def test_imap_not_required_for_setup(self):
        del self.env["IMAP_HOST"]
        os.remove(self.env["IMAP_PASSWORD_FILE"])
        c = self.load(require_imap=False)
        self.assertEqual(c.meili_master_key, "test-master-key")

    def test_invalid_values(self):
        cases = [
            ("IMAP_PORT", "imap"), ("IMAP_PORT", "0"), ("IMAP_PORT", "70000"),
            ("IMAP_TLS", "maybe"), ("MEILI_URL", "meilisearch:7700"), ("MEILI_INDEX", "my index"),
            ("INDEX_LOCALES", "en"), ("INDEX_LOCALES", "eng,english"), ("BODY_MAX_CHARS", "0"),
            ("BATCH_SIZE", "-1"), ("BATCH_PAUSE", "fast"), ("BATCH_PAUSE", "-1"), ("BATCH_PAUSE", "nan"),
            ("INTERVAL_SECONDS", "0"), ("RECONCILE_AT", "3h"), ("RECONCILE_AT", "24:00"),
            ("RECONCILE_AT", "03:60"), ("STATE_DIR", ""), ("KEYS_DIR", " "),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                env = dict(self.env)
                env[name] = value
                with self.assertRaises(ConfigError) as cm:
                    config.load(env)
                self.assertIn(name, str(cm.exception))

    def test_missing_secret_file(self):
        os.remove(self.env["MEILI_MASTER_KEY_FILE"])
        with self.assertRaises(ConfigError) as cm:
            self.load()
        self.assertIn("MEILI_MASTER_KEY_FILE", str(cm.exception))

    def test_empty_secret_file(self):
        with open(self.env["IMAP_PASSWORD_FILE"], "w") as f:
            f.write("\n")
        with self.assertRaises(ConfigError) as cm:
            self.load()
        self.assertIn("IMAP_PASSWORD_FILE", str(cm.exception))

    def test_secret_first_line_kept_verbatim(self):
        with open(self.env["IMAP_PASSWORD_FILE"], "w") as f:
            f.write(" pass with spaces \nsecond line\n")
        self.assertEqual(self.load().imap_password, " pass with spaces ")

    def test_password_variable_ignored_with_warning(self):
        self.env["IMAP_PASSWORD"] = "should-not-be-used"
        c = self.load()
        self.assertEqual(c.imap_password, "test-password")
        self.assertEqual(len(self.warnings), 1)
        self.assertNotIn("should-not-be-used", self.warnings[0])

    def test_skip_secrets(self):
        os.remove(self.env["IMAP_PASSWORD_FILE"])
        self.assertIsNone(self.load(read_secrets=False).imap_password)


class TestEnvFile(Base):
    def write(self, content):
        path = os.path.join(self.tmp.name, "indexer.env")
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_parsing_and_environment_wins(self):
        path = self.write("# comment\n\nIMAP_HOST=file.example.com\nexport MEILI_INDEX='mail'\n"
                          "EXCLUDE_FOLDERS=\"Junk, Trash\"\nEMPTY=\n")
        env = {"IMAP_HOST": "env.example.com"}
        config.load_env_file(path, env)
        self.assertEqual(env, {"IMAP_HOST": "env.example.com", "MEILI_INDEX": "mail",
                               "EXCLUDE_FOLDERS": "Junk, Trash", "EMPTY": ""})

    def test_malformed_line(self):
        path = self.write("IMAP_HOST=imap.example.com\nnot a setting\n")
        with self.assertRaises(ConfigError) as cm:
            config.load_env_file(path, {})
        self.assertIn("line 2", str(cm.exception))

    def test_missing_file(self):
        with self.assertRaises(ConfigError):
            config.load_env_file(os.path.join(self.tmp.name, "absent.env"), {})


if __name__ == "__main__":
    unittest.main()
