# Kaggle one-hour reserve (#41, duplicate #42)

## Enforcement

- The lane scheduler checks official `kaggle quota --format json` before starting new work. The five independent read-only checks run in parallel. A lane with no trustworthy quota is not eligible for new work.
- New work means both an empty lane and a never-submitted `prepared` batch. `submitting`, `submit_unknown`, submitted/running and downloaded/terminal batches remain eligible for reconciliation without quota. No cancellation is introduced.
- The bridge checks fresh quota before preparing another batch, after returning any outstanding batch. The final Controller.submit boundary checks the official CLI again immediately before committing the submit transition. This prevents stale scheduler/GUI cache or a quota change during extraction from bypassing admission.
- A blocked submit remains `prepared`; it is not marked uncertain, cancelled or retired. No GPU request was made. Quota recovery above 1.00h resumes automatically.
- Drain exits when pre-prepare or final-submit admission blocks. Existing remote work can finish/download/import first, then the next prepare rechecks quota.
- The resource dashboard separately shows running/recovery state, remaining quota, and “额度保护 / ≤1h 停用新批次” or unknown/stale protection. Display cache is never used for the final submission decision.

## Fail-closed inputs

0h, 0.26h and 1.00h block; 1.01h permits submission. Missing, negative, NaN, Infinity, boolean, invalid JSON, duplicate GPU rows, provider exceptions, stale cache, missing timestamps and future timestamps fail closed. Errors return only a fixed safe reason. Account token contents are neither read by this code nor returned/logged; existing CLI credential-file references remain within the child process environment.

## Tests and boundaries

The new tests exercise parsing, expiry, recovery eligibility, five-lane planning, direct Controller.submit, automatic quota recovery, output download/validation, and two drain transitions. All provider calls are synthetic; no GPU notebook was launched or cancelled, no production config was changed, and no token was read.

The existing `test_lane_scheduler.py` / `test_live_scope.py` cannot collect from a clean repository because production imports `exception_audit` / `recovery_policy` are missing in Git. Planner logic was extracted into the dependency-free guard module and tested directly; this is not a claim that the whole production scheduler has passed. Reconcile the server's untracked modules before deploying, then verify official quota and protected dashboard states on the five live lanes while preserving running jobs.

The safety floor is an admission reserve, not a runtime cap: a job launched at >1h may continue below 1h. That is intentional per the issue's instruction to avoid cancelling existing work.
