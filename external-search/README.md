# external-search

A [Tachyon](https://github.com/kimusan/Tachyon) webmail plugin that answers the message-list search box from an external full-text engine ([Meilisearch](https://www.meilisearch.com/)) instead of the IMAP `SEARCH` command. IMAP `SEARCH` matches exact words only, does not rank, and on a server without `MULTISEARCH` has to be run once per folder; a search engine answers a misspelt query, across every folder, in a few milliseconds. Whenever the engine is absent, slow or failing, the request is handed back to the native search, so the box never stops working.

## What it does

- Intercepts a message-list request that carries a non-empty search and sends it to the plugin's server action instead.
- Queries Meilisearch (typo tolerant, ranked, searches subject, sender, attachment names, body, recipients).
- Returns results from **all folders** of the account at once, rendered by the core's own list code (flags, threads, avatars look exactly like a native list): grouped by folder, or as one flat list newest first or most relevant first, with a two-button switch above the list (see [Order of the results](#order-of-the-results)).
- Understands a few operators (`from:`, `to:`, `before:`, `after:`, `has:attachment`, see below).
- Optionally runs a **hybrid search**: keyword matches fused with semantic matches (messages whose meaning is close to the query, in any language), when the index has an embedder (see [Hybrid search](#hybrid-search-optional)).
- Falls back to the native search on any error, timeout or missing configuration.
- Shows a read-only **engine status** in its admin window: engine reachable, document count, embedders, ranking rules, newest indexed message.

## What it does not do

- **It does not index anything.** The plugin only reads. Something else must write documents into the index (see [Document schema](#document-schema)). Any indexer that produces that schema works; this repository ships one in [`deploy/`](https://github.com/remmmi/tachyon-plugins/blob/main/external-search/deploy) (IMAP → Meilisearch, with a Docker Compose file for both), and [`AGENTS.md`](https://github.com/remmmi/tachyon-plugins/blob/main/external-search/AGENTS.md) guides a coding agent through the whole installation.
- It does not search inside a conversation thread (requests with a thread UID go native).
- It does not touch the empty search, the "unread"/"flagged" filters, or any message-state operator: those stay 100 % native.
- `totalEmails` is the engine's *estimated* total hit count, so pagination totals are approximate.

## Requirements

- Tachyon **4.3.1** or later (plugin API `REQUIRED = 2.36.0`). Tested on 4.3.1, 4.4.2, 4.4.3 and master `5a3dbd9`, on a mailbox of about 35 000 messages in 177 folders.
- PHP with the `curl` extension.
- A reachable Meilisearch (tested with `getmeili/meilisearch:v1.54.3`) holding an index filled as described below.
- **How a result page is built.** The plugin hands the core a list of `[folder, uid]` pairs and lets it fetch and render them:
  - where the core has the public entry point `MailClient::MessageListByTuples()` (Tachyon master since commit `5a3dbd9`, written for this plugin: kimusan/Tachyon#140), the plugin uses it. It keeps the order given and reports the pairs that did not come back;
  - otherwise (Tachyon 4.4.x and older) it calls the *protected* method `MailClient::MessageListMultiFolderFetch()` through a bound closure. This path is **best effort**: a core upgrade may rename or change that method without notice. The call is guarded by `method_exists`; if neither method exists, every search falls back to the native one and a warning is logged.

## Installation

Copy the `external-search` directory of the [release zip](https://github.com/remmmi/tachyon-plugins/releases) (plugin files only) into Tachyon's plugin directory (`data/_data_/_default_/plugins/` for a standard install). From a clone of [the repository](https://github.com/remmmi/tachyon-plugins), copy the `external-search/` folder without `deploy/`, `docs/`, `AGENTS.md` and `tests/`: `deploy/` holds keys and index data once installed, and Tachyon deletes the plugin folder when the plugin is reinstalled from the admin panel, so clone outside Tachyon's data folder. Then enable **External Search** in *Admin → Extensions* and open its settings.

The plugin alone does nothing without a filled index. The fastest path is the kit in [`deploy/`](https://github.com/remmmi/tachyon-plugins/blob/main/external-search/deploy) (Meilisearch + indexer in Docker Compose) or, with a coding agent, [`AGENTS.md`](https://github.com/remmmi/tachyon-plugins/blob/main/external-search/AGENTS.md): it checks the server, asks before installing each part, and explains the trade-offs. The manual steps follow.

`order-switch.js` is optional: delete it to keep the core's list exactly as it is (see [Order of the results](#order-of-the-results)).

## Meilisearch setup

### 1. Run Meilisearch

```bash
docker run -d --name meilisearch \
  -p 127.0.0.1:7700:7700 \
  -e MEILI_MASTER_KEY="$(openssl rand -base64 32)" \
  -e MEILI_NO_ANALYTICS=true \
  -v "$PWD/meili-data:/meili_data" \
  getmeili/meilisearch:v1.54.3
```

Keep the master key private; bind the port to a private interface only. The plugin must reach `url` from the webmail server; browsers never talk to Meilisearch.

### 2. Create the index

```bash
MASTER=...   # the master key
curl -X POST http://127.0.0.1:7700/indexes \
  -H "Authorization: Bearer $MASTER" -H 'Content-Type: application/json' \
  --data '{"uid": "messages", "primaryKey": "id"}'
```

### 3. Apply the index settings

```bash
curl -X PATCH http://127.0.0.1:7700/indexes/messages/settings \
  -H "Authorization: Bearer $MASTER" -H 'Content-Type: application/json' \
  --data '{
  "searchableAttributes": ["subject", "from", "attachments", "body", "to", "cc"],
  "filterableAttributes": ["folder", "date", "from", "has_attachments"],
  "sortableAttributes": ["date"],
  "displayedAttributes": ["id", "folder", "uidvalidity", "uid", "date", "subject", "from"],
  "localizedAttributes": [{"attributePatterns": ["*"], "locales": ["fra"]}],
  "typoTolerance": {"minWordSizeForTypos": {"oneTypo": 4, "twoTypos": 8}},
  "pagination": {"maxTotalHits": 5000}
}'
```

Adjust `locales` to your mailbox language(s) (or drop `localizedAttributes`). The plugin relies on `date` being sortable and filterable, `has_attachments` filterable, and `folder`, `uidvalidity`, `uid` being retrievable. It requests `showRankingScore`, which needs no setting.

### 4. Create a search-only key

```bash
curl -X POST http://127.0.0.1:7700/keys \
  -H "Authorization: Bearer $MASTER" -H 'Content-Type: application/json' \
  --data '{"description": "external-search plugin", "actions": ["search"], "indexes": ["messages"], "expiresAt": null}'
```

Put the returned `key` value in the plugin's *API key* setting. Never give the plugin the master key.

### 5. Optional: a status key

The engine status in the admin window needs more than `search` to show the document count, the embedders and the ranking rules. A key limited to reading does it:

```bash
curl -X POST http://127.0.0.1:7700/keys \
  -H "Authorization: Bearer $MASTER" -H 'Content-Type: application/json' \
  --data '{"description": "external-search status", "actions": ["indexes.get", "stats.get", "settings.get"], "indexes": ["messages"], "expiresAt": null}'
```

Put it in *Status key*. Without it the status shows only whether the engine answers and the newest indexed message.

### Document schema

One document per message, primary key `id`:

| Field | Type | Notes |
|---|---|---|
| `id` | string | `sha1("<folder>\|<uidvalidity>\|<uid>")` (hex); any unique string works |
| `folder` | string | IMAP folder name **in UTF-8**, as Tachyon sees it (`Bestätigungen`, not the modified UTF-7 `Best&AOQ-tigungen` that `LIST` returns): the plugin hands it to the core, which encodes it itself |
| `uidvalidity` | integer | UIDVALIDITY of the folder when indexed |
| `uid` | integer | IMAP UID |
| `date` | integer | message date, Unix epoch seconds (used by `before:`, `after:` and the default sort) |
| `from`, `to`, `cc`, `subject` | string | decoded header text |
| `body` | string | plain text of the body (HTML converted to text; the author's indexer caps it at 20 000 characters) |
| `attachments` | array of strings | attachment file names |
| `has_attachments` | boolean | true for real attachments (not inline logos) |

`uidvalidity` matters: at query time the plugin issues one IMAP `STATUS` per distinct folder of the page and drops hits whose UIDVALIDITY no longer matches (folder recreated, UIDs renumbered). Hits whose message was deleted or moved since indexing come back missing: the rest of the page still renders, each missing message is logged (`external-search: indexed message not on the mail server: folder "…" uid …`), and the response carries `ExternalSearchSkipped` (the number of rows the page is short by). Keep the index in sync with moves and deletions to avoid short pages; the indexer in [`deploy/`](https://github.com/remmmi/tachyon-plugins/blob/main/external-search/deploy) reconciles every night.

## Plugin settings

Set in *Admin → Extensions → External Search*:

| Key | Default | Meaning |
|---|---|---|
| `driver` | `meilisearch` | Search engine driver (only `meilisearch` today) |
| `url` | `http://meilisearch:7700` | Engine URL, reachable from the webmail server |
| `api_key` | *(empty)* | Search-only key. **Empty = plugin inactive**, everything is native |
| `status_key` | *(empty)* | Optional read-only key (`indexes.get`, `stats.get`, `settings.get`) for the engine status |
| `index` | `messages` | Index name |
| `hybrid_enabled` | off | Hybrid search on or off. See *Hybrid search*. (Configurations from before 1.0 have no such key: a non-empty `hybrid_embedder` then means on.) |
| `hybrid_embedder` | *(empty)* | Name of an embedder configured on the index (`settings.embedders`); the engine status lists the ones found |
| `semantic_percent` | `50` | Hybrid search only: weight of meaning vs keywords, `0` = keywords only, `100` = meaning only (Meilisearch `semanticRatio`) |
| `hybrid_max_results` | `200` | Hybrid search only: semantic search ranks *every* document, so the engine reports the whole index as the result count; the plugin caps it so pagination stays meaningful. `0` = no cap |
| `default_order` | `folder` | Order of a page of results: `folder` (grouped by folder), `date` (newest first) or `relevance` (the engine's ranking). See *Order of the results* |
| `show_order_switch` | on | The two buttons above a result page in engine order (needs `order-switch.js`) |
| `timeout_ms` | `800` | Request timeout; floor of 100. Past it, fall back |
| `fallback` | on | Fall back to the native search on error. If off, an error is shown instead |

## Order of the results

By default (`default_order = folder`) a page is grouped by folder, as the core shows any list that spans folders: the
engine's ranking decides which messages are on the page, folders keep the order in which they first appear.

Two other orders put the page back in the order of the engine, as one flat list:

- `relevance`: the engine's ranking;
- `date`: newest first. The query is sent with `sort: ["date:desc"]` and `matchingStrategy: "all"` (every word
  required, or partial matches would bury the full ones), and never as a hybrid search. It needs the `sort` ranking
  rule **first** in the index settings (`rankingRules`), otherwise the date only breaks ties. Dates are those of the
  index (`date` field), which may differ by a few minutes from the `Date:` header a client displays.

A request can ask for either with the parameter `externalOrder` (`date` or `relevance`); it wins over the setting.
`search.js` sends it when the browser has a value under the `localStorage` key `external-search-order`, so any
interface can offer a switch by writing that key and reloading the list. The response tells the client which order
it got in the collection's search scope: `external` (grouped by folder), `external:date` or `external:relevance`.

The core groups any multi-folder list by folder, so with `date` or `relevance` it would show a new folder heading
each time the folder changes. `order-switch.js` turns that grouping off for these pages only: one flat list, each row
tagged with its folder name, and two buttons above it, *Newest first* and *Most relevant first* (setting
`show_order_switch`; the choice is remembered per browser). It replaces the `listGrouped` computed of the core's
`MailMessageList` view, which is the fragile part: re-check it after each core upgrade. It also sets the class
`external-search-order-switch` on `<html>`, so another script can tell that the order is handled. Without
`order-switch.js` the server side works the same.

## Hybrid search (optional)

Meilisearch can fuse keyword matches with *semantic* matches (documents whose meaning is close to the query, even without a common word). The plugin only has to name the embedder; everything else lives in the index:

1. Configure an embedder on the index (`PATCH /indexes/<index>/settings` with an `embedders` object). Any Meilisearch source works (`rest`, `openAi`, `ollama`, `huggingFace`…); with `source: rest` the engine calls your provider itself, at indexing time for the documents and at search time for the query. The author's setup uses a `rest` embedder on Mistral `mistral-embed` with a `documentTemplate` built from `subject`, `from`, `attachments` and `body`. [`docs/hybrid.md`](https://github.com/remmmi/tachyon-plugins/blob/main/external-search/docs/hybrid.md) tells how it runs, what it costs and the pitfalls (what to embed, how to cut long mails). The admin window does not configure embedders: that needs a key that can write, which the webmail should not hold.
2. Tick `hybrid_enabled`, set `hybrid_embedder` to that embedder's name and, if you like, `semantic_percent`. The engine status says whether that name exists on the index.

The plugin then sends `"hybrid": {"embedder": "<name>", "semanticRatio": <percent/100>}` with every free-text query. Queries made only of operators (`from:` alone, `has:attachment`, dates) stay keyword searches sorted by date: there is nothing to embed. Raise `timeout_ms` (the query embedding is one extra HTTP call made by the engine): 3000 ms is a sane start. An embedder name that does not exist on the index makes the engine answer an error, which triggers the fallback like any other error.

If the engine refuses the hybrid request (embedding provider unreachable, out of credit, expired key), the plugin retries once as a plain keyword search and logs `external-search: hybrid search unavailable, keyword results served`: the search box keeps working, only the semantic part is missing. Deciding *which* documents get an embedding is the indexer's job, not the plugin's (Meilisearch lets a document opt out with `"_vectors": {"<embedder>": null}`; the author's indexer skips newsletters, automated notifications and old mail that way).

Two engine behaviours worth knowing: the semantic part is skipped when the keyword results already fill the page with high scores (short pages, exact subjects), so the meaning search shows up mostly when keywords are weak or scarce; and when it does run, it ranks every document, hence `hybrid_max_results`.

## Query operators

Free words go to the engine as the query. Quoted phrases (`"annual report"`) are kept as one term.

| Operator | Example | Effect |
|---|---|---|
| `from:` | `from:dupont`, `from:"Dupont SA"` | Alone: searched in the `from` attribute only. Next to other text: added as one more term (best effort, not an exact filter) |
| `to:` | `to:martin` | Same, on `to` |
| `before:` | `before:2026-03-01` | `date < ` that day, 00:00 in PHP's default timezone (the server's local midnight). Format `YYYY-MM-DD` only |
| `after:` | `after:2026-01-01` | `date >= ` that day, 00:00 in PHP's default timezone |
| `has:attachment` | `has:attachment` (or `has:attachments`) | Only messages with `has_attachments = true` |

An operator with an empty or invalid value (bad date, unknown `has:` value, unterminated quote) is treated as plain text. With no free text, results are sorted newest first; otherwise by relevance. Date and attachment filters are built from integers and constants only: no user string reaches the Meilisearch filter expression.

### Searches left to the native engine

A search is answered natively, never by the plugin, when it is empty (after removing operators with no valid value, nothing remains) or contains, outside a quoted phrase, any of:

- bare state words (case-insensitive): `unseen`, `seen`, `flagged`, `unflagged`, `answered`, `unanswered`, `deleted`, `undeleted`;
- the prefixes `is:`, `in:`, `keyword:`, `larger:`, `smaller:`;
- raw IMAP syntax of the core, i.e. one of the core's upper-case search keywords followed by a colon (`FROM:x`, `SUBJECT:y`, `DATE`, `BODY`, `EMAIL`, `TO`, `KEYWORD`, `IN`, `SMALLER`, `LARGER`, `SINCE`, `ON`, `SENTON`, `SENTSINCE`, `SENTBEFORE`, `BEFORE`, `OLDER`, `YOUNGER`, `HEADER`, `TO-ONLY`, `ATTACHMENT`, `FLAGGED`, `UNFLAGGED`, `SEEN`, `UNSEEN`, `ANSWERED`, `UNANSWERED`, `DELETED`, `UNDELETED`). Other upper-case prefixes (`RE: invoice`, `FW: quote`) are ordinary text.

The client mirrors this test to skip the extra hop; the server's `QueryParser` stays authoritative. Also native: requests inside one thread, and the GET variant of the list request.

## Fallback behaviour

If the plugin is unconfigured, or the engine is unreachable, slow, answers non-200 or garbage, and `fallback` is on, the server runs the core's regular `MessageList` with the same parameters and adds `ExternalSearchFallback: true` to the response. The client then marks the search field with the CSS class `external-search-fallback` for 5 seconds and sets a tooltip ("Fast search is unavailable, showing standard results"; strings in `langs/`, English and French provided). The default style is a dashed outline in `search.css`; override it freely. The warning is also written to the Tachyon log. With `fallback` off, the error surfaces as a normal "cannot get message list" error.

## Naming

The browser-side action is `PluginExternalSearch` (the core prefixes plugin actions); the server-side JSON hook is `ExternalSearch`. `search.js` wraps `rl.app.Remote.request`: a `MessageList` call with a non-empty, non-native search is replaced by `PluginExternalSearch` with the same parameters, and a pending external search is aborted whenever a new list is requested so a late answer cannot overwrite a newer list. Bump `VERSION` in `index.php` whenever `search.js` changes (the client bundle is cached by plugin name and version).

## Writing another driver

Implement `ExternalSearch\SearchDriver` (`lib/SearchDriver.php`):

```php
interface SearchDriver
{
    /**
     * @return array{hits: Hit[], total: int}
     * @throws \RuntimeException when the engine is unreachable, slow or answers garbage
     */
    public function search(Query $q, int $offset, int $limit): array;
}
```

`Query` carries the free `text` and the parsed `filters` (`from`, `to`, `before`, `after`, `has_attachment`); each `Hit` is `(folder, uidvalidity, uid, score)`; `total` is the engine's (possibly estimated) total. Throwing triggers the fallback. Then register the driver in `ExternalSearchPlugin::configMapping()` (the `driver` select) and `driver()` in `index.php`. `MeilisearchDriver` takes an injectable HTTP transport closure, which is how its tests run without a network.

## Documentation

In the repository, next to the plugin files (not in the release zip):

- [`docs/footprint.md`](https://github.com/remmmi/tachyon-plugins/blob/main/external-search/docs/footprint.md): disk, RAM and time, measured, and how to project them onto your mailbox
- [`docs/document-schema.md`](https://github.com/remmmi/tachyon-plugins/blob/main/external-search/docs/document-schema.md): the contract between indexer and plugin
- [`docs/hybrid.md`](https://github.com/remmmi/tachyon-plugins/blob/main/external-search/docs/hybrid.md): hybrid search, what it brings, what it costs, the pitfalls
- [`docs/history.md`](https://github.com/remmmi/tachyon-plugins/blob/main/external-search/docs/history.md): what was tried, measured and learnt, with dates

Upstream threads: kimusan/Tachyon#140 (the core entry point `MessageListByTuples`), kimusan/Tachyon#150 (the date
shown on rows of a list grouped by folder).

## Changelog

- **1.0** (2026-10-16): result page built through the core's public `MailClient::MessageListByTuples()` where it exists, the protected method otherwise (best effort); messages missing from the mail server counted (`ExternalSearchSkipped`) and logged; engine status in the admin window (optional status key); `hybrid_enabled` switch; result order switch and flat list in the plugin (`order-switch.js`, `show_order_switch`); license changed from MIT to AGPL v3.
- **0.7** (2026-10-13): order of the results (`default_order`, request parameter `externalOrder`): grouped by folder as before, or the engine's order restored, newest first or most relevant first.
- **0.6** (2026-10-13): code style aligned on the Tachyon core (tabs, `else if`, no one-line blocks, `\`-prefixed global constants), `LICENSE` constant (the admin panel showed the inherited AGPL v3), no PHP 8.1-only syntax (the core supports PHP 8.0), unused `RESULTS_GROUPED` string removed.
- **0.5** (2026-10-13): optional hybrid search (`hybrid_embedder`, `semantic_percent`, `hybrid_max_results`), keyword retry when the embedder is unavailable.
- **0.4** (2026-10-09): first complete version: all-folder search, operators, grouped results, native fallback.

## Running the tests

The tests are plain PHP scripts with fake Tachyon and MailSo classes (no Tachyon install, no engine needed). From the plugin directory, with PHP 8.2+:

```bash
for t in tests/*Test.php; do php "$t" || exit 1; done
```

## License

GNU Affero General Public License v3 or later, see [LICENSE](LICENSE).
