# Required ledger dispatch review

Refs #87. Base: `da5a6222779517b92782bf19e2a2166801676fb4`.

## Result and self-review

The missing-ledger `continue` is replaced by `DispatchBlocked(local_state)`.
The successful API remains `set[int]`; no empty or partial result is returned on
any required-ledger failure. Read-only snapshots explicitly close their SQLite
connections. Valid claim rows remain authoritative over missing or corrupt
manifests; complete anchored manifests support the legacy fallback.

Checks cover new prepare, extraction lock reacquisition, immutable manifest
publication, prepared submit and scheduler startup. Self is always required;
all five named campaign roots also apply to the standalone Controller CLI.
Scheduler topology failure starts no services. Initial installation is an
explicit init-only path; normal connections use `mode=rw` and cannot recreate a
missing DB. Initialization rejects sidecars/batch evidence and corrupt DBs.
Same-ID submitted/running/uncertain status recovery remains available with a
blocked peer. No retirement/absence-proof policy was changed. Retry expiry does
not release claims. Failure reports use fixed reason and ordinal peer labels,
reuse existing local-state backoff, and count bridge/cycle failure once.

No tables, columns, authentication, lane configuration, settings, frontend,
NewAPI or provider/model/GPU behavior was added. Existing fresh test fixtures
now explicitly initialize and use complete manifests; old assertions remain.
No repository AGENTS.md or SKILL.md was found; the Kaggle RUNBOOK and existing
pytest/unittest/CI conventions were inspected.

## Verification

761 passed, 1 skipped, 2 warnings, 161 subtests passed in 81.06s (0:01:21)

The final Python suite ran from a disposable `/tmp` copy, with
`initialize_secrets.ROOT` pointed there and IPv4/IPv6 socket connections
forbidden. Systemctl, Kaggle, HTTP/extraction and credentials in the new
regressions use synthetic/mock boundaries. Current machine load/memory were
checked before each full run; no build/browser suite was started on the shared
machine. Changed Python source was byte-compared against the tested copy.
`git diff --check` and AST parsing passed.

Console/JUnit evidence is retained at
`/tmp/required-ledger-validation/python-exact-candidate.log` and
`/tmp/required-ledger-validation/python-exact-candidate.xml`.
Initial failure logs remain in the same evidence folder: missing generated
frontend input and old live-scope fixtures lacking an explicit self ledger were
fixed before the final full run. The unchanged generated frontend was copied
read-only from the current release solely as Python source-test input. A pinned
frontend clone/prepare attempt failed because the target was already populated;
no build or browser result is claimed.

## Limits and parent handoff

This establishes source and isolated test behavior, not production ledger loss
or a deployed recovery. No live systemctl/Kaggle/GPU/model/HTTP request,
production ledger inspection/mutation, merge, deployment or issue closure was
performed. Full hosted JavaScript/build/Chromium CI and real production
acceptance remain for the parent review. SQLite provides one snapshot per
ledger; cross-ledger atomicity still depends on the existing coordination lock
and repeated admission checks. The complete five-lane layout is the existing
named `kaggle-month-*` family; arbitrary layouts still require explicit peers.
Independent parent review is required before merge/deploy.
