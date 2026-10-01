# Notes metadata candidate (Refs #73)

This is an opt-in paired Reader/Miniflux release candidate, not a production rollout.
The deployed configuration and ordinary official Miniflux binary are unchanged.

## Release gate and rollback

`READER_NOTES_METADATA` is an explicit process-environment switch: absent or `0`
retains the existing notes path; exactly `1` enables the metadata adapter. Other
values fail visibly. There is no capability auto-detection or error fallback that
fetches every body. Do not enable it against stock Miniflux. Install and verify the
pinned optional Miniflux patch in an isolated environment first; an authenticated
POST `/v1/entries/metadata` with `{"entry_ids":[]}` must return HTTP 200,
`X-Reader-Entry-Metadata: 1` and `{"entries":[]}`. A version string is insufficient.

Deployment requires its separate release review and authorization. This PR does
not modify systemd, proxy, credentials, grants, schema or running services. Before
adoption, verify real candidate cardinality, proxy request-body limit, and retain
exact prior Reader and Miniflux release artifacts. Roll back Reader first (prior
release or switch to `0`, then verify notes); only then restore the previous
Miniflux binary/image. Preserve notes, databases and volumes. No DB rollback is
introduced. The old path still has its known all-body cost; rollback is explicit.

## Selection and errors

- Freeze the authenticated user's nonempty SQLite notes independently of analyses
- Retain authenticated read/unread/starred ID enumeration and live feed visibility
- Intersect note IDs with readable IDs; one candidate-only metadata request, maximum
  10,000 positive int64 IDs and 256 KiB. Above the bound returns an explicit error;
  no truncation, batching, zero-total success or full-body fallback
- Strictly validate capability, exact five-field metadata DTO, user ownership,
  requested IDs, duplicates and types. Metadata missing an inaccessible ID does
  not delete its local note. Malformed or unavailable provider data fails 503
- Preserve Python `[:200].casefold()` literal title-OR-note matching, inclusive
  integer date aliases, invalid/absent article timestamp fallback, hidden/scope
  rules, both tuple sort directions, ties, exact selection total and clamped page
- Hydrate only the selected page, with at most eight HTTP requests concurrently.
  Selected body 5xx/transport/JSON/auth failures remain explicit 503. Off-scope and
  off-page body endpoints are never contacted by the enabled path
- A selected disappearance or title/feed/time/status contradiction triggers one
  fresh IDs/feeds/metadata selection, then refills the correctly recomputed page.
  Repeated churn fails 503 with Retry-After: 1. At most 2 * clamped limit body calls
  occur; no shortened successful page with stale total is manufactured
- All SQLite, metadata decoding, selection/sort and decoration helpers run through
  the existing bounded ReaderWorkPool. Existing cached-card zero-DML is preserved;
  cold cards may still legitimately enqueue their selected-page translations

A stable 200-notes/24-matches request uses 2 ID lists + 1 feed list + 1 metadata
POST + 24 bodies = 28 HTTP calls, excluding login and optional worker identity
verification. Previously it used 203. Metadata remains O(readable noted entries),
and ID enumeration remains O(all readable entries), fully paginated. This is not
an all-work-becomes-page-sized claim.

The total is a selection-snapshot total. SQLite notes, ID visibility, feeds,
PostgreSQL metadata and later bodies are separate snapshots, not one distributed
transaction. Only the metadata query itself is one PostgreSQL statement snapshot.

## Optional upstream patch maintenance

See `ops/miniflux-metadata/` for the exact official-source pin, small patch, build,
compatibility checks and disposable PostgreSQL/auth tests. The projection adds no
body, enclosure, cookie or note to its DTO and needs no new DB access. Existing
API auth normally updates last-login/API-key-use timestamps; only the new query
is SELECT-only. Do not describe the whole authenticated request as zero Miniflux
DML. Preserve upstream Apache-2.0 licensing/notices in built artifacts.

Every upstream upgrade requires reviewing/rebasing this patch and passing the
same auth/ownership/time/query regression suite. Compatibility fails closed on
source/pin drift. Do not disable or indefinitely defer security updates to retain
this patch: use the paired rollback path if maintenance cannot be completed.

## Verification

`tests/test_notes_metadata.py` exercises actual adapter requests with isolated
SQLite and synthetic Miniflux responses. It includes more than 274 differential
selections against the unchanged legacy path, 200/24 isolation, 10,000/10,001
admission, legacy notes without analyses, title changes without changed_at,
foreign/malformed responses, provider failures, race repair, bounded concurrency,
thread offloading, and warm zero-DML reads. These mocks are not PostgreSQL proof.

The unchanged Reader workflow retains all Python/JS/build/browser checks. The
separate Miniflux workflow compiles the real pinned source and uses disposable
PostgreSQL plus authenticated synthetic fixtures. Only passed exact-head CI is
release evidence. Issue #73 remains open until deployment acceptance is reviewed.
