# Reader #73: cached-card read isolation

## Scope

This is the first, independently testable part of Issue #73, based on release
`codex/kaggle-qwen36-batches` at `b1de29d3f36302ddaf69253b0d53fe8925ec7daa`.
It does not resolve or close the complete issue.

- Compute the real source fingerprint after applicable prepared-content selection.
- Return before any writer transaction/DML for an unchanged current card with
  sufficient priority. This includes done/native cards with translation paused.
- Promote only genuinely lower priorities. Archive a completed old source when
  replacing it, and restore matching historical translations including same-hash
  pending/error rows. Recheck under the writer lock only when a change is needed.
- Move reader SQLite selection, card enqueue/decorate, prepared HTML processing,
  cover extraction and detail fallback into complete synchronous worker calls.
  Each connection opens, is consumed and closes in that worker. HTTP remains async.
- Batch the page's enqueue work. Limit actual synchronous workers to eight and
  also bound event-loop admission. Cancelling an HTTP waiter does not stop a
  running thread or release its admission slot. Release happens at actual worker
  completion; late errors are retrieved, and cancellation still propagates.

No scoring, date, model, budget, Kaggle, source-integration, importer, workflow,
production database or service changes are included.

## Reproduction and regression evidence

Isolated temporary SQLite, synthetic Miniflux responses, real `source_card` HTML
parsing, 24 unchanged done cards, priority 30 and translation disabled:

| Version | Returned cards | SQLite connections | INSERT/UPDATE/DELETE/REPLACE |
| --- | ---: | ---: | ---: |
| Release baseline | 24 | 170 | 48 |
| This change | 24 | 123 | 0 |

This is a deterministic fixture comparison, not a production latency measurement.
Connection reduction is separate from the stronger SQL-trace/lock assertions.

The twelve warm done/native × enabled/disabled × priority 0/30/40 regressions all
fail on the original implementation and pass after the change. The new tests also
cover unchanged timestamps/version rows, promotion followed by a write-free read,
first English/native enqueue while disabled, real excerpt/model/prepared-content
changes, missing archives, A→B→A restoration, same-hash pending/error restoration,
native cards with redundant archives, guards, ownership, backoff and simultaneous
requests for the same changed source.

`tests/test_reader_work.py` uses an externally held `BEGIN IMMEDIATE`, threading
and asyncio Events, and event-order assertions:

- Cached recommended/pending/note/native pages and article details complete while
  the writer remains held, with no DML or writer transaction.
- A genuine new-card write waits in its worker while a heartbeat and independent
  `/readyz` request complete before writer release.
- Cancelling that request keeps its real worker admitted until completion and
  connection closure; cancellation is explicitly not treated as a rollback.
- Twenty further cancelled waiters cannot exceed a configured two-worker budget
  or queue extra executor submissions. Late worker exceptions are consumed.
- SQLite open/use/close and HTML/cover parsing run off the loop; HTTP runs on it.

Ten-second waits are deadlock watchdogs only. Assertions use event ordering and
SQL, not a fragile millisecond performance cutoff.

## Verification on 2026-10-01

- Full isolated Python: 602 passed, 74 subtests passed; two existing dependency
  deprecation warnings. Includes all tests and `src/kaggle_batch` excluding its
  checked-in `test-runs` fixtures, as in the existing workflow.
- All ten `tests/test_*.mjs` programs passed, including real DOM/component lifecycle
  tests; `source_history_component_acceptance.mjs` separately passed.
- Full pinned ReactFlux overlay/repeat test and isolated `/inbox/` build passed.
  Upstream commit: `534eeb97723ac11025de4ec1ac56335072e3be52`; local pnpm 11.21.0,
  local Node 24.19.0. Hosted CI uses its existing pinned Node 24.21.0.
- Ruff passed for the new worker module and both new regression files.
- Local real Chromium was attempted, but the environment denied its process
  singleton socket (`Operation not permitted`). The official headless-shell
  download also returned truncated ZIP content. No security flags or workflow
  were changed to bypass this. Desktop/390px and the console/reading browser
  suites must be established by the unchanged GitHub CI for the exact PR head;
  consult the PR check results rather than treating this local limitation as a pass.

No production deployment or production acceptance is claimed by this document.

## Remaining Issue #73 work

Notes still fetch all readable noted article bodies before scope selection and
pagination. The unrelated/out-of-page article failure and unbounded body work are
not fixed here. A separate design must define note metadata population, legacy
backfill and freshness/invalidation while preserving unanalyzed notes, user
access, filters, exact totals and ordering. This change only offloads that path's
existing SQLite read and selected-page enrichment; it does not change note
selection or promise page-bounded upstream body fetches.
