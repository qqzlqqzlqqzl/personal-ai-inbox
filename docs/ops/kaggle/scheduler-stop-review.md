# Scheduler stop admission review

Issue #94 fixes the confirmed released source defect at
`0d77969743868cd4ff859a4aec2784115c8c49c8`. The read-only operator report
Library `libfile_732a97fa398c819193e8befa6b1f7bdf` v0 was materialized through
Library prepare_materialize into `/tmp/scheduler-stop-admission-evidence`.
Its SHA256 is `8bddca8048eb4af74f6cdda56bc11b13011b6f6b0308e4933ace6fadf2536f39`.
No production unit/config/provider commands were run by this repair.
Searches for schedule_enabled, stop scheduler and watchdog found no duplicate;
#52 is the completed scheduler migration, not this admission defect.

## Policy

Automatic mutation needs the exact selected configuration with strict boolean
`schedule_enabled: true`, no global pause marker, and no `reconcile_only: true`.
False, missing and non-boolean schedule flags deny admission. Unknown reconcile
values also fail closed; existing absent/null reconcile_only means false.
The scheduler checks this before provider quota, not just at planning. Disabled
lanes cannot qualify as quota-independent old-batch recovery. Configs are reread
before each service start, and cycle/bridge workers pin their selected config
and reload it at provider and local mutation boundaries. Cycle supplies its
canonical typed config SHA256 to bridge children so a still-enabled changed
identity/config cannot be accepted as a new child baseline. This fingerprint
binds source configuration; it is not new authentication. The drain checks again
after sleeps and subprocess results. Stopping is not recovery success: existing
recovery counters/cooldowns are not reset. Required-ledger claims and existing
660/1320/2640/3600 backoff remain unchanged.

The obsolete broad `--manual` bypass now exits with
`manual_authorization_required`, even if present in an old installed unit.
Explicit `--manual-recovery --batch ID` (bridge action `recover --batch ID`)
binds provider/lifecycle/import work to that existing ID. Controller retains
existing shared-table compatibility setup, but claim backfill is restricted to
the selected ID; it does not backfill unrelated pending IDs. Legacy valid
manifests still reserve those unrelated entries. It cannot prepare or submit a
prepared batch, drain into another batch, produce a replacement, or bypass
lane/batch/scheduler cooldown. It may status/download/validate/import the same
submitted batch. Uncertain missing remote IDs are retained without absence
retirement. Complete valid import still finishes the same batch normally.

Explicit `--reconcile-readonly` is a local, mode=ro self-ledger snapshot. A
scheduled `reconcile_only: true` cycle uses the same local observer. Neither
constructs Controller (which can migrate/backfill), reads credentials, invokes
status/advance or imports, creates application locks, migrates schema, nor
changes logical ledger data. SQLite mode=ro may create/update WAL/SHM coordination sidecars; this is
a logical read-only observer, not a filesystem no-write promise. A genuinely
read-only filesystem may cause it to fail closed if SQLite cannot coordinate.
We deliberately do not use immutable=1, which can omit current live WAL state.
The report exposes logical_readonly and sqlite_sidecars_may_change. The scheduler
never starts services merely for this local observer. Disabled configuration
alone never authorizes the observer: use its explicit CLI flag. The observer
validates its self ledger, and does not claim that all peers admit new dispatch.

Month-control enabled reflects schedule flags and global pause. Disabled status
polling uses quota cache only, including expired/missing cache, without refreshing
or rewriting it. Per-lane quota gates are disabled. Resume removes a pause marker
but does not alter schedule_enabled or implicitly enable work.

## Scope and races

The operator report identifies a deployed month lane unit containing --manual;
there was no tracked month template at this baseline. The new reviewed template
at deploy/systemd/ai-news-kaggle-month@.service uses the exact %i config and no
manual bypass. This task does not install or replace the deployed unit. Root
must review deployment of both code and unit; the old unit fails closed meanwhile.

The scheduler lock remains shared with month-control pause, so its pause API
waits for scheduler admission before recording stop. Raw external config edits
are not a distributed transaction: a previously admitted service can already be
queued via systemctl --no-block. Its worker rechecks before work. An in-flight
request or transaction admitted before stop may finish; the next request or
transaction rechecks. HTTP admission also covers nested source clients and
redirect hops, browser request routes, per-article writes and card enqueue.
Business and exception imports check before each item transaction; the first
committed item/receipt is preserved if stop prevents the next item. Failure
handlers recheck before any audit/backoff mutation. Lease cleanup skips a new
transaction after stop; the existing 660-second lease expiry bounds that hold. A
660-second observer sleep checks again before the next provider request. No
remote job cancellation or global atomic stop acknowledgement is claimed.

Watchdog snapshot retention intentionally still runs before tick. Zero-provider/
service/logical-ledger-write claims apply to stopped dispatch and the observer,
with the observer WAL/SHM coordination exception above,
not the entire watchdog (retention may prune its separately managed snapshots).
No authentication, permissions, SQL schema or production setting was changed.

## Validation and handoff

All tests use temporary state and mocks for provider, systemctl, HTTP,
credentials and clock. External IPv4/IPv6 sockets are prohibited locally.
Positive legacy test fixtures now explicitly declare schedule_enabled true;
their behavioral assertions are retained. Full Kaggle and month/watchdog targeted
suite: **415 passed plus 59 subtests**, 24.26 seconds, with no skip.
Evidence is `/tmp/scheduler-stop-admission-evidence/targeted-final-2.{log,xml}`.
A subsequent import-seam follow-up adds checks after validation/upstream/import
and before defer/resolve/report writes: **207 targeted passed**, including three
new stop-race cases, on the updated source. Hosted exact-head CI results are
recorded in the PR.
AST and git diff --check are required. Local lefthook is unavailable and is not
bypassed. Hosted Reader CI supplies full Python/JavaScript/build/browser checks.
The source commit/tree must be independently reviewed before paired CODE pin
promotion; strict paired guards must stay unchanged. No merge/deploy/issue close.


Independent review of d092e452 rejected six concrete counterexamples despite
its green CI. The regression source was verified Library
`libfile_0543ff6774dc8191a4e6126fb5ae0ada` v0,
`Reader_PR95_d092e452_Independent_Counterexamples.zip` (18,622 bytes), SHA256
`d2080948bb78ce949cc4feee23b4d6e1b107ed363c2612bfb970ddc3d15a8459`.
The corrected source retains those extraction/import/failure/manual/WAL cases
as behavioral tests, plus typed true→1/1.0 and false→0/0.0 config races,
redirect/fallback stop checks and exception per-item audit/transaction checks.
Guard callbacks are optional for existing source helpers; the paid-provider
branch stays unchanged. No new tables, credentials or permission changes were
introduced by this follow-up. This remains pending independent source approval.


The revised local targeted run uses the complete Kaggle + month/watchdog suite
and affected source-helper tests. An exploratory whole-Python invocation in the
archive-only temporary directory lacked the pinned ReactFlux checkout and Git
history fixtures; those frontend fixture checks are deferred to hosted Reader
CI, which supplies both. This is not treated as a successful whole-suite run.

Revised targeted verification: **455 passed + 59 subtests, no skip**, covering
all six reviewed cases and affected source helpers. Evidence:
`/tmp/scheduler-stop-admission-evidence/revision-targeted-final.{log,xml}`.
