# Document schema

The contract between whatever fills the index (the indexer in `deploy/indexer/`, or your own) and the plugin
`external-search`, which only reads. One document per message, primary key `id`.

| Field | Type | Required by the plugin | Notes |
|---|---|---|---|
| `id` | string | yes (primary key) | `sha1("<folder>|<uidvalidity>|<uid>")` in hex. Any unique string works for the plugin; the indexer uses this one so that a document can be found again from IMAP alone |
| `folder` | string | yes | IMAP folder name **in UTF-8**, as Tachyon shows it (`Bestätigungen`), not the modified UTF-7 that IMAP `LIST` returns (`Best&AOQ-tigungen`): the plugin hands it to the core, which encodes it itself |
| `uidvalidity` | integer | yes | UIDVALIDITY of the folder when the message was indexed |
| `uid` | integer | yes | IMAP UID |
| `date` | integer | yes | Unix epoch seconds. Used by `before:`, `after:`, the date order and the engine status. The indexer takes the server's arrival date (`INTERNALDATE`), so it can differ by a few minutes from the `Date:` header a client displays |
| `subject`, `from`, `to`, `cc` | string | searched | decoded header text (`from` also filterable, for `from:`) |
| `body` | string | searched | plain text of the body; HTML converted to text; capped (`BODY_MAX_CHARS`, 20 000 by default) |
| `attachments` | array of strings | searched | attachment file names |
| `has_attachments` | boolean | filtered | true for real attachments, false for inline images such as logos (`has:attachment`) |

## Index settings the plugin relies on

- `searchableAttributes`: `subject`, `from`, `attachments`, `body`, `to`, `cc` (in that order of weight);
- `filterableAttributes`: `folder`, `date`, `from`, `has_attachments`;
- `sortableAttributes`: `date`;
- `displayedAttributes`: at least `id`, `folder`, `uidvalidity`, `uid`, `date`;
- `rankingRules` with `sort` **first**, or "Newest first" only breaks ties between equally relevant results;
- `pagination.maxTotalHits`: 5 000 is enough for a webmail list.

`deploy/indexer/setup_index.py` applies exactly these; `setup_index.py --check` tells whether an existing index
matches.

## Why `uidvalidity`

IMAP UIDs are only unique within a folder *and* a UIDVALIDITY value: when a folder is recreated or renumbered, the
server changes its UIDVALIDITY and old UIDs point to other messages, or nothing. At query time the plugin asks the
server for the current UIDVALIDITY of each folder on the page (one `STATUS` per folder) and drops hits that no
longer match. The indexer, on its side, reindexes a folder whose UIDVALIDITY changed.

## Keeping it in sync

A message moved or deleted after indexing is still in the index until the next reconciliation (the indexer runs one
every night). Meanwhile the plugin skips it: the page is one row short, the plugin logs the pair and counts it in
`ExternalSearchSkipped`. A moved message is found again under its new folder by the next incremental run.
