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


## Follow-up independent review corrections

The two P2 review findings are fixed on the same draft PR. Failure recording now
validates recovery JSON shape and failure-counter type, so arrays/null/strings,
invalid JSON and invalid counters cannot turn a redacted dispatch block into an
AttributeError traceback. Cycle admission also converts invalid recovery input
to the same fixed typed block before bridge/provider activity.

Scheduler cooldown is now enforced using its persisted dispatch recovery record
and the tick's explicit clock. Before retry_at, it performs no ledger, service or
quota work, starts zero services and does not increment failures. At expiry,
complete topology/claim validation and the final pre-start snapshot must succeed
before clearing the cooldown. Tests cover repaired ledger before/at expiry,
660/1320/2640/3600 capped timing, reset after successful admission, unchanged
article attempts and retained submit_unknown claims. Normal same-ID recovery
branches are unchanged.

Additional real-Controller tests delete/corrupt a peer at prepare's inner
transaction guard, assert rollback and no manifest/runner publication, and never
mock Controller.prepare. A bridge regression adds a real valid peer claim after
extraction releases the coordination lock; the later recheck excludes that
article and publishes only the remaining article through the real Controller.

Targeted follow-up regression: 130 passed. Final full Python and new-head hosted
Reader CI results are recorded in the PR update. Prior-head hosted Reader CI was
independently confirmed green with no skip by the parent. All new evidence is
isolated; no production requests, merge, deployment, or issue closure occurred.

Follow-up full regression: 792 passed, 1 skipped, 2 warnings, 161 subtests passed in 81.24s (0:01:21)

Follow-up evidence: `/tmp/required-ledger-validation/python-p2-final.log` and
`python-p2-final.xml`; log SHA256 `419dbc630fc483219a9cb05fb2ef69e32cafc1be10fba276cda5296bc34dcccd`.
Changed Python files were byte-compared with the full-suite source copy.
