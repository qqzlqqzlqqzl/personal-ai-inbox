# Production recovery integration

Base release: 3a64df3. The server still had reviewed release 3b7a29f plus unpublished recovery and publisher adapters. This change integrates those implementations in isolation, preserving production databases and existing batch manifests.

- Preserve durable batch_claims, cross-lane preparation leases, per-lane locks, terminal local retry, selective salvage and upstream-unavailable recovery.
- New submissions require fresh quota strictly above one hour; unknown/stale quota blocks. Existing downloads/imports do not require quota.
- Use the release account-bound complete-list absence proof twice, separated by 660 seconds and after 1800 seconds; ambiguous claims remain claimed. Retired manifests require explicit recovery rather than an automatic new GPU attempt.
- Preserve server publisher/WordPress adapters and integrate Adafruit followed originals, summary fallback and durable prepared bodies with consistent source hashes.
- Preserve bounded recovery audit and strict scheduler audit. Retry records use fail-closed validation; authentication aliases receive the same long cooldown.

Independent read-only review found no blocking claim/lease/quota/recovery issue. Local full suite: 471 passed, 1 skipped, 59 subtests. Isolated Vite build validated. Browser and production readback results are recorded separately after completion. No GPU/paid model tests or historical AI recalculation were run.

Private code and database backups remain on the server; do not commit those files.
