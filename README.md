# cloud-maintenance

Operations tooling that runs on the reader server (VM-0-4-ubuntu) as the `ubuntu` user.
Versioned here since 2026-11-12; before that it existed only on the server.

## What runs
| Unit | Cadence | Script | Does |
|---|---|---|---|
| `cloud-recovery.timer` | every 5 min | `recovery_agent.py --apply` | health checks of Miniflux, RSSHub, web gateway, New API; bounded restarts (3 failed checks, max 2/h, 10 min apart); retry of feeds stalled >= 3 parse errors; New API log rotation; read-only Kaggle queue and vendor source status |
| `cloud-repair-rollback.timer` | every 5 min (drop-in; was 20 s) | `repair_guard.py --recover` | replays the compensation journal of the two fixed data repairs if a repair was interrupted |

`ops_agent.py` / `ops_review.py` / `ops_protocol.py` / `ops_runbooks.py` / `production_cases.py` are the
"Qwen operations" loop: Kaggle batches of the primary lane reviewed operations evidence and the server
executed allow-listed actions. **Disabled on 2026-11-12** (`~/ai-news/.private/recovery-agent/qwen-settings.json`
`"enabled": false`); the native rules above keep working. `repair_guard.py` still imports `ops_runbooks`
and `production_cases`, so these modules must stay.

`systemd/` holds copies of the user units and drop-ins as installed in `~/.config/systemd/user/`.

## Not versioned
- `security-20261023/`: the 2026-10-23 security upgrade. Live parts: `go-candidates/` (miniflux, mihomo,
  x binaries), `desktop-commander-candidate-v2/`, `rsshub-candidate-v2/`. Also pre-upgrade database dumps
  and venv archives kept for rollback.
- `backups/`, `newapi-metrics-syscalls.trace`: root-owned.
