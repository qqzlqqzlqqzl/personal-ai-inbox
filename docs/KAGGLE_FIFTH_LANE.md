# Fifth Kaggle lane

2026-09-26: Added account ytwbxa as fifth to the existing shared article queue.
Five lanes share peer claims, the existing allowlist, backoff, and exception audit.
Credential stays in .private; runtime Dataset is private and reuses the pinned archive.
No model, context, batch size, or article scope change.
Baseline backup: /home/ubuntu/ai-news/backups/add-fifth-1790420079
GPU completion is not implied by credential validation or submission.

## Five-lane recovery check (2026-10-06)

The five-lane scheduler remains capped at five independent accounts. A working
primary lane does not prove the other four have recovered. Inspect the effective
runtime configs after resolving symlinks: all intended lanes need
`schedule_enabled: true`; each config's `source` and the effective systemd
`cloud_cycle.py` entrypoint must use the same current release. Instance drop-ins
can otherwise hide an outdated common template.

Use the existing CLI and credentials for fresh quota/status checks. Then let
the existing scheduler assign unclaimed work. Verify separate evidence for
service launch, remote RUNNING, and validated import; quota success and submitted
batches do not prove GPU execution. Preserve quarantined historical claims until
their own remote outcomes are established. No full source/environment copies or
replaying unknown batches are needed to restore fresh parallel dispatch.
