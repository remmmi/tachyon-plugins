# Hybrid search (keywords + meaning)

How hybrid search runs on the mailbox this plugin was built for, what it costs, how to set it up, and what went
wrong on the way. There is deliberately **no code** here: the embedding side depends on your provider and your mail,
and a coding agent can write it from this page (see `AGENTS.md`, step 9). The plugin itself only needs an embedder
name; everything else lives in the index and the indexer.

## What it brings, and what it does not

An **embedding** is a list of numbers (here 1 024) that a model computes from a text so that texts with close
meanings get close lists. Meilisearch stores one per document and, for a hybrid query, mixes the keyword ranking
with a "closest meaning" ranking.

What it found here that keywords missed: a query in one language matching a thread written in another; "refund
problem" matching a payment-schedule mail and a return thread that never used the word "refund".

What it does **not** improve: names, order numbers, references, exact phrases. Keyword search already finds those,
fast and exactly. If people mostly search for those, hybrid search adds cost and latency for little. Say so plainly.

## How it runs here (October 2026)

- **Model**: Mistral `mistral-embed` (1 024 dimensions, USD 0.10 per million tokens, 8 192 tokens at most per input),
  declared on the index as a Meilisearch `rest` embedder. **Meilisearch calls the provider itself**: about 10
  documents per request when the indexer adds documents, and once per hybrid query for the typed text. No separate
  embedding service, no nightly batch: a new mail can be found by meaning about 5 minutes after it arrives.
- **Plugin settings**: *Hybrid search* ticked, embedder name `mistral`, semantic weight 50 %, results capped at 200,
  timeout 3 000 ms.
- **Volume**: about 35 000 messages indexed, 18 338 with an embedding (60 % of what was eligible), the rest keyword
  only by choice (see *What to embed*).
- **Latency, engine side, same query**: keyword only 21 ms; hybrid about 1.5 s (the query embedding is a call to the
  provider); newest-first order 18 ms (never hybrid, see below).

## What it cost

- **EUR 1.33** for 31 200 embedded texts, all included: a 500-document trial, the full pass over 26 500 messages,
  and 4 200 texts embedded again after the body was trimmed better. Average text sent: 862 characters.
- Projection for about 160 000 messages with the same filters: **under EUR 10** in total, then cents per month for
  new mail (about 26 new messages a day here).
- Copying an index with its vectors to a new index: **EUR 0** (see *Copy without paying again*).
- What makes it expensive: sending whole bodies (quoted history, long signatures, links), embedding thousands of
  identical notifications, and **changing models**, which means embedding everything again.

## Procedure

1. **Choose the provider** with the person: a hosted API (Mistral, OpenAI, Voyage, Cohere…) or a local model
   (Ollama, Hugging Face). Hosted = cheap and fast to set up, mail text leaves the server. Local = free and private,
   but hours of CPU for the first pass on a shared server, and a model to keep running. Here: hosted, Mistral.
2. **Get an API key** and keep it in a file next to the engine's master key (mode 600), never in the webmail.
3. **Add a field for the embedding text** in the indexer's documents (see *Trim what you send*), and decide
   **what to embed** (see below). Documents that should stay keyword only carry `"_vectors": {"<embedder>": null}`.
4. **Declare the embedder** on the index, from the server, with the master key:
   `PATCH /indexes/<index>/settings` with `{"embedders": {"<name>": {"source": "rest", "url": …, "apiKey": …,
   "dimensions": …, "documentTemplate": "{{doc.<your field>}}", "request": …, "response": …}}}`, following the
   Meilisearch documentation for `rest` embedders and your provider's API. Meilisearch then embeds every document
   already in the index that does not opt out: this is the expensive moment. Try it first on a throwaway index of
   a few hundred documents.
5. **Watch it**: `GET /tasks` until the embedding task succeeds; `GET /indexes/<index>/stats` shows
   `numberOfEmbeddedDocuments`. The plugin's admin window lists the embedders found on the index.
6. **Switch it on** in the plugin: tick *Hybrid search*, set the embedder name, keep the weight at 50 % to start,
   raise the timeout to about 3 000 ms.
7. **Compare** a few real searches with the box ticked and unticked before keeping it.

## Trim what you send

The biggest lesson, in money and in quality.

- Build a **dedicated field** for the embedding text and point `documentTemplate` at it alone. Keep `body` whole for
  keywords. Meilisearch 1.54 did **not** apply `documentTemplateMaxBytes` to a `rest` embedder: a 20 000-character
  body went out whole and the provider refused it (over 8 192 tokens). Cap the field yourself.
- What went into the field here: subject, sender, attachment names (never their content), then the body **without
  quoted history** (only the last reply of a thread) and **without URLs**, cut at **1 500 characters**. URLs were
  10 % of the characters and carried no meaning. One support thread had a 20 000-character body made of stacked
  quotes; its embedding text became 1 482 characters.
- Cutting quotes: about 30 lines of in-house code (cut at the first "On … wrote:" style line or `>` block) left 44
  badly cut mails out of 26 499. Libraries such as `talon` or `email-reply-parser` were rejected for their heavy
  dependencies.

## What to embed

Embedding everything costs more and **lowers quality**: 2 475 near-identical payment notifications crowded the
meaning space and kept surfacing at the end of results. The rule kept here, all three criteria together:

1. **A date cut-off** (here 2024-01-01): imported history stays keyword only.
2. **Automated mail**, recognised by headers: `List-Unsubscribe`, `List-Id`, `Auto-Submitted`, `Precedence: bulk`.
3. **Repetitive templates**: same sender address, same subject shape and same body opening (digits neutralised),
   seen 20 times or more.

Why all three: a rule on the subject alone would have excluded 37 % of the mail, including hundreds of hand-written
customer messages that share a one-word subject, and contact-form messages. Headers alone missed the payment
processor's templates (2 542 mails with no automated header). With the three criteria, on 26 499 messages: 60 %
embedded, 21 % automated, 18 % templates, 35 empty. Rare templates (under 20, such as payment refusals) stay
embedded, which is what you want: they answer "payment refused".

A template is only recognised at its 20th occurrence; the first 19 got an embedding. A small catch-up job that
re-marks them costs nothing to re-run when no rule changed.

## When the provider fails

Expired key (it happened here: a key expired three days before the first run, HTTP 401), no credit, API down:

- **Indexing must not block.** If an embedding call fails, add the batch keyword only, remember which messages are
  waiting, and on each later run try one of them; when the provider answers again, embed the rest. Tested here by
  swapping in an invalid key and back: 4 messages, 1 embedded and 3 waiting, then 4 embedded with no intervention.
- **Searching does not block either**: if the engine refuses the hybrid query, the plugin retries once as a keyword
  search and logs `external-search: hybrid search unavailable, keyword results served`.

## Copy without paying again

To rebuild or rename an index without embedding everything again: read the source with `retrieveVectors: true`,
write each document into the new index with `"_vectors": {"<name>": {"embeddings": […], "regenerate": false}}`
(or `null` for the ones that opted out), the new index having the same embedder declared. As a safety net, declare
that embedder with an **invalid** API key during the copy, so a mistake cannot trigger paid calls; set the real key
afterwards (setting the key does not re-embed). Done here: 29 377 documents, 18 102 vectors, EUR 0. Then swap the
indexes (`POST /swap-indexes`) and keep the old one until the new one is validated.

## Engine behaviours worth knowing

- A semantic ranking ranks **every** document, so the engine reports the whole index as the total. The plugin caps
  it (`hybrid_max_results`, 200). An exact name with 28 messages shows its 28 hits first, then neighbours by meaning.
- When keyword results already fill the page with high scores, Meilisearch **skips** the semantic part. Meaning shows
  up mostly when keywords are weak or scarce.
- **Newest-first order is never hybrid**: sorting a ranking by meaning by date makes no sense. That order is keyword
  only, every word required.
- The first hybrid query after a quiet period can take 2–3 s; with the plugin's default 800 ms timeout it comes back
  keyword only. Use about 3 000 ms.
- Scores with `mistral-embed` (a late-2023 model) are tight, 0.78 to 0.90. No reranker is used.
- A copy of an index with its own indexer (here a test instance) embeds every new mail twice: cents per month here,
  but know it.
