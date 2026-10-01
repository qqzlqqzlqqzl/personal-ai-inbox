from unittest.mock import Mock
import httpx
from kaggle_batch.recovery_watchdog import run_once


def test_success_preserves_scheduler_fields():
    original = {"state": "paused", "lanes": {"primary": {"quota_gate": {"allowed": False}}}}
    tick = Mock(return_value=original)
    report, code = run_once(tick, lambda: {"state": "ok"})
    assert code == 0 and report["lanes"] == original["lanes"]
    assert "snapshot_retention" not in original
    tick.assert_called_once_with()


def test_reader_unavailable_is_retryable_without_leaking_secrets():
    tick = Mock(side_effect=httpx.ConnectError("SECRET in URL"))
    report, code = run_once(tick, lambda: {})
    assert code == 1 and report["state"] == "dependency_unavailable"
    assert "SECRET" not in str(report)
    tick.assert_called_once_with()


def test_partial_tick_not_repeated_in_wrapper():
    tick = Mock(side_effect=ValueError("bad state secret"))
    report, code = run_once(tick, lambda: {})
    assert code == 1 and report["state"] == "scheduler_error"
    assert "secret" not in str(report)
    tick.assert_called_once_with()


def test_retention_failure_does_not_skip_scheduler():
    tick = Mock(return_value={"state": "waiting_for_lane_recovery"})
    report, code = run_once(tick, Mock(side_effect=OSError("private path")))
    assert code == 0 and report["snapshot_retention"]["error_type"] == "OSError"
    tick.assert_called_once_with()


def test_unit_has_bounded_restart_and_no_new_scheduler():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    unit = (root / "deploy/systemd/ai-news-kaggle-recovery.service").read_text()
    assert "Restart=on-failure" in unit and "StartLimitBurst=3" in unit
    assert "After=ai-news-miniflux.service" in unit
    assert "n8n" not in unit
