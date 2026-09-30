# Recovery packaging and independent review follow-through

## Restored tracked behavior

The release checkout imported `recovery_policy` and `exception_audit`, but neither file existed anywhere in its Git history. This follow-through restores the narrow interfaces used by the tracked scheduler: finite nonnegative retry timestamps (invalid metadata makes a lane ineligible), fixed-code provider errors, and append-only credential-free scheduler audit records.

Existing uncertainty tests assumed a raw not-found response, or an empty list plus readable quota, proved a batch absent. Independent review rejected that assumption: authentication or account mismatch can hide a private notebook. Those tests were strengthened instead of making an unsafe implementation pass them.

## Conservative absence reconciliation

- No submission, cancellation or remote write is performed during reconciliation
- Only `submitting` / `submit_unknown`, with no previously observed remote status, and at least 1,800 seconds of uncertainty can be considered
- Official `kernels list --mine --csv --page-size 100 --page N --sort-by dateCreated` is paged until an explicitly empty page, even after a short page; max 10 pages / 1 MiB per page
- At least one own-list result must positively establish the expected owner. Empty accounts, any foreign owner, duplicate/malformed pages, truncation, exact target presence, provider/auth failures or unreadable quota cannot prove absence
- Readable zero quota is sufficient for read-only account evidence; it never permits new GPU work
- Two fresh negative receipts must bind owner, exact batch, original ledger state/update and listing evidence. They must be 660–3,600 seconds apart; legacy/unbound/stale receipts cannot retire a claim
- The final retirement compares the unchanged ledger under a write transaction. A concurrent known-running/result state wins
- Retirement is not import and does not create a new attempt. A repeated immutable manifest reports `retired_manifest_requires_new_attempt`; drain exits with `recovery_required`, and scheduler does not keep reextracting it. An operator must resolve that explicit block after checking the evidence before deliberately creating a new attempt

Official CLI option reference: https://github.com/Kaggle/kaggle-cli/blob/main/docs/kernels.md

## Independent-review fixes

- Adafruit malformed original-body href, canonical URL or redirect Location now returns a fixed safe fallback reason and preserves the full source summary
- Malformed feed URLs no longer turn the combined source-history endpoint into a 500; valid stored history remains available while the feed window is unknown

## Verification

The full local suite, including both previously excluded scheduler/live-scope files, runs without exclusions. New regressions cover full pagination, later-page target presence, foreign/empty account ambiguity, unreadable quota, raw error redaction, timing and evidence binding, ledger races, no blind resubmission, terminal drain behavior, scheduler manual-recovery block, audit permissions/symlinks and malformed source URLs. Final counts and reviewer outcome are recorded in the PR.

This is a reconstruction of the tracked callers' safe contract, not a claim of byte-for-byte parity with uncommitted production modules. Before deployment, reconcile the deployed code and retained state. The real-device, 24-hour, installed-PWA, ranking-snapshot and disaster-recovery criteria in #3 still need their own evidence.
