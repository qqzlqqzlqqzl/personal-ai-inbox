# Source catalog provenance and bounded Kicktraq RSS subscription

Refs #107. This slice requires the complete independently reviewed backend
identity/CAS restriction chain at 18270894081bf826c06f3969a53bdd3da139aa3b,
whose three runtime files are byte-identical in the current 3349cf22 baseline.
This restores the independently reviewed consumer 6f155731 without treating its
old 057 dependency as sufficient approval. A pure policy version alone is not runtime deployment
attestation. This candidate does not activate a production subscription or close
the issue, and does not add a project HTML fetcher.

## Existing archive and cached status

The existing Kickstarter adapter publishes selected third-party newsletter
archives. Its item links are archive pages, not individual project URLs. The
catalog now displays its existing note and that coverage limit. Its authored
category becomes 产品与众筹; an existing subscription with another category is
shown separately as 当前订阅分类. No existing Miniflux category is changed here.

The catalog calls `vendor_sources.status(read_only=True)`. This path never creates
the state directory, refreshes, migrates or contacts a publisher. Only an exact
configured loopback feed URL receives vendor snapshot metadata. An unreadable
cache, unseeded source, missing/future/invalid success time, recent failure and
stale last-good snapshot are distinct. A successful cached RSS HTTP response does
not erase an upstream failure. The projection exposes fixed error reasons and
numeric metadata, not arbitrary upstream exception text. Freshness uses the
existing six-hour adapter interval and means only time since the last successful
snapshot, not complete coverage or a newly published article.

## New published input

Kicktraq's own https://www.kicktraq.com/rss/ directory explicitly publishes
category RSS for news readers. It identifies itself as independent of Kickstarter.
The https://www.kicktraq.com/policies/ page retains intellectual-property rights,
provides service as-is and allows withdrawal of access. This does not authorize
an expanded project-page crawler or unrestricted redistribution.

The two independently supplied source captures are:

- https://www.kicktraq.com/categories/technology/latest.rss, 18193 bytes,
  SHA256 23723010b5f64d9edefc6c033637565f3bdc2db9adf1cca7e4bbf1bfc856a420
- https://www.kicktraq.com/categories/design/latest.rss, 17617 bytes,
  SHA256 ccc8e6d1bb0c6e439bac402dc2ada3e4b783507edfde63f7ed7dd5708395da12

Both were HTTP 200 at the publisher Date 2026-10-04 22:00:43 GMT. Each contains
15 RSS 2.0 items with unique links, descending dates and GUID equal to link; the
two snapshots have 30 distinct GUIDs together. Their project links point to HTTP
Kicktraq project pages. Neither contains a direct Kickstarter project URL. No
Kickstarter URL is manufactured from a slug. Technology's latest item is
2026-10-03 14:34:51Z; Design's is 2026-10-04 16:25:45Z. This is a recent category
window, not complete discovery or evidence of continued successful polling.

HTTP and XML both declare ISO-8859-1, while the raw bytes are also valid UTF-8.
Parsing as declared reveals C1/mojibake characters in 3 Technology and 2 Design
titles. The source quality limitation is displayed; titles are not guessed or
silently repaired. Atom self uses HTTP despite the HTTPS capture URL. These HTTP
identities are observed but unapproved aliases. The actual persisted Miniflux
feed identity and native encoding result must be checked before activation.

## Subscription and analysis are separate

`subscription_supported` is an explicit catalog capability. Missing/unknown
capability is not an approval; the UI requires an own boolean true. The original
paper restriction remains. An explicitly typed ordinary manual URL retains its
existing confirmation flow, but a known catalog URL reuses that catalog's
capability, and unverified Kicktraq aliases cannot use the manual form to bypass
the gate.

Only the two exact HTTPS feed strings can have RSS subscription support, and
only when the local `feed_consumption` policy version, immutable URL set and
positive enum results match the reviewed contract. Missing/unknown policy keeps
them closed. Analysis remains unsupported. The backend subscription endpoint
also enforces this gate, rejects extra fields and identity changes, and forces
Miniflux `crawler=false` even if the caller explicitly sends true. Other source
behavior is unchanged. This does not claim to control an operator's later direct
Miniflux configuration changes or its transport redirects; production readback
must verify the effective feed identity and crawler flag.

The new fixed processing reasons `rss_summary_only` and
`rss_feed_identity_unverified`, together with their corresponding states, select
bounded badge text. RSS summaries are not called papers; actual legacy paper and
unknown-state behavior is retained. Other processing reasons, including
submission uncertainty, remain visible. Source classification does not certify
that historical cached entry HTML is the original RSS text and does not rewrite
its provenance.

## Validation and remaining acceptance

Python tests run real catalog/load/subscription functions with isolated files and
synthetic Miniflux transport. React tests exercise the actual metadata and badge
components, explicit subscription capability, manual alias rejection, note text
escaping and unknown states. Old cache-200, classification, hidden note, RSS
selection, crawler and badge counterexamples are retained separately. Installer
controls retain exact former panel/utility hashes and reject unfamiliar inputs.

`tests/source_catalog_browser.py` is a future real built-reader check for four
viewport/theme contexts. It requires the separately reviewed explicit sandboxed
Harness before construction, uses complete synthetic feed DTOs, checks the source
labels and confirmation without submitting, and rejects the HTTP identity. It
must run on the exact combined build; local syntax/component checks are not real
browser results. The restored shared Harness catalog fixtures have explicit capability fields
and preserve the current navigation/feed-shape assertions. The separately
reviewed a3db407 sandbox opt-in is composed additively; actual pinned sandbox
launch remains a distinct mandatory check.

Release requires independent combined source/CODE review and full CI with the
182 identity/CAS backend chain. Main must verify effective Miniflux feed URLs, `crawler=false`,
category, native encoding, visible project summary/link/date, last-good failure
behavior and subsequent source observations. Existing unobserved cards without
a persisted policy marker are outside the backend's historical guarantee.
No global zero-model guarantee, full project text, direct Kickstarter link,
complete enumeration or continuous freshness is claimed here.

## Restoration on the current delivery baseline (2026-10-05)

This finite candidate starts from 3349cf22bb73a9b08c04e84e960048fb8e64a653,
tree b05f4e7f053d680bebfd397df659d84e2ddbf206. The original consumer
6f155731846818055ecdf2a69ccb3b362a9a5358 still exists with its exact 18-path
delta and source-slice-only independent review. Compared with that consumer's
7554 base, nine affected current files were unchanged and eight additions were
absent; only previous-hashes.json contained later unrelated additions. That file
is combined additively without dropping any existing guard. The original
consumer implementation is restored rather than rewritten. Its overlay tests
now use 3349, whose old panel bytes are identical, as their available fixed base.

The explicit sandbox support comes from reviewed a3db4072bb9c55f858e0cf63a85cea20ab108fe7.
Its helper, tests and documentation are reused byte-for-byte, and its Harness
changes are applied to the current Harness while preserving later article
navigation and feed-icon fixes. Four safety helper dependencies have identical
ASTs to a3. Existing default suites keep their old launch behavior; the new source
catalog suite requires the explicit sandboxed=True branch and has no fallback.
The shared catalog producer now has own boolean subscription capability fields
matching the actual pure API projection, including false for blocked sources.

Required browser identity inventory and evidence-preparation paths include the
new suite. The workflow itself is owned by a separate CI change and must add the
unconditional source_catalog_browser.py step with AI_NEWS_TEST_BUILD pointing to
runtime/browser-build, the existing 600-second timeout, and runtime/source-catalog/
artifact upload. A dedicated test intentionally rejects the unmodified workflow;
this source candidate alone is therefore not a full-CI-ready release.

The final combined Harness SHA and SRC must be independently rebound in
dev_fixture_workspace_browser_acceptance.py. No pin is weakened, broadened or
guessed in this slice. Mandatory runtime, four-context browser, source continuity
and actual Miniflux identity/crawler/encoding acceptance are still separate. This
restoration does not incorporate the concurrent short-quality or reader-latency
candidates and does not authorize production subscriptions.
