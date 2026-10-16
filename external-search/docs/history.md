# History

What was tried, measured and learnt while building full-text search for one shared Tachyon mailbox, in order. The
mailbox: one IMAP account used all day by a small team, migrated from Gmail (each Gmail label became a folder),
about 26 000 messages in 176 folders when this started, 35 000 in 177 a week later, on an IMAP server without
`MULTISEARCH`. Hybrid search has its own page: [`hybrid.md`](hybrid.md).

## Before any engine (2026-10-07)

- The stock search only looked in the open folder. A first fix ran IMAP `SEARCH` in every folder: a sender address
  gave 4 results from 2 folders in 4.7 s.
- Measured cost of the native multi-folder search: about 10 ms per folder plus 5–10 ms per 1 000 messages; just
  opening the 174 folders took 1.8 s. Projected at 160 000 messages: 5 to 7 s per search. Too slow, and IMAP
  `SEARCH` matches exact words only: a typo finds nothing.

## Choosing the engine (2026-10-09)

- Constraint: keep the mail at the IMAP provider, spend only a few GB on the server for an index.
- Weighed: headers only; SQLite FTS5 (one file, no service, the first recommendation); Meilisearch (typo tolerance,
  filters, ranking, but one more service with resident RAM); embeddings (later).
- **Meilisearch** won: one binary in an official Docker image, LMDB storage (RAM follows what is read, not the index
  size), RAM that can be capped, typo tolerance, and hybrid search possible later **without a second engine**. A
  separate vector database was ruled out for that reason: one engine means one sync and one reconciliation.
- Content: headers, plain-text body (capped at 20 000 characters), attachment **names**. Attachment **text** was
  deferred: it means downloading every attachment, tens of GB read instead of a few.
- One search box: the plugin takes over the native box rather than adding a second one. The native search stays as
  the fallback (engine down, state filters such as unread or flagged).
- Limits from day one: `mem_limit: 2g`, one indexing thread, 1 GiB of indexing memory, so that the other services on
  the same server keep priority. Port bound to loopback. The webmail only gets a search-only key.

## The indexer (2026-10-09)

- Read only (`EXAMINE`, `BODY.PEEK`), never downloads attachments, reads the first `text/plain` part (else HTML).
- Incremental per folder, state `{folder: uidvalidity, last uid}`; the state moves forward only once the engine has
  accepted the batch.
- **UIDVALIDITY**: when a folder's value changes, its documents are dropped and the folder is indexed again, because
  old UIDs mean nothing any more. The plugin checks it too at query time.
- A nightly reconciliation removes UIDs that left the server (moved, deleted) and adds missing ones. It first ran at
  03:00, at the same minute as an incremental run that held the lock, so it never ran: moved to 03:02 with a wait
  on the lock.
- Folder names are decoded from IMAP's modified UTF-7 to UTF-8, the form Tachyon uses; inline images do not count as
  attachments; folders that disappear are purged.
- Measured: 5 000 documents from 91 folders = 168 MB of index in about 2 min 25 s; first scheduled run 11 185
  documents in 2 minutes; 8.9 GiB of RAM still available on the host during indexing.

## Rendering the results (2026-10-09)

- Two designs from the plan were dropped: re-running the core's list with a UID search (the core's search parser has
  no UID operator) and rewriting the core's response afterwards (the search would run twice).
- Instead, the plugin hands the core a list of `[folder, uid]` pairs and lets it fetch and render them through an
  internal method, `MailClient::MessageListMultiFolderFetch`: protected, reached through a bound closure, guarded by
  `method_exists`, with a fallback to the native search if it ever disappears.
- First end-to-end result: 23 messages in 5 folder groups in 0.52 s.

## Measured against IMAP (2026-10-13)

On 26 499 messages in 176 folders, IMAP `SEARCH TEXT` over every folder against the engine:

| Query | IMAP | Engine |
|---|---|---|
| a common word | 4.2 s, 252 messages | 5 ms, 234 messages |
| the same word with a typo | 3.7 s, 0 | 5 ms, 234 |
| another word with a typo | 3.6 s, 0 | 4 ms, 6 721 |

RAM at that size, keyword index only: 340 MB really held, plus 696 MB of page cache the kernel can reclaim; 675 MB
on disk.

## The order of the results (2026-10-13)

- The internal method returns a page grouped by folder, which threw away the engine's ranking (the best hit could be
  30th), and the core draws any multi-folder list under folder headings.
- In this mailbox folders are not a reading key (Gmail labels turned into folders, filed unevenly over the years). So:
  **newest first by default, most relevant on a click**. Newest first sends `sort: ["date:desc"]` with every word
  required (otherwise partial matches bury full ones) and never runs a hybrid query. The `sort` ranking rule had to
  move first in the index settings (it was fifth), without reindexing; otherwise the date only breaks ties.
- A flat list with each row tagged with its folder, and a two-button switch. At first this lived in a site-specific
  interface plugin; in 1.0 it moved into the plugin (`order-switch.js`).
- Side effect found: in a list grouped by anything other than days, the core shows only the time on each row
  (`timeFormat` follows the sort mode, the grouping follows the search scope; inherited from SnappyMail). Worked
  around by the interface plugin, reported upstream as kimusan/Tachyon#150 (2026-10-15).

## Upstream (2026-10-13 to 2026-10-15)

- kimusan/Tachyon#140 asked whether such a plugin is welcome and how to handle the dependency on a protected method:
  a public entry point, living with the risk, or a separate repository.
- The maintainer chose a public entry point and asked for the plugin to live in its own repository (this one). He
  wrote `MailClient::MessageListByTuples(array $aTuples, ?array &$aMissing = null)` on master (`5a3dbd9`,
  2026-10-15): keeps the caller's order across folders, reports the pairs that did not come back, at most 1 000 pairs
  per call. Tachyon 4.4.3, tagged the same day a few hours earlier, does not have it yet.

## Hybrid search (2026-10-13 and 14)

Summary only; details, costs and pitfalls in [`hybrid.md`](hybrid.md).

- Scope kept to hybrid search: turning a request into filters with a language model, or having a model read the mail
  and answer, were set aside.
- Mistral `mistral-embed` through a Meilisearch `rest` embedder; what is sent is trimmed (no quoted history, no URLs,
  1 500 characters); automated mail, repetitive templates and mail before 2024 stay keyword only.
- EUR 1.33 for 31 200 embedded texts. Copying the index to the production instance cost nothing.
- Production switched to hybrid search with the newest-first order on 2026-10-14. Same query: keyword only 21 ms,
  hybrid about 1.5 s, newest first 18 ms.
- Two indexes on purpose, one for a test instance and one for production, so that settings can be tried without
  touching production; each new mail is embedded twice, for cents a month.

## Version 1.0 (2026-10-16)

- Two ways to render a page: the public `MessageListByTuples` where the core has it, the protected method otherwise
  (best effort). Verified on a Tachyon built from master (public path) and on 4.4.3 (protected path).
- Messages missing from the server are counted (`ExternalSearchSkipped`) and logged instead of silently shortening
  the page.
- An engine status in the admin window (a read-only key), a switch for hybrid search, the order switch inside the
  plugin.
- The indexer, until then specific to one site, was made generic and shipped in `deploy/` with a Compose file and an
  installer; `AGENTS.md` guides an agent through the installation.
- License changed from MIT to AGPL v3; the plugin folder lost its site prefix (`external-search`).

## Not done, and why

- **Attachment text**: tens of GB to download, a converter to run, a much bigger index. Possible later; the names are
  indexed.
- **Mail younger than one indexer run** (5 minutes) is only found by the native search. Merging a fresh native search
  into the engine's results was discussed, not built.
- **A local embedding model**: free, but hours of CPU to throttle on a shared server; a hosted API was cheaper in
  practice.
- **One index shared by the test and production instances** to halve the embedding cost: refused, to keep room for
  tuning on the test instance.
- **A full reindex** to correct an early `has_attachments` bug on the first 5 000 documents: not done yet.
