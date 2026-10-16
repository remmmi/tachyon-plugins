# Footprint

What the search engine and the indexer cost on the server, as measured on one production mailbox, and how to
project it onto another one. `AGENTS.md` compares a target server against these numbers before installing anything.

## The measured setup

- One shared IMAP mailbox, about **35 000 messages in 177 folders** (October 2026), growing every day.
- Meilisearch `v1.54.3` in Docker, `mem_limit: 2g`, `MEILI_MAX_INDEXING_MEMORY=1GiB`, `MEILI_MAX_INDEXING_THREADS=1`.
- Server: 4 vCPU, 16 GB RAM, shared with a web shop and other services. Indexing never disturbed them.
- Body text capped at 20 000 characters per message; attachment *names* indexed, attachment *content* not.

## Numbers

| What | Measured | Date |
|---|---|---|
| Index size, keyword only | 5 000 documents = 168 MB (`indexSize`) | 2026-10-09 |
| Raw documents, keyword only | 29 377 documents = 96 MB (`rawDocumentDbSize`), 3.2 KB per document on average | 2026-10-16 |
| Raw documents, with vectors on half of them | 35 422 documents = 162 MB, 4.5 KB per document on average, 18 338 embeddings | 2026-10-16 |
| Database on disk, 3 indexes (35 422 + 35 421 + 29 377 documents, 36 675 embeddings) | 3.0 GB file, 1.7 GB used | 2026-10-16 |
| First fill (incremental run from empty) | 11 185 documents in 2 minutes | 2026-10-09 |
| First fill with this kit, from scratch, whole mailbox | 35 304 documents in 18 minutes (10 per second on the first batch while the IMAP server was cold, then 35 to 40), 0 folder failed, 929 MB on disk | 2026-10-16 |
| Engine RAM during that fill, container capped at 1 GB | at the cap (page cache included), not killed; search answered in 5 ms right after | 2026-10-16 |
| Incremental run, nothing new | 4 to 5 seconds, every 5 minutes | 2026-10-16 |
| Meilisearch RAM at rest | about 100 MB (container capped at 2 GB) | 2026-10-16 |
| Meilisearch image | 106 MB (`getmeili/meilisearch:v1.54.3`, as stored by Docker) | 2026-10-16 |
| Free RAM on the host during the first fill | 8.9 GiB available, nothing swapped | 2026-10-09 |
| Search answered by the plugin | a page of 23 results in 5 folder groups in 0.52 s, end to end in a browser | 2026-10-09 |

The indexer itself is a Python process from the standard library: a few tens of MB while it runs, nothing between
runs (in Docker, a sleeping `python:3-alpine` container).

## Projecting onto another mailbox

Use the keyword-only rate as the base, then add margins:

- **Index on disk**: about **35 KB per message** in practice (168 MB / 5 000 is a worst case from the first
  sample; the 3-index database above gives 1.7 GB used for 100 000 documents ≈ 17 KB each, 3.0 GB allocated ≈ 30 KB;
  the kit's fresh index: 929 MB for 35 304 messages ≈ 26 KB each).
  Projection for N messages: `N × 35 KB`, then keep **3×** that free on the disk (Meilisearch allocates ahead and
  needs room to rebuild). 100 000 messages → 3.5 GB projected → 10.5 GB free wanted.
- **RAM**: 2 GB for the engine container is comfortable up to at least 100 000 documents with
  `MEILI_MAX_INDEXING_MEMORY=1GiB`; a 1 GB container still filled 35 000 messages without trouble. Want **2×** the container limit free on the host (4 GB), or lower the limits and
  accept a slower first fill.
- **Time of the first fill**: 30 to 40 messages per second measured with this kit (35 000 messages in 18 minutes);
  the IMAP server is the bottleneck, slower on old mail it has not read for a while (`BATCH_PAUSE` slows the indexer
  down on purpose to be polite). 160 000 messages ≈ 1 to 1.5 hours. The engine itself indexes 1 000 documents in
  about 2 seconds.
- **Vectors (hybrid search)**: an embedding of 1 024 floats adds about 4 KB per vectorised document in the
  database, plus the provider's cost per token. See `hybrid.md`.

## When to worry

`AGENTS.md` flags the server as constrained, and offers `deploy/load_probe.sh` before installing, when any of these
holds:

- available RAM < 2 × the engine container limit;
- free disk on the volume of the engine data < 3 × the projected index;
- the 15-minute load average already above the number of CPUs most of the time.

The user may go ahead anyway; the agent notes it in one line.
