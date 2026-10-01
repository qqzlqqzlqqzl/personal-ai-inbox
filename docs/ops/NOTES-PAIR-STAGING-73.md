# Issue 73 paired-process staging evidence

Refs #73. The original Reader notes tests mock upstream HTTP and the Go integration
tests directly exercise Miniflux handlers with real PostgreSQL. Both are necessary,
but neither alone proves a real Reader process can consume the candidate binary.

`tests/notes_pair_acceptance.py` fills only that gap. It runs the unchanged runtime
`src/` from reviewed merge `46fa51a36a57b2856fe2e7aa54e7d12c823212a6`, verifies the
candidate binary SHA256, and starts actual Uvicorn and Miniflux processes against
the existing digest-pinned disposable PostgreSQL workflow service. Synthetic
SQLite notes deliberately have no analyses rows. No test replaces Reader functions
or mocks successful metadata responses.

The loopback observing proxy relays successful traffic to actual Miniflux. It logs
only synthetic IDs, request paths and statuses. Explicit failure scenarios strip
a capability header, rewrite a route to a real nonexistent route, or inject
403/500 responses. One selected-row race deletes a synthetic PG entry before its
real body request, proving 404 refresh/refill with an updated total.

The script refuses non-hosted runs, an unrecognized service container image,
unexpected binary/runtime source, or an already populated public schema. Official
Miniflux startup migrations and fixed synthetic accounts are used solely inside
this disposable service. No production schema, grant, credential, service or flag
is changed. Reader AI and translation are explicitly disabled in its temporary
SQLite fixture, and no model/worker credential is passed to the child processes.
Miniflux scheduling is disabled so synthetic feeds are never fetched.

## Required outcomes

- Absent flag and explicit `0`: original legacy path, 200 candidate body requests
- Explicit `1`: one real metadata request and 24 selected bodies for 200 candidates,
  with all off-page body endpoints configured to fail if unexpectedly requested
- HTTP request totals include the outer `/me` authorization: 204 legacy and 29
  enabled, equivalent to the inner-path 203/28 counts in the selection unit tests
- Authenticated direct empty capability probe retains `X-Reader-Entry-Metadata: 1`
- Generic Reader `/mf` forwarding intentionally strips that header while preserving
  the exact five-field DTO and `Cache-Control: no-store`; it is not a valid capability
  probe location. The internal notes adapter consumes the direct upstream header
- Real proxy body admission: 256 KiB accepted, +1 byte rejected by Miniflux
- Per-user metadata/notes, foreign-body 404, Reader admin 403, invalid-auth 401
- Missing capability/route/provider failure has no body fallback or fake empty total
- Selected body 403/500 remains explicit failure, not evidence of deletion
- Real selected 404 causes one bounded reselect; original notes are preserved

Results and fixture-only process logs are retained in `paired/` inside the existing
Miniflux workflow artifact. Full original Reader/Go/PostgreSQL regressions remain
required. This is synthetic HTTP loopback staging, not production Nginx/TLS,
actual candidate-cardinality, configuration-file/service precedence, or production
performance acceptance. Those remain operator gates before any paired enablement.

No deployment is performed. Production remains on its independently authorized
release target. Enable Miniflux capability first, Reader second; roll back Reader
first, Miniflux second. Do not use this test script against a live database.
