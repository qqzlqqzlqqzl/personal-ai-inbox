"""Single systemd entrypoint; lane_scheduler owns all dispatch/recovery policy."""

if __name__ == '__main__':
    import argparse
    argparse.ArgumentParser(allow_abbrev=False, description='Run one recovery watchdog tick.').parse_args()
import json
import httpx


def run_once(tick, prune):
    """Do not retry a partial tick blindly; systemd retries the locked scheduler."""
    try:
        retention = prune()
    except Exception as exc:
        retention = {"state": "error", "error_type": type(exc).__name__}
    try:
        report = tick()
    except httpx.HTTPError as exc:
        # No exception messages: upstream URLs/headers can contain credentials.
        return {"state": "dependency_unavailable", "error_type": type(exc).__name__,
                "retry": "systemd", "snapshot_retention": retention}, 1
    except Exception as exc:
        return {"state": "scheduler_error", "error_type": type(exc).__name__,
                "retry": "systemd", "snapshot_retention": retention}, 1
    return {**report, "snapshot_retention": retention}, 0


def main():
    if __package__:
        from .lane_scheduler import tick
        from .snapshot_retention import locked_prune
    else:
        from lane_scheduler import tick
        from snapshot_retention import locked_prune
    report, code = run_once(tick, locked_prune)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
