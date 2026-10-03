# Read-only URL continuation manifests (v1)

Refs #77. This CLI is independent of `export_dot_articles_readonly.py`, whose
prepared-body schema and three-item limit are unchanged. The URL importer still
defaults to three returned results and permits an explicit maximum of twelve.

## Operator procedure

Use a reviewed checkout and an existing bridge configuration. Production export
is a separately authorized read-only operation; running this CLI does not deploy,
import, fetch article bodies, call a model/GPU, or turn workers on or off.

1. Verify the configuration explicitly identifies the intended `scope_user_id`,
   `database`, `source`, `state_root`, complete `peer_state_roots` (including every
   configured lane), and `coordination_root`. Missing lane databases, incomplete
   active-batch state, a missing prepare-lease table or a missing lock fail closed.
   The operator must supply the complete lane catalog; the exporter cannot discover
   an undeclared lane. Do not create substitute empty stores to bypass a failure.
2. The existing analysis and translation worker switches must already be exactly
   `false`. Changing either switch is outside this command. An existing shared
   `bridge.lock` is held during feed verification and export. A busy lock fails
   without retrying, changing, or creating it.
3. Put previously handled **and intentionally skipped** entry IDs in a private
   UTF-8 JSON array, e.g. `[101,102]`. Use `[]` for a first batch. IDs must be unique
   positive JSON integers within SQLite's signed 64-bit range; strings, booleans,
   fractions and duplicate IDs are rejected. Personal continuation IDs are not
   committed to the repository. Do not mechanically exclude an unfinished article
   unless that is the intended continuation policy.
4. Run from the reviewed repository, using the existing runtime Python:

   ```sh
   umask 077
   python src/export_dot_urls_readonly.py \
     --config /private/operator/bridge.json \
     --scope-user-id 1 --limit 12 \
     --exclude-entry-ids-file /private/operator/excluded-entry-ids.json \
     > /private/operator/url-manifest.json
   sha256sum /private/operator/url-manifest.json
   ```

   The paths above are placeholders, not production configuration. The shell
   creates the requested output file; the exporter itself only writes JSON to
   stdout. Error output contains a sanitized code and exits nonzero. Check the
   exit status before consuming the file. Do not use a database path as stdout.
5. Check `schema`, `exporter_version`, `scope_user_id`, `entry_ids`,
   `enabled_feed_ids`, `selection`, `excluded_entry_count`, and the hashes below.
   Selection is newest first: `published_at DESC, entry_id DESC` using SQLite
   ordering (NULL published timestamps last). Explicit exclusions are applied
   before the limit. The CLI never fills a short batch using ineligible articles.
   `selection.status` is `full`, `short` or `empty`; reason counts cover every
   scoped row exactly once, including otherwise eligible rows beyond the limit.
   An empty manifest is a valid export report and is **not importable**.
6. URL analysis must use the returned exact URL and frozen prompt/policy. Record
   actual full reading, reference-only provenance and reviewed short evidence in
   the existing `dot-url-results-v1` contract. No fulltext archive is asserted.
   Bind each result to its `manifest_snapshot_hash`, and bind the result packet to
   `manifest_hash`. A read or analysis failure must not be fabricated as success.
7. After independent review of the results, use the existing import dry-run:

   ```sh
   python src/dot_url_article_import.py \
     --config /private/operator/bridge.json \
     --manifest /private/operator/url-manifest.json \
     --results /private/operator/url-results.json --batch-limit 12
   ```

   Expect `validated_no_write`. Do **not** add `--apply` without separately
   authorized import. Import independently rechecks current upstream identity,
   feed ownership/enabled status, source versions, prompts/policy, claims/leases,
   and analysis/card CAS snapshots. A failed preflight means re-export/review;
   do not edit a hash or reset the queue to force acceptance.

## Snapshot and eligibility contract

The primary database is opened with SQLite `mode=ro` and `query_only=ON`. One
explicit read transaction freezes settings, analysis, cards, translation versions
and prepare leases. SQLite reads the live WAL consistently; never copy only the
main file of an active WAL database. Connections are closed without DML. Only
`source_text IS NULL` is queried; stored body text, results, errors and notes are
not loaded into the manifest. Lane databases are individually read-only, under
the existing coordination lock, and scanned again within the main snapshot.
This does not claim a distributed transaction or prevent an uncoordinated writer;
the importer's fresh preflight remains mandatory.

Live feed scope comes from the existing read-only Miniflux `GET /v1/feeds` helper,
requiring exact ownership and `disabled=false`. API failures or malformed catalog
metadata fail closed. Only the scoped user's rows are considered. Selection uses
the importer's shared eligibility predicate: accepted queue state, integer attempts
0–2, finite retry and update timestamps no later than cutoff, all frozen body/source
fields NULL, and `truncated=0`. A non-NULL source text, even an empty string, is
ineligible. All unfinished lane states, including uncertain submissions, block their
claimed IDs; `imported`, `retired`, and `resolved` claims are terminal. An active
batch without claim rows must have a complete readable legacy manifest. Orphan
claims, malformed IDs and missing state fail closed. Leases block while
`expires > cutoff`; exact expiry at cutoff is not live.

Both exporters and both importer preflights use the dependency-neutral
`dot_import_coordination.py` validator. Every lane must have the production
batch-ledger columns and pass SQLite integrity checking. Every batch, including
finished batches and batches with transactional claims, must carry a lowercase
64-character hexadecimal ledger manifest hash.

A fallback manifest must identify the exact ledger batch and bind its complete
canonical contents to both its own `manifest_hash` and the ledger hash. The
canonical hash excludes only `batch_id` and `manifest_hash` and uses the digest
specified below. Items require unique nonempty string IDs and nonempty lists of
source-reference objects containing positive integer entry IDs. Correctly signed
historical manifests remain valid when the claims table is absent. Existing
transactional claims remain authoritative if a parked manifest is missing or
corrupt; finished batches do not need their old manifest.

Importer checks run before backup and again inside the write transaction after
the fresh upstream read. Historical receipt replay preserves prior receipt
counts and does not reconcile current rows. These checks do not establish full
Controller topology, lifecycle or recovery parity.

Cards are optional: only the matching user's eligible, due, attempt-bounded card
with a valid update timestamp and no preserved translation version is included.
An absent/ineligible card is `null`, with an aggregate omission reason; the analysis
may still be exported and the importer will leave that card untouched.

The importer's public-URL validator is reused, with additional rejection of
ambiguous numeric hosts, malformed DNS labels, backslashes and encoded sensitive
query keys. This is lexical URL validation, **not DNS/redirect attestation**. The
exporter does not resolve hosts or visit article URLs. The later reader must
independently enforce public destinations and handle redirects/login/paywalls.

## Hash specification: `python-json-sha256-v1`

The existing URL importer had no `hash_spec` field validation or versioned
exporter. This specification describes its **actual** digest implementation,
without assigning meaning to earlier temporary manifests:

```python
H(value) = sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                            separators=(',', ':')).encode('utf-8')).hexdigest()
T(text) = sha256(text.encode('utf-8')).hexdigest()
```

This is Python JSON serialization, not RFC 8785/JCS. Preserve numeric types, JSON
nulls, Unicode and raw prompt whitespace/newlines. Nonfinite numeric inputs are
rejected in the relevant projections. Never substitute `""`, `0` or omitted fields
for nulls. Hash strings are lowercase hexadecimal.

- `analysis_prompt_sha256` / `translation_prompt_sha256`: `T` of the unmodified
  effective prompt string; there is no trimming or Unicode normalization
- `excluded_entry_ids_hash`: `H` of the sorted validated exclusion array (input
  file whitespace/order do not matter); count includes valid IDs absent from scope
- `policy_snapshot_hash`: `H` of the eight top-level fields from `analysis_prompt`
  through `translation_fidelity` constructed in the exporter's `policy` mapping
- `analysis_snapshot_hash`: `H` of exactly the importer's `ANALYSIS_FIELDS`
- `card_snapshot_hash`: `H` of exactly `CARD_FIELDS`, or `H(null)` when absent
- `snapshot_hash`: `H` of the entire article object excluding `snapshot_hash`
- `manifest_hash`: `H` of the entire packet excluding `manifest_hash`, including
  selection metadata, hash specification and all nested hashes

The shell `sha256sum` is an optional transport hash of the **file bytes**, distinct
from the canonical `manifest_hash`. Reformatting JSON changes the former, not the
latter. Reconstruct `policy_snapshot_hash` from these exact keys:
`analysis_prompt`, `analysis_prompt_sha256`, `max_output_tokens`,
`translation_prompt`, `translation_prompt_version`, `translation_prompt_sha256`,
`analysis_fidelity`, `translation_fidelity`.

The importer continues to validate its established chain and CAS projections; it
does not interpret the additional `hash_spec` or selection-report fields. Those
fields are nevertheless covered by `manifest_hash`. A version change must not
silently redefine existing digest or importer semantics.

## Validation

`tests/test_dot_url_article_export.py` uses temporary databases and fake live-feed
metadata only. It covers the read-only authorizer/no-DML guard, WAL concurrency,
pre-limit exclusions, raw prompt/null hashes, scope/feed/URL rejection, every peer
lane and leases, missing/incomplete state, retry boundaries, short/empty reports,
real importer dry-run and post-export CAS/source/policy/claim/lease/feed rejection.
Run the complete unchanged Reader regression workflow before merge. Production
export acceptance and issue closure happen separately after merge/review.
