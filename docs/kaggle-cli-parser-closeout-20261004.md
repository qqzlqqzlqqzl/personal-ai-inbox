# Official Kaggle CLI parsing closeout

This narrow transplant starts from fa7196b7aca8f9c4f7256c20dd24eaeaf62e3140. Its source is PR68 head 0a71b93bd1ba9a0364ab1ed08b1558dee87e797f. It does not merge the old branch or restore its older scheduler/controller interfaces.

The parser accepts the exact optional official version warning and `Next Page Token = ` preamble emitted by Kaggle CLI v2.2.4. Continuation metadata remains in memory; numeric pagination, the ten-page bound, expected owner, exact target ID, complete row/ref integrity, nonempty owner evidence and authenticated readable quota requirements remain. Empty refs, duplicate fields, malformed/truncated rows, repeated/reordered/unrecognized metadata, token-bearing empty pages and repeated tokens cannot prove absence.

Official source: https://github.com/Kaggle/kaggle-cli/blob/v2.2.4/src/kaggle/api/kaggle_api_extended.py (kernels_list_cli and _check_response_version). No Kaggle provider execution is required to test these formats.

The original15 unittest methods are preserved byte-for-byte in test_absence_cli_format.py. A separately named current24-format matrix is in test_absence_cli_formats_closeout.py. The historical reviewer's24-case script was not available: these current cases are new and must not be described as a replay of that missing script. Two additional synthetic incident/control tests retain the100-row/73-empty-ref rejection and verify that ambiguous output leaves submit_unknown, immutable claims and cooldown intact while breaking the prior negative-proof sequence.

Only one existing recovery fixture changes: an empty CSV header is replaced by the official `Not found` terminal output. The current initialized Controller, source_refs, schedule_enabled flags, stop checks and authorize callbacks are retained. The controller's30-minute grace, two observations separated by660 seconds, proof freshness window, current-ledger compare-and-swap and recovery/cooldown policy are not modified.

This is a CLI-format fix, not proof that the five production submit_unknown jobs are absent or resolved. The reported incident had73 empty refs and27 identifiable rows without a warning/token preamble; that evidence remains insufficient and rejected. No claims are deleted and no retry, submit, GPU, provider, service or production operation is performed by these tests.

This candidate changes the whole-src identity. It does not promote CODE, update the pair pin, merge or deploy. The final integrator must bind the combined whole-src/CODE/pair and run exact-head fullCI and production acceptance separately.
