# tachyon-plugins

Plugins for the [Tachyon](https://github.com/kimusan/Tachyon) webmail (a maintained fork of SnappyMail), written
for a small team that works in one shared mailbox all day, and published as they are tested in production.

| Plugin | Version | What it does |
|---|---|---|
| [`external-search`](external-search/) | 1.0 | Answers the message search box from Meilisearch: every folder at once, typo tolerant, ranked, optionally hybrid (keywords + meaning), with automatic fallback to the native IMAP search |

More plugins will follow once they are tested well enough (conversation view, attachment preview…).

Each plugin has its own folder: the plugin files at its top, and next to them its deployment kit (`deploy/`),
documentation (`docs/`) and agent guide (`AGENTS.md`) when it has some. The release zips hold the plugin files only.
A coding agent can start from [`AGENTS.md`](AGENTS.md), which sends it to the plugin's own guide.

## License

GNU Affero General Public License v3 or later, see [LICENSE](LICENSE). Copyright (C) 2026 remmmi.
