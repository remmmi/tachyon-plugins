# AGENTS.md — installing full-text search for a Tachyon webmail

You are a coding agent helping someone install `external-search` (a Tachyon plugin) with its search engine
(Meilisearch) and its indexer (IMAP → Meilisearch), on a server you do not know. Your job: discover the system, ask
the right questions with their trade-offs, deliver a **working keyword search**, and leave everything ready for
**hybrid search** (keywords + meaning) if the person wants it later.

## Where things are

The repository holds one folder per plugin; this file is in `external-search/`, and every path below is relative to
it. The folder is the plugin itself (`index.php`, `lib/`, `langs/`, `tests/`, its `README.md`) with, next to the
plugin files, what only the installation needs: `AGENTS.md`, `deploy/` (engine and indexer) and `docs/`.

- Clone the repository **outside** Tachyon's data folder (for example `/opt/tachyon-plugins`). `deploy/` will hold
  keys and the index data; Tachyon deletes a plugin's folder when the plugin is reinstalled or updated from the admin
  panel, so nothing of the installation may live inside it.
- What goes into Tachyon is the plugin files only (step 5): the release zip, or this folder without `deploy/`,
  `docs/`, `AGENTS.md` and `tests/`.

Read this whole file before acting. Then read, as needed: `README.md` (the plugin),
`docs/footprint.md` (what it costs), `docs/document-schema.md` (the index contract), `docs/hybrid.md` (embeddings),
`docs/history.md` (what was tried, measured and learnt, with dates), `deploy/install.sh` (the steps).

## Hard rules

1. **Never modify the code of Tachyon or SnappyMail**, neither a file nor the image. Everything goes through the
   plugin, its settings, `application.ini`, or the kit in `deploy/`. If something seems to need a core patch, stop,
   explain it to the person, and suggest an issue upstream (https://github.com/kimusan/Tachyon/issues).
2. **Never write to the mailbox.** The indexer only reads (`EXAMINE`, `BODY.PEEK`). Do not test by moving, flagging
   or deleting mail.
3. **No key or password in a versioned file or in a message.** Secrets live in `deploy/secrets/` and
   `deploy/keys/` (mode 600, ignored by git). Refer to a key by its file, never print it. The webmail gets the
   search-only key and, optionally, the read-only status key; **never the master key**.
4. **Offer the order of the results** (step 8 below), with its trade-off.
5. **Compare the server with the measured footprint** before installing anything (step 2), and warn when it is
   constrained.
6. When it is constrained, **offer to measure the load for a few hours** with `deploy/load_probe.sh` before going on.
7. The person **may skip these checks** if they say they are sure. Then note it in one line ("Footprint check skipped
   at the user's request") and go on.
8. **Ask before installing each piece**: Docker, the Meilisearch image or binary, the indexer container, a cron
   entry, a systemd unit, a package. One explicit yes per piece. Silence, or a yes to another piece, is not a yes.

## How to ask

Every question to the person:

- is **explicit** (a question of its own, never buried in a paragraph);
- is in **their language** (the language of their messages; if unsure, ask that first);
- carries **one decision**;
- gives **2 to 4 options**, each with what it implies: **cost** (time, money, disk, RAM), **risk**, **reversibility**;
- marks **one recommendation** and says why.

Example:

> **Where should the search engine run?**
> 1. **In Docker, next to Tachyon** (recommended: Tachyon already runs in Docker here) — one more container, 2 GB RAM
>    limit, about 3.5 GB of disk for your mailbox; removed with `docker compose down -v`.
> 2. **As a binary with a systemd unit** — no Docker needed; you update it by hand; removed by deleting the unit and
>    the data folder.

## Order of work

### 1. Discover (read only)

Find out, and report in a short list:

- **Tachyon**: where it runs (Docker container? which image tag?), its version (`/tachyon/tachyon/v/<version>`
  inside the image, or the `v/` folder of a plain install), its data folder (`_data_/_default_/plugins/`), and, if in
  Docker, its network (`docker inspect <container> --format '{{json .NetworkSettings.Networks}}'`).
- **Docker**: present? Compose v2? Can the current user run it?
- **Server**: CPUs, RAM (total and available), free disk where the engine data would go, load average.
- **Mailbox**: IMAP host, port, user (ask the person), the number of messages and folders. A quick count:
  `STATUS <folder> (MESSAGES)` per folder, or the webmail's folder list.
- **IMAP `MULTISEARCH`**: if the server announces it in `CAPABILITY`, the native search can already search every
  folder at once. The plugin still adds typo tolerance, ranking and speed, but the gain is smaller: say so.
- **The core entry point**: whether the core has `MailClient::MessageListByTuples` (grep the core's
  `MailSo/Mail/MailClient.php`). Without it, the plugin uses a protected method, "best effort": tell the person a
  Tachyon upgrade may break search until the plugin is updated (it falls back to the native search meanwhile).

### 2. Footprint

Project the index from `docs/footprint.md` (about 35 KB per message, keyword only). Flag the server as constrained
if any of: available RAM < 2 × the engine's memory limit (2 GB by default); free disk < 3 × the projected index;
15-minute load above the CPU count. If constrained, ask whether to run `deploy/load_probe.sh <hours> <dir>` first
(rule 6), or to lower the limits (`MEILI_MEM_LIMIT`, `MEILI_MAX_INDEXING_MEMORY` in `deploy/.env`: slower first
fill, same search speed), or to go on anyway (rule 7).

### 3. Questions

Ask, one at a time, only those whose answer you could not discover:

- **Docker or binary** for the engine (and whether to install Docker, if absent).
- **Which mailbox** to index, and where its password comes from (it goes in `deploy/secrets/imap_password`).
- **Folders to leave out** (`EXCLUDE_FOLDERS`: spam, trash, very large archives nobody searches).
- **Languages** of the mail (`INDEX_LOCALES`): it improves word splitting and stemming.
- **Pace**: incremental run every 5 minutes (default, a few seconds each when nothing is new) or slower.
- **Body length**: 20 000 characters per message by default. Longer = bigger index, rarely better results.
- **Attachments**: their *names* are indexed, their *content* is not. Extracting text from PDF and Office files is a
  separate project (a converter, more CPU at indexing time, much bigger index); say it is possible, not shipped.
- **Status key**: let the admin window show the document count and the index settings (a read-only key), or not.

### 4. Install, piece by piece, each with consent

```bash
cd deploy                                       # from external-search/
./install.sh check                              # nothing installed: Docker, Compose, port, disk
./install.sh init --tachyon-network <network>   # folders, .env, indexer.env, master key (omit the flag without Docker Tachyon)
# the person (or you, with their consent) fills indexer.env and secrets/imap_password
./install.sh engine                             # starts Meilisearch, creates the index and the two keys
./install.sh indexer                            # a 50-message test run, then the indexer service
./install.sh status
```

Without Docker: run Meilisearch as a binary (https://www.meilisearch.com/docs/learn/self_hosted/install_meilisearch_locally)
under a systemd unit with the same environment as `deploy/compose.yml`, and the indexer from cron:
`python3 indexer/indexer.py --env-file indexer.env --incremental` every 5 minutes and `--reconcile` once a night
(Python 3.9+, standard library only). Exit codes: 0 ok, 1 a folder failed, 2 configuration error, 3 already running.
`data/state/status.json` (or `STATE_DIR/status.json`) holds the last run's result.

The first fill runs in the background; `./install.sh status` shows progress (document count).

### 5. Install the plugin

Copy the plugin files, and only them, into Tachyon's plugin folder as `_data_/_default_/plugins/external-search/`
(the folder name sets the plugin class; in Docker, through the container or its data volume, as the owner of that
folder). Either unzip the release (https://github.com/remmmi/tachyon-plugins/releases, `external-search-<version>.zip`,
plugin files only), or, from the clone:

```bash
rsync -a --exclude deploy --exclude docs --exclude AGENTS.md --exclude tests ./ <plugins folder>/external-search/
```

Never copy `deploy/`: it now holds keys and index data. Enable the plugin in *Admin → Extensions*, then set: engine URL
(`http://meilisearch:7700` on the shared Docker network, else `http://127.0.0.1:7700`), API key (content of
`deploy/keys/search.key`), status key (content of `deploy/keys/status.key`, optional), index name. Let the person
paste the keys themselves if they prefer: you never need to see them.

### 6. Verify

- `./install.sh status`: engine answers, index settings match, last indexer run ok.
- The plugin's admin window shows the engine status (opened after saving the settings).
- In the webmail: search a word you know is in an old message of another folder. Results come from several folders,
  fast. Then stop the engine (`docker compose stop meilisearch`), search again: the native search answers and the
  search field shows a dashed outline for 5 seconds. Start the engine again.

### 7. Hand over

Tell the person, in a few lines: what runs where, how much disk and RAM it takes now, how to see its health
(`./install.sh status`, the admin window), how to stop or remove it (`./install.sh down`; `docker compose down -v`
and deleting `deploy/data/` removes everything).

### 8. Offer the order of the results

The plugin can show a result page in three orders (`default_order`, and a switch above the list):

- **grouped by folder** — the native look; good when folders mean something (a well-sorted mailbox);
- **newest first** — reads like a timeline; best for a mailbox nobody sorts consistently; every word of the query
  is required; needs the `sort` ranking rule first (the kit sets it);
- **most relevant first** — the engine's ranking; best when you search a topic rather than a recent message.

The switch (`order-switch.js`, setting *Show the order switch*) lets each person flip between newest and most
relevant; the choice is kept per browser. Ask which default they want and whether to show the switch.

### 9. Offer hybrid search (only after keyword search works)

Explain, without overselling: hybrid search also finds messages whose **meaning** is close to the query when the
words differ ("delivery delay" finds "the parcel is late"), across languages. It does **not** help for names,
order numbers, references or exact phrases, which keyword search already finds; for many mailboxes that is most
searches. It costs money and care: every message sent to an embedding provider (or a local model), a few euros for
tens of thousands of mails with a hosted model when done well, more if the body is not trimmed, and again in full if
you change models. `docs/hybrid.md` has the numbers measured here, the procedure and the pitfalls.

If they want it: follow `docs/hybrid.md` to choose the provider, configure the embedder on the index (with the
master key, from the server, never from the webmail), decide what to vectorise, vectorise in batches, then tick
*Hybrid search* in the plugin and set the embedder name. If not: nothing to do; it can be added at any time.

## When something goes wrong

- **Search falls back to native every time**: the admin window's engine status says why (engine unreachable, key
  refused, index missing). From the Tachyon container, the engine URL must resolve (`wget -qO- <url>/health`).
- **Results are missing recent mail**: `data/state/status.json`, then `docker compose logs indexer`.
- **A page of results is short**: messages moved or deleted since indexing; the nightly reconciliation fixes it.
  The Tachyon log shows `external-search: indexed message not on the mail server`.
- **After a Tachyon upgrade, search falls back**: check that `MessageListByTuples` (or the protected
  `MessageListMultiFolderFetch`) still exists in the core, and that the plugin version is current.
- **The plugin vanished from Tachyon after an update from the admin panel**: Tachyon replaces the whole plugin
  folder; copy the plugin files again (step 5). Nothing of the installation lived there, so nothing else is lost.
- Anything that would need a change in the core: stop (rule 1).
