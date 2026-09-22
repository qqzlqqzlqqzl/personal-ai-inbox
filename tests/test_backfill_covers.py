import json
import sys
import pytest
import core
import backfill_covers


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_ids", [[1], []])
async def test_retry_filter_precedes_limit_and_reports_mode(db, monkeypatch, entry, retry_ids):
    monkeypatch.setattr(core, "ROOT", db)
    # The newest missing-cover item must not consume the retry limit.
    core.discover([{**entry, "id": 1, "published_at": "2026-01-01T00:00:00Z"},
                   {**entry, "id": 2, "published_at": "2026-09-01T00:00:00Z"}])
    (db / "prior.json").write_text(json.dumps({"entries": [{"entry_id": i, "result": "failed"} for i in retry_ids]}))
    monkeypatch.setattr(sys, "argv", ["backfill_covers", "--retry-report", "prior.json", "--limit", "1", "--extracted-only", "--report", "result.json"])
    monkeypatch.setattr(backfill_covers, "read_env", lambda _: {"MINIFLUX_API_KEY": "test-only"})
    called = []
    async def one(client, sem, row, extracted_only=False):
        called.append(row["entry_id"])
        assert extracted_only is True
        return "no_cover"
    monkeypatch.setattr(backfill_covers, "one", one)
    await backfill_covers.main()
    assert called == retry_ids
    report = json.loads((db / "result.json").read_text())
    assert report["extracted_only"] is True
    assert report["requested"] == len(retry_ids)
    assert report["no_cover"] == len(retry_ids)
    assert report["failed"] == 0
    assert report["complete"] is True
