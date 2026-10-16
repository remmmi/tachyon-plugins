#!/usr/bin/env python3
"""Create and configure the Meilisearch index for the external-search plugin, and its API keys.

Usage:
  python3 setup_index.py [--env-file F]           create the index if missing, apply the settings, write the keys
  python3 setup_index.py [--env-file F] --check   print health, document count and whether the settings match

Idempotent. Keys are written to KEYS_DIR/search.key (actions: search) and KEYS_DIR/status.key (actions:
indexes.get, stats.get, settings.get), both limited to MEILI_INDEX, mode 600, only when the file is absent.
Key values are never printed.
Exit codes: 0 ok, 1 Meilisearch error or (with --check) settings mismatch, 2 configuration error.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
from meili import DEFAULT_INDEX, Meili, MeiliError, real_http  # noqa: E402

BASE_SETTINGS = {
    "searchableAttributes": ["subject", "from", "attachments", "body", "to", "cc"],
    "filterableAttributes": ["folder", "date", "from", "has_attachments"],
    "sortableAttributes": ["date"],
    # `sort` first: a sort asked by the query (date:desc, the plugin's "newest first") wins over relevance instead of
    # only breaking ties; without a sort in the query the rule does nothing and relevance ranks as usual
    "rankingRules": ["sort", "words", "typo", "proximity", "attributeRank", "wordPosition", "exactness"],
    "displayedAttributes": ["id", "folder", "uidvalidity", "uid", "date", "subject", "from"],
    "typoTolerance": {"minWordSizeForTypos": {"oneTypo": 4, "twoTypos": 8}},
    "pagination": {"maxTotalHits": 5000},
}

# Compared as sets by --check (the engine may return them in another order)
UNORDERED = ("filterableAttributes", "sortableAttributes", "displayedAttributes")

KEYS = {
    "search.key": {"description": "external-search plugin", "actions": ["search"]},
    "status.key": {"description": "external-search status",
                   "actions": ["indexes.get", "stats.get", "settings.get"]},
}


class SetupError(RuntimeError):
    pass


def settings_for(locales):
    """Index settings; `localizedAttributes` from INDEX_LOCALES (None resets it when no locale is given)."""
    s = dict(BASE_SETTINGS)
    s["localizedAttributes"] = [{"attributePatterns": ["*"], "locales": list(locales)}] if locales else None
    return s


def key_description(base, index):
    return base if index == DEFAULT_INDEX else "%s (%s)" % (base, index)


def write_600(path, content):
    """Write a file with mode 600, never through a wider mode."""
    os.makedirs(os.path.dirname(path) or ".", mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(content)
    os.chmod(path, 0o600)


def _same_key(k, description, actions, index):
    return (k.get("description") == description and sorted(k.get("actions") or []) == sorted(actions)
            and (k.get("indexes") or []) == [index])


def setup(http, index, locales, keys_dir, out=print):
    """Create the index if missing, apply the settings, create the missing key files. Returns written paths."""
    meili = Meili("", None, http=http, index=index)
    status, _ = http("GET", "/indexes/" + index)
    if status == 404:
        status, resp = http("POST", "/indexes", {"uid": index, "primaryKey": "id"})
        if status >= 300:
            raise SetupError("index creation refused (HTTP %d)" % status)
        meili.wait(resp["taskUid"])
        out("index %s created" % index)
    elif status != 200:
        raise SetupError("cannot read index %s (HTTP %d)" % (index, status))

    status, resp = http("PATCH", "/indexes/%s/settings" % index, settings_for(locales))
    if status >= 300:
        raise SetupError("settings refused (HTTP %d)" % status)
    meili.wait(resp["taskUid"])  # raises MeiliError if the task fails
    out("settings applied to %s" % index)

    written, existing = [], None
    for filename, spec in KEYS.items():
        path = os.path.join(keys_dir, filename)
        if os.path.exists(path):
            out("%s already present, kept" % path)
            continue
        if existing is None:
            status, listing = http("GET", "/keys?limit=1000")
            if status != 200:
                raise SetupError("cannot list keys (HTTP %d)" % status)
            existing = listing.get("results", [])
        description = key_description(spec["description"], index)
        value = next((k["key"] for k in existing if _same_key(k, description, spec["actions"], index)), None)
        if value is None:
            status, created = http("POST", "/keys", {
                "description": description, "actions": spec["actions"], "indexes": [index], "expiresAt": None,
            })
            if status >= 300:
                raise SetupError("key creation refused (HTTP %d)" % status)
            value = created["key"]
            existing.append(created)
        write_600(path, value + "\n")
        written.append(path)
        out("wrote %s" % path)
    return written


def _matches(expected, actual):
    """Expected settings must be found in the actual ones; dicts are compared on the expected keys only."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(_matches(v, actual.get(k)) for k, v in expected.items())
    return expected == actual


def settings_mismatches(expected, actual):
    """Names of the settings whose value differs from the expected one."""
    diff = []
    for name, want in expected.items():
        got = actual.get(name)
        if name in UNORDERED:
            same = isinstance(got, list) and sorted(map(str, got)) == sorted(map(str, want))
        elif want is None:
            same = got in (None, [])
        else:
            same = _matches(want, got)
        if not same:
            diff.append(name)
    return diff


def check(http, index, locales, out=print):
    """Print health, document count and settings match. Returns True when everything is as expected."""
    status, health = http("GET", "/health")
    healthy = status == 200 and health.get("status") == "available"
    out("health: %s" % ("available" if healthy else "unavailable (HTTP %d)" % status))
    if not healthy:
        return False
    status, stats = http("GET", "/indexes/%s/stats" % index)
    if status == 404:
        out("index %s: missing" % index)
        return False
    if status != 200:
        out("index %s: cannot read stats (HTTP %d)" % (index, status))
        return False
    out("index %s: %s document(s)%s" % (index, stats.get("numberOfDocuments", "?"),
                                        ", indexing" if stats.get("isIndexing") else ""))
    status, actual = http("GET", "/indexes/%s/settings" % index)
    if status != 200:
        out("settings: cannot read (HTTP %d)" % status)
        return False
    diff = settings_mismatches(settings_for(locales), actual)
    out("settings: %s" % ("match" if not diff else "MISMATCH in " + ", ".join(diff)))
    return not diff


def main(argv=None, http=None, environ=None, out=print):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="only report health, document count and settings match")
    ap.add_argument("--env-file", metavar="F", help="read KEY=value settings from F (environment variables win)")
    args = ap.parse_args(argv)
    env = os.environ if environ is None else environ
    try:
        if args.env_file:
            config.load_env_file(args.env_file, env)
        cfg = config.load(env, require_imap=False)
    except config.ConfigError as e:
        out("configuration error: %s" % e)
        return 2
    if http is None:
        http = real_http(cfg.meili_master_key, cfg.meili_url)
    try:
        if args.check:
            return 0 if check(http, cfg.meili_index, cfg.index_locales, out=out) else 1
        setup(http, cfg.meili_index, cfg.index_locales, cfg.keys_dir, out=out)
        return 0
    except (SetupError, MeiliError, OSError, ValueError, KeyError) as e:
        out("FAILED: %s: %s" % (type(e).__name__, e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
