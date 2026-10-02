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
authorizes mutation of that existing ID only. It cannot prepare or submit a
prepared batch, drain into another batch, produce a replacement, or bypass
lane/batch/scheduler cooldown. It may status/download/validate/import the same
submitted batch. Uncertain missing remote IDs are retained without absence
retirement. Complete valid import still finishes the same batch normally.

Explicit `--reconcile-readonly` is a local, mode=ro self-ledger snapshot. A
scheduled `reconcile_only: true` cycle uses the same local observer. Neither
constructs Controller (which can migrate/backfill), reads credentials, invokes
status/advance, imports, creates locks/files, nor writes ledgers. The scheduler
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
provider request authorized before stop cannot be cancelled by these checks; a
660-second observer sleep checks again before the next provider request. No
remote job cancellation or global atomic stop acknowledgement is claimed.

Watchdog snapshot retention intentionally still runs before tick. Zero-provider/
service/ledger-write claims apply to stopped dispatch and the read-only observer,
not the entire watchdog (retention may prune its separately managed snapshots).
No authentication, permissions, SQL schema or production setting was changed.

## Validation and handoff

All tests use temporary state and mocks for provider, systemctl, HTTP,
credentials and clock. External IPv4/IPv6 sockets are prohibited locally.
Positive legacy test fixtures now explicitly declare schedule_enabled true;
their behavioral assertions are retained. Full Kaggle and month/watchdog targeted
suite: **415 passed plus 59 subtests**, 24.26 seconds, with no skip.
Evidence is `/tmp/scheduler-stop-admission-evidence/targeted-final-2.{log,xml}`.
Hosted exact-head CI results are recorded in the PR.
AST and git diff --check are required. Local lefthook is unavailable and is not
bypassed. Hosted Reader CI supplies full Python/JavaScript/build/browser checks.
The source commit/tree must be independently reviewed before paired CODE pin
promotion; strict paired guards must stay unchanged. No merge/deploy/issue close.
