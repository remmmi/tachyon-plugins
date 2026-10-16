"""Settings of the indexer, read from environment variables.

Optionally, `load_env_file()` reads a `KEY=value` file first (for running outside Docker); variables already set in
the process environment win over the file. Secrets (IMAP password, Meilisearch master key) are only ever read from
files whose paths are given by `IMAP_PASSWORD_FILE` and `MEILI_MASTER_KEY_FILE`: never from a variable value, never
logged, never part of an error message.
"""
import os
import re
import sys

DEFAULTS = {
    "IMAP_HOST": "",
    "IMAP_PORT": "993",
    "IMAP_TLS": "true",
    "IMAP_USER": "",
    "IMAP_PASSWORD_FILE": "/run/secrets/imap_password",
    "MEILI_URL": "http://meilisearch:7700",
    "MEILI_MASTER_KEY_FILE": "/run/secrets/meili_master_key",
    "MEILI_INDEX": "messages",
    "INDEX_LOCALES": "",
    "EXCLUDE_FOLDERS": "",
    "BODY_MAX_CHARS": "20000",
    "BATCH_SIZE": "1000",
    "BATCH_PAUSE": "0.5",
    "STATE_DIR": "/state",
    "KEYS_DIR": "/keys",
    "INTERVAL_SECONDS": "300",
    "RECONCILE_AT": "03:00",
}

# Variables that would carry a secret value: ignored, with a warning, so nobody believes they are used.
SECRET_VALUE_VARS = ("IMAP_PASSWORD", "IMAP_PASS")

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


class ConfigError(Exception):
    """Missing or invalid setting. The message names the variable, never a secret value."""


class Config:
    """Validated settings. `imap_password` and `meili_master_key` are kept out of repr()."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def __repr__(self):
        shown = {k: v for k, v in self.__dict__.items() if k not in ("imap_password", "meili_master_key")}
        return "Config(%s)" % ", ".join("%s=%r" % kv for kv in sorted(shown.items()))


def load_env_file(path, environ=None):
    """Read `KEY=value` lines into `environ` (default os.environ) without overriding keys already set.

    Blank lines and `#` comments are skipped; an optional `export ` prefix and matching surrounding quotes are
    removed. Raises ConfigError if the file cannot be read or a line is malformed."""
    environ = os.environ if environ is None else environ
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        raise ConfigError("cannot read env file %s (%s)" % (path, e.strerror or type(e).__name__))
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            raise ConfigError("env file %s, line %d: expected KEY=value" % (path, number))
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        environ.setdefault(key, value)
    return environ


def read_secret(path, name):
    """First line of a secret file, without its line ending. The value is never put in a message."""
    if not path:
        raise ConfigError("%s is empty" % name)
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError as e:
        raise ConfigError("%s: cannot read %s (%s)" % (name, path, e.strerror or type(e).__name__))
    lines = content.splitlines()
    value = lines[0].strip("\r\n") if lines else ""
    if not value.strip():
        raise ConfigError("%s: %s is empty" % (name, path))
    return value


def _get(env, name):
    value = env.get(name)
    if value is None:
        value = DEFAULTS[name]
    return value.strip()


def _int(env, name, minimum, maximum=None):
    raw = _get(env, name)
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError("%s must be an integer, got %r" % (name, raw))
    if value < minimum or (maximum is not None and value > maximum):
        bounds = ">= %d" % minimum if maximum is None else "between %d and %d" % (minimum, maximum)
        raise ConfigError("%s must be %s, got %d" % (name, bounds, value))
    return value


def _float(env, name, minimum):
    raw = _get(env, name)
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError("%s must be a number, got %r" % (name, raw))
    if not value >= minimum:  # also rejects nan
        raise ConfigError("%s must be >= %s, got %r" % (name, minimum, raw))
    return value


def _bool(env, name):
    raw = _get(env, name).lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    raise ConfigError("%s must be true or false, got %r" % (name, raw))


def _list(env, name):
    return [item.strip() for item in _get(env, name).split(",") if item.strip()]


def load(environ=None, require_imap=True, read_secrets=True, warn=None):
    """Build a Config from `environ` (default os.environ). Raises ConfigError on the first problem found.

    `require_imap=False` is for tools that only talk to Meilisearch (setup_index.py): the IMAP settings are then
    neither required nor read. `read_secrets=False` skips reading the secret files (validation only)."""
    env = os.environ if environ is None else environ
    if warn is None:
        def warn(message):
            print("config: " + message, file=sys.stderr, flush=True)
    for name in SECRET_VALUE_VARS:
        if env.get(name):
            warn("%s is ignored: put the password in a file and point IMAP_PASSWORD_FILE to it" % name)

    values = {}
    if require_imap:
        for name in ("IMAP_HOST", "IMAP_USER"):
            if not _get(env, name):
                raise ConfigError("%s is required" % name)
        values["imap_host"] = _get(env, "IMAP_HOST")
        values["imap_user"] = _get(env, "IMAP_USER")
        values["imap_port"] = _int(env, "IMAP_PORT", 1, 65535)
        values["imap_tls"] = _bool(env, "IMAP_TLS")
        values["imap_password_file"] = _get(env, "IMAP_PASSWORD_FILE")

    url = _get(env, "MEILI_URL").rstrip("/")
    if not re.match(r"^https?://[^/\s]+", url):
        raise ConfigError("MEILI_URL must start with http:// or https://, got %r" % url)
    values["meili_url"] = url
    index = _get(env, "MEILI_INDEX")
    if not re.match(r"^[A-Za-z0-9_-]{1,400}$", index):
        raise ConfigError("MEILI_INDEX may only contain letters, digits, '-' and '_', got %r" % index)
    values["meili_index"] = index
    values["meili_master_key_file"] = _get(env, "MEILI_MASTER_KEY_FILE")

    locales = [code.lower() for code in _list(env, "INDEX_LOCALES")]
    for code in locales:
        if not re.match(r"^[a-z]{3}$", code):
            raise ConfigError("INDEX_LOCALES must be ISO 639-3 codes such as eng,fra; got %r" % code)
    values["index_locales"] = locales
    values["exclude_folders"] = _list(env, "EXCLUDE_FOLDERS")
    values["body_max_chars"] = _int(env, "BODY_MAX_CHARS", 1)
    values["batch_size"] = _int(env, "BATCH_SIZE", 1)
    values["batch_pause"] = _float(env, "BATCH_PAUSE", 0.0)
    values["interval_seconds"] = _int(env, "INTERVAL_SECONDS", 1)

    reconcile_at = _get(env, "RECONCILE_AT")
    m = re.match(r"^(\d{1,2}):(\d{2})$", reconcile_at)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ConfigError("RECONCILE_AT must be HH:MM (24 h), got %r" % reconcile_at)
    values["reconcile_at"] = "%02d:%s" % (int(m.group(1)), m.group(2))

    for name in ("STATE_DIR", "KEYS_DIR"):
        if not _get(env, name):
            raise ConfigError("%s is empty" % name)
    values["state_dir"] = _get(env, "STATE_DIR")
    values["keys_dir"] = _get(env, "KEYS_DIR")

    values["imap_password"] = None
    values["meili_master_key"] = None
    if read_secrets:
        if require_imap:
            values["imap_password"] = read_secret(values["imap_password_file"], "IMAP_PASSWORD_FILE")
        values["meili_master_key"] = read_secret(values["meili_master_key_file"], "MEILI_MASTER_KEY_FILE")
    return Config(**values)
