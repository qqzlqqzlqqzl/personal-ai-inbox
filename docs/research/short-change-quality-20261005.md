# Bounded short technical change candidate

Base: `3349cf22bb73a9b08c04e84e960048fb8e64a653`, tree
`b05f4e7f053d680bebfd397df659d84e2ddbf206`, SRC
`3f13dba5dee642a8dd1d5d3a6df87fa5a2a7ee12`. This is a local candidate for
independent review, not a production correction or final whole-reader acceptance.

## Finding and scope

The existing short-body exception accepts a security risk with action, or an
imperative with an effect. A declarative release subject can already explain a
concrete failure and the fix mechanism or trigger. Six synthetic contrast cases
were incorrectly excluded as `release_subject_without_explanation`; the same
six failed the reviewed fulltext selector's short-body guard. The retained
baseline test run had 12 failures and 13 passes before changing source.

`meaningful_short` now additionally accepts a bounded set of named technical
effects/failures, a change verb, and either a `by` mechanism or `when` trigger
containing the required technical object and specific mechanism/trigger verb.
It does not use a new length threshold, turn generic labels into explanations,
change scores, promise recommendation, or fetch explanatory linked material.
The finite vocabulary is intentionally incomplete; it is not general semantic
understanding of every short article or every language.

Only `src/content_quality.py` changes runtime behavior. Payment detection,
source bindings, recommendation scores, owner/identity CAS, all three reviewed
182 backend files, the catalog, provider and scheduling paths are unchanged.
The candidate adds its own test file and this document. Final integration must
recompute the combined SRC and its exact dev-fixture pin; this slice does not
guess that union or edit CI/thresholds.

## Evidence classes and compatibility

- New positive cases are synthetic: request smuggling with conflicting headers,
  use-after-free on request cancellation, integer precision via 64-bit ID
  parsing, NULL dereference on device disconnect, deadlock with mutex release,
  and out-of-bounds read with buffer length validation
- Generic fixes and unexplained fault names remain negative synthetic controls
- b11385, b11384 and b11378 remain three separate existing public API release
  body fixtures; each still excludes its template/subject-only material
- The two existing publisher-nonfree fixtures contain selected public metadata
  from the previous coordinator observation, not raw HTTP, private reading
  history or observed payment prompts. They support nonfree pending review,
  not a confirmed price or a new paid-history claim
- Worker/fulltext tests use synthetic HTML and mocked transport, disable model
  requests, and never demonstrate a production transition

The durable receipt schema/policy remains `reader-content-quality-v1`.
Existing source-bound `false` receipts stay excluded; computing a new assessment
does not reinterpret them. A policy bump would invalidate unrelated historical
exclusions into unknown and could expose them again, so it is deliberately not
performed. Only newly assessed inputs use the additional short-change rule.
The new regression proves a historical false receipt stays unchanged during
projection; an explicit correction still requires the existing exact source and
expected-receipt CAS. No historical scan, migration or repair was performed.

## Verification boundary

Tests cover pure quality, public metadata fixtures, hidden/free/paid gates,
fulltext admission, worker behavior with no model call, recommendation API and
identity, detail source contracts, and existing Kicktraq identity/CAS consumers.
The isolated runner blocks socket connections and retains its own test files.
The final finite run passed 208 tests and 5 subtests; these counts include the
existing controls, not an additional independent full-suite pass. All 30 Python
package versions were checked against the base dev lockfile.
Source/API assertions are not built-browser, full CI, or production validation.

The supplied Kicktraq snapshot is audited separately without another request:
15 items in each RSS, 30 distinct links/GUIDs, all third-party HTTP project links,
zero direct Kickstarter project URLs, HTTP Atom self identity despite HTTPS
capture, and 3/2 title encoding anomalies under the declared ISO-8859-1.
This does not prove native Miniflux decoding, actual stored identity, project
link liveness or continued polling. No source was activated, restricted request
retried, private body published or provider request made. Issues 105/106/107
remain open pending their separate actual-history/runtime acceptance.
