# Acceptance follow-through — 2026-09-30

This record separates newly tested code from deployment and lifecycle acceptance. It does not replace the failed or incomplete evidence already recorded in #3.

## Completed code and local checks

- #36: PR #43 adds controlled Adafruit original extraction and durable reader provenance. Fresh CERN extraction selected 2,485 characters; Medium HTTP 403 exercised the full-summary fallback. No library-wide retry or model/GPU call occurred. Follow-on review additionally invalidates a prepared original when an attribution href changes even if its visible label stays the same.
- #10: source management can request stored count and oldest/newest publication dates from Miniflux; current RSS/Atom window is measured separately and labeled with observation time, missing dates and unknown/error states. Manually added feeds are included. It does not claim archive completeness or implement unbounded historical backfill.
- #41 / #42: one-hour Kaggle admission reserve covers scheduler, next preparation, final submit, drain exit, automatic recovery above 1h, and resource labels. Recovery/download/import paths remain available. Provider errors never return credential-bearing output.
- Local app and available Kaggle suites use synthetic fixtures and temporary databases. The secret-file safety tests run with their ROOT redirected to this checkout and their existing PRIVATE test patches; no real secret files are used.

## #3 remains open

The following original acceptance criteria still require separate evidence:

1. Physical Android/iOS and actual Safari/Firefox sessions
2. A real 24-hour reading/background-workload observation on the deployed target
3. Upgrade from an already-installed old PWA and recovery after browser exit
4. Snapshot consistency while new articles arrive or rankings change across requests
5. Cross-cloud disaster restoration and an independent security review

A short unit test or Chromium-sized viewport is not evidence for these criteria. No fake 24-hour monitor was started without a reachable deployment target.

## Additional baseline blockers found

- `src/kaggle_batch/test_lane_scheduler.py` and `test_live_scope.py` fail collection because `exception_audit.py` / `recovery_policy.py` are imported but not committed. Their recovery tests also describe controller behavior absent from the tracked controller. Server-vs-Git reconciliation is required; inventing replacement recovery behavior is unsafe.
- Repaired the missing clean-rebuild sorter stage with narrow normalization from both known earlier variants. A regression now starts from the exact pinned Git tree, applies all ten stages twice, and verifies byte-identical output. The build also restores upstream’s version-info prebuild before Vite. These checks pass; this fixes reproducibility, not the wider lifecycle criteria above.
- This workspace could compile the actual frontend and exercise component request lifecycle, but browser launch was blocked by the runtime's local socket restriction. The separate cloud browser could not open the loopback server. No successful visual/browser production acceptance is claimed.

## Deployment gates

No production service, user database, budget, job or credentials were modified. A connected execution target must reconcile its uncommitted source, deploy the reviewed release branch, and perform authenticated readback of:

- The two explicitly identified Adafruit entries and their final content-source metadata, without reenqueuing the whole library
- Each Kaggle lane's current official quota and protected/new-work/recovery behavior without deliberately exhausting quota or cancelling a job
- Source history success, authenticated-feed unknown state, empty feeds, changing collections and normal reader navigation

Until those checks have actual results, #36, #41 and #10 remain open for deployment acceptance. #42 is the duplicate of #41 and can close as such without declaring the feature deployed. The wider #3 acceptance backlog stays open.
