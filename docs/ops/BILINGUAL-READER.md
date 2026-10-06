# Cached bilingual Reader bodies

Reader translation is an optional background addition. It preserves `entry.content`
and never waits for a model in a detail request. `migrate()` initializes its tables
inside the existing `core.DB` SQLite database. Start/cancel `run_worker()` with the
API lifespan. After obtaining the existing detail body with
`core.decorate(entry, user_id, include_source_fallback=True)`, call
`enqueue(entry, priority=100, admission=admission)` and `attach(entry, user_id)`.
`attach` only reads an exact user/entry/body/model/prompt cache version. Do not attach
large translation HTML to list responses.

Configuration is read from the existing service environment. No credentials belong
in source, logs, fixtures or this document:

- `BILINGUAL_ENABLED`: opt in with `true` (default off).
- `BILINGUAL_API_BASE_URL`: HTTPS OpenAI-compatible API root, without credentials,
  query parameters or `/chat/completions`; the worker appends that path.
- `BILINGUAL_API_KEY`: supplied securely by the operator.
- `BILINGUAL_MODEL`: defaults to `gpt-4o-mini`.
- `BILINGUAL_DAILY_REQUESTS`: default 40 requests per UTC day.
- `BILINGUAL_DAILY_TOKENS`: default 250,000 reserved/actual tokens per UTC day.

The separate `bilingual_usage` ledger does not consume analysis/card/Kaggle budget.
Reservations include a conservative input allowance and output maximum. Failed or
interrupted requests still count. There is one model request in flight per database,
with a process lock, 75-second HTTP timeout, at most eight chunks/10,000 input
characters per request, and four attempts per missing chunk with backoff. Successful
chunks are persisted immediately and never included in another paid request for the
same cache version. Source bodies over 2 MiB are not queued.

When ready work is absent, each worker round fetches at most two existing Miniflux
entries using `worker.MF` and `MINIFLUX_API_KEY`. Candidates must have analyses in
`done` with score at least 8, ordered newest publication first. It uses the same
decorated body as Reader, including prepared/source fallback content. It does not
fetch article websites, re-score entries, or run an all-library model backfill. The
active pending queue is bounded, and completed source candidates are revisited only
after six hours or a changed analysis revision. Opening an uncached detail promotes
its work ahead of background articles.

`entry.translation` returns `status`, `language`, `model`, `source_hash`,
`blocks_total`, `blocks_done`, and `updated_at`. `bilingual_html` and `chinese_html`
are present only after at least one successful chunk. Statuses are `pending`,
`partial`, `done`, `native`, `notconfigured`, `budget_paused`, and `error`.
Only pending/partial should be polled. Missing configuration returns
`notconfigured`; a partial cached view remains readable, while a complete cached
view stays `done`. Original-only Chinese articles are `native`.

Paragraphs, headings, list text, quotations, table cells and captions are extracted
without overlapping parent/child text. Chinese and code are not sent for translation.
Immutable markers preserve source inline structure; responses with missing or changed
markers are retried. Images occur once in each output mode. All model-provided text
is HTML-escaped; only sanitized original-source formatting markers become markup.
Chinese-only partial views retain untranslated original sections instead of blanking
them. JSON responses accept top-level `items`, `blocks` or `translations`, all with
integer `id` and string `text` fields. Truncated responses are never marked complete.

Offline focused validation (stdlib unittest plus the project's existing `httpx`):

```
PYTHONPATH=src python -m unittest discover -s tests -p test_bilingual_translation.py -v
python -m py_compile src/bilingual_translation.py tests/test_bilingual_translation.py
```

These tests use temporary databases and fake-key `httpx.MockTransport` exclusively.
They do not test production credentials, model quality, deployment, or browser UI.
