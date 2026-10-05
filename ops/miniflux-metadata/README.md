# Issue 73: Miniflux metadata projection candidate

Private downstream patch for official Miniflux **2.3.3**, exact commit
`c4d54f87a81b30aa173fddf05d7ff83ae7da5796`. This is an independently testable
source/build candidate, not authorization to deploy. No runtime deployment,
credentials, grants, service settings, or production data are changed here.
No upstream tree is vendored: the verifier fetches exact official source.

## Protocol v1

`POST /v1/entries/metadata`, using the existing user's `X-Auth-Token` or Basic
authentication. The only request object is `{"entry_ids":[1,2,3]}`. The exact
successful envelope is `{"entries":[{"id":1,"user_id":1,"feed_id":2,"title":"…","url":"https://example.org/article","published_at":"…"}]}`.
Responses set `Cache-Control: no-store` and `X-Reader-Entry-Metadata: 1`.
An authenticated empty-array POST is the capability probe; the ordinary version
string alone is insufficient. Reader requires the capability header and exact DTO.
The URL extension retains capability `1` and compatibility version `2.3.3`;
therefore an empty probe cannot distinguish the previous five-field binary.
The paired release must pin the real binary checksum and verify a nonempty
six-field response. A previous five-field nonempty response fails closed with
Reader HTTP 503, even when its capability header is `1`.

- Exactly one `entry_ids` key; unknown/duplicate/case-alias keys, missing/null IDs,
  trailing JSON, every URL query (including a bare `?`), zero/negative/fractional,
  string or overflowing IDs fail HTTP 400 before the metadata query
- At most 10,000 positive signed-int64 IDs and 256 KiB of JSON, including whitespace
  and duplicate IDs. Boundary values are accepted; excess fails, never truncates
- Explicit empty array returns `{"entries":[]}` without a metadata SELECT
- Duplicate candidates yield one row. Numeric entry-ID order is ascending
- Only authenticated user's current read/unread entries with consistently owned
  user/feed/category parents are returned. Admins have no cross-user bypass
- Foreign, removed, deleted and nonexistent IDs are omitted identically. No totals
  or reason codes reveal why. Omission does not authorize deletion of Reader notes
- A single parameterized `SELECT` binds authenticated user ID and the full candidate
  array, selecting id/user_id/feed_id/title/url/published_at plus internal timezone
- `url` is the current stored entry URL, returned verbatim without normalization,
  truncation, a new permission or a new account scope. Reader requires a string
  containing at least one non-whitespace character. Feed refresh can change URL
  without changing `changed_at`, so the latter is not URL freshness evidence
- No body, cookie, enclosure, note, analysis, feed settings or token field is
  selected or serialized. Heap pages may contain other columns; this is projection
  avoidance, not a claim of zero physical access to such pages
- PostgreSQL `AT TIME ZONE` and upstream `timezone.Convert` preserve ordinary
  2.3.3 timestamp semantics, including its DST ambiguity behavior
- Five-second query deadline and request cancellation; query/scan/late iteration
  failures return HTTP 500 with no partial successful entries. Connections/rows close

Authentication middleware is unchanged. It performs its existing user login and
API-key last-used timestamp UPDATEs; **this is not a zero-DML API request**. The new
metadata operation itself is SELECT-only. Invalid nonempty API key takes precedence
over valid Basic credentials and returns 401; a valid key overrides another user's
Basic identity. Upstream Basic-password database errors return 401, while API-key
lookup errors return 500. The patch preserves these established behaviors.

## Files and build

- `miniflux-2.3.3-entry-metadata.patch`: three production files only
- `tests/`: three Go test overlays; no new Go module dependency or production hook
- `pins.json`: official commit/blob, toolchain checksum, PostgreSQL digest, patch and
  test digests; manually review any change to these trust/compatibility inputs
- `scripts/verify.py`: fresh-source fetch, fail-closed pristine/apply/patched checks;
  reject masked index entries (including assume-unchanged/skip-worktree), hash every
  tracked working file against the pinned tree or exact patch allowlist, and check
  file types, executable modes and symlink target bytes independently of Git diff
- `scripts/bootstrap-go.sh`: official Linux-amd64 Go 1.26.8, SHA256 checked before use
- `scripts/test.sh`: format/vet/upstream race tests, required PostgreSQL integration,
  dedicated fault/contract tests, module verification, reproducible candidate binary
- `.github/workflows/miniflux-metadata.yml`: isolated hosted job, read-only repository
  permission, ephemeral PostgreSQL, pinned Actions, no production secrets

From the Reader repository root, with an empty scratch location:

```sh
python3 ops/miniflux-metadata/scripts/verify.py package
python3 ops/miniflux-metadata/scripts/verify.py fetch /tmp/issue73-source
python3 ops/miniflux-metadata/scripts/verify.py apply /tmp/issue73-source
bash ops/miniflux-metadata/scripts/bootstrap-go.sh /tmp/issue73-go
export PATH=/tmp/issue73-go/go/bin:$PATH
# Only a disposable loopback PostgreSQL fixture. Never substitute production.
export ISSUE73_POSTGRES_URL='postgres://issue73:issue73-disposable-only@127.0.0.1:55473/issue73_metadata_test?sslmode=disable'
export ISSUE73_DISPOSABLE_POSTGRES=1
bash ops/miniflux-metadata/scripts/test.sh /tmp/issue73-source /tmp/issue73-evidence
```

The hosted workflow provisions that database automatically. Local integration
refuses a non-loopback URL, another database/user, or a missing explicit disposable
marker. Each run migrates a new isolated fixture schema using the exact official
migration code and drops only that schema at completion. This schema creation is
**test setup**, not a production migration required by the patch. No credentials
are generated or persisted for real accounts. Existing remote/feed integration
suites that require their own TEST_MINIFLUX_* configuration remain skipped; the
new metadata integration requires no remote feed or external account.

The final Go build uses `-trimpath`, `-buildvcs=false`, the stable compatibility
version `2.3.3`, the fixed upstream commit and no wall-clock build date. A custom
version suffix blocked Reader's supported-release gate in the real browser. The
private downstream build is identified by its pinned binary checksum, patch and
build/module receipts; the authenticated metadata capability probe remains
independent of the ordinary version string. This is not an unpatched upstream
binary. Paired acceptance checks the actual `/v1/version` response. Hosted runner
OS state is recorded but is not claimed to be an immutable runtime image or a
complete deployment/SBOM artifact.

## Acceptance coverage

Go unit tests exercise strict parsing, trusted context enforcement, exact body and
ID boundaries, bound arguments, exactly one projection statement, storage validation,
pre-canceled context, timeout, row closure, scan and late-row failure. PostgreSQL
tests exercise actual migrations/schema, Basic/API-key auth, token precedence,
revocation, multi-user/admin isolation, corrupt/missing parents, exact six-field
DTO, 10,000 actual results, body/enclosure-independent payload, title freshness
and URL freshness without changed_at, ordinary-query timestamp parity across DST/offset/subseconds/
minimum date, normal auth audit writes, real blocking query cancellation/deadline,
connection release/reuse, ordinary entries/me and unchanged OPTIONS behavior.
Driver wrappers record SQL text without parameter values. Deliberate query/scan/
late-iteration faults verify HTTP failure rather than partial/empty success.

See the exact-head workflow artifacts for pass/fail evidence. Compilation alone,
a dry run, a skipped database test, or a green Reader-only workflow is insufficient
for Miniflux acceptance. `test.sh` explicitly requires its PostgreSQL suite.

## Rollout, limitations and paired rollback

Do not deploy from this PR without separately authorized staging/deployment work.
1. Review/accept maintaining this private downstream Miniflux build and the admitted
   candidate count. Confirm actual proxy body admission, candidate count and timeout
2. Build and stage pinned Miniflux with synthetic data; verify authenticated empty
   capability probe and failure/ownership behavior. Retain prior exact binary/image
3. Deploy Miniflux capability before enabling Reader's explicit
   `READER_NOTES_METADATA=1` switch. The default Reader path remains the prior path
   for notes. The recommendation-quality consumer also requires this exact URL
   extension whenever its current scope contains source-bound exclusions, so stage
   the reviewed six-field Miniflux binary before the quality Reader release
4. Once enabled, unsupported/missing headers, auth/provider errors, oversize inputs
   and malformed/cross-user metadata fail visibly. Never silently switch to bodies,
   split one admitted snapshot into batches, delete notes, or return total zero
5. Reader note, visibility/feed, metadata and hydration reads are separate snapshots.
   This patch guarantees only one metadata statement snapshot. Reader must perform
   its documented contradiction repair; there is no distributed transaction
6. Roll back **Reader first**: disable the explicit consumer switch or restore its
   known-good prior release. Disabling only the notes switch does not disable the
   recommendation-quality consumer; for this extension restore the prior Reader
   release before restoring the five-field binary. Verify notes/ordinary entries, then restore the exact
   prior Miniflux image/binary. Verify auth/health/entries again. Preserve notes and
   volumes. The patch adds no schema or stored metadata state to roll back
7. Any upstream upgrade requires a fresh auth/schema/timezone/route review, explicit
   pin and digest changes, full tests and paired rollback artifacts. Unsupported
   source commits or edited source fail closed before application/build. Do not
   defer upstream security updates to preserve this patch: if timely rebasing is
   unavailable, roll Reader back first and run the updated unpatched Miniflux
   release until the paired candidate passes again

## Official provenance checked 2026-10-01

- [Miniflux exact source](https://github.com/miniflux/v2/tree/c4d54f87a81b30aa173fddf05d7ff83ae7da5796)
- [Official Go downloads/checksums](https://go.dev/dl/?mode=json)
- [Docker official PostgreSQL metadata](https://github.com/docker-library/repo-info/blob/master/repos/postgres/remote/17.md), blob `757a99f49bd489e96166653ec10f96a24e93ef67`, image index in `pins.json`, PostgreSQL 17.11
- [Reviewed checkout commit](https://github.com/actions/checkout/commit/3d3c42e5aac5ba805825da76410c181273ba90b1)
- [upload-artifact v7.0.1 commit](https://github.com/actions/upload-artifact/commit/043fb46d1a93c77aae656e7c1c64a875d1fc6a0a)

## Local verification before hosted CI

On 2026-10-01, checksum-verified workspace-local Go 1.26.8 compiled the full
candidate and test suites. `go vet ./...` passed; all nine metadata unit-test
functions (plus their subtests) passed under `-race`; all 11 offline compatibility
negative tests passed; gofmt and shell syntax checks passed. The exact official
fetch/apply/patched gates passed.

The full `go test -race -count=1 ./...` run passed every test package except
`internal/http/server`, whose existing Unix-socket test is blocked by this cloud
sandbox (`socket: operation not permitted`). The PostgreSQL integration was
compiled but skipped locally because no disposable PostgreSQL service was present.
Neither limitation is counted as a full pass: the hosted workflow must pass the
unchanged whole suite and the explicitly required real PostgreSQL/auth matrix.
