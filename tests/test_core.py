import json, math
import pytest
import core
from worker import validate_result
from content_input import is_our_social_feed


def test_canonical_tracking_removed():
    assert (
        core.canonical_url("https://EXAMPLE.org/a?utm_source=x&b=2#section")
        == "https://example.org/a?b=2"
    )


def test_idempotent_discovery(db, entry):
    core.discover([entry])
    core.discover([entry])
    with core.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 1


def test_connection_is_closed(db):
    with core.connect() as c:
        assert c.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(Exception):
        c.execute("SELECT 1")


def test_settings_persistent_and_validated(db):
    core.save_settings(
        {"minimum_score": 8, "prompt": "Return required schema", "daily_articles": 20}
    )
    assert core.settings()["minimum_score"] == 8
    assert core.settings()["daily_articles"] == 20
    with pytest.raises(ValueError):
        core.save_settings({"enabled": "true"})
    with pytest.raises(ValueError):
        core.save_settings({"base_url": "https://user:password@example.org"})


def test_evidence_normalizes_only_formatting():
    assert core.evidence_matches(
        "a verified sentence", "a  verified\nsentence with facts"
    )
    assert not core.evidence_matches("a fabricated sentence", "a verified sentence")
    assert not core.evidence_matches("fact", "fact")


@pytest.mark.parametrize("score", [True, -1, 11, float("nan"), float("inf"), "8"])
def test_reject_invalid_model_scores(model_result, score):
    model_result["score"] = score
    with pytest.raises(ValueError):
        validate_result(model_result)


def test_requires_structured_model_fields(model_result):
    assert validate_result(model_result)["score"] == 8
    del model_result["reason"]
    with pytest.raises(ValueError):
        validate_result(model_result)


def test_budget_counts_requests_and_survives_restart(db):
    cfg = {**core.DEFAULT_SETTINGS, "daily_articles": 1, "daily_tokens": 50000}
    first = core.reserve_budget(1, "original text", cfg)
    assert first is not None
    core.close_budget(first, 350)
    assert core.reserve_budget(2, "another original text", cfg) is None
    core.init_db()
    core.init_usage()
    with core.connect() as c:
        assert c.execute("SELECT actual FROM usage").fetchone()[0] == 350


def test_adapter_whitelist():
    assert is_our_social_feed("http://127.0.0.1:1200/telegram/channel/telegram")
    assert not is_our_social_feed("https://example.org/telegram/channel/telegram")
    assert not is_our_social_feed("http://127.0.0.1:1200/arbitrary/blog")
    assert not is_our_social_feed("http://127.0.0.1:8092/telegram/channel/telegram")


def test_failed_source_cannot_starve_other_sources(db, entry):
    import time

    items = [
        {
            **entry,
            "id": n,
            "url": f"https://example.org/{n}",
            "feed_id": 1 if n < 10 else n,
        }
        for n in range(1, 14)
    ]
    core.discover(items)
    core.update(
        1,
        state="fetch_error",
        attempts=1,
        next_try=time.time() + 600,
        error="temporary network error",
    )
    rows = core.next_batch(["pending", "fetch_error"], time.time(), 6)
    ids = [r["feed_id"] for r in rows]
    assert len(ids) == len(set(ids))
    assert ids[0] != 1
    assert set(ids) == {1, 10, 11, 12, 13}
