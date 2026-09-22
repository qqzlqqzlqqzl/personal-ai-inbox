import asyncio
import sys
import time
from types import SimpleNamespace

import httpx
import pytest
import core
import preview_worker


def row(entry_id):
    with core.connect() as db:
        return dict(db.execute("SELECT * FROM analyses WHERE entry_id=?", (entry_id,)).fetchone())


@pytest.mark.asyncio
async def test_preview_works_with_ai_disabled_without_model(db, entry, monkeypatch):
    core.discover([entry])
    core.save_settings({"enabled": False, "daily_articles": 1})
    core.update(entry["id"], state="budget_paused", next_try=time.time() + 1000)
    before = row(entry["id"])
    monkeypatch.setenv("MINIFLUX_API_KEY", "test-only")
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    async def cover(*args, **kwargs):
        return "https://example.org/photo.jpg", "page_hero"
    monkeypatch.setattr(preview_worker, "discover_original_cover", cover)
    requests = []
    def upstream(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "127.0.0.1"
        return httpx.Response(200, json=entry)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        report = await preview_worker.run_once(client)
        assert (await preview_worker.run_once(client))["processed"] == 0
    after = row(entry["id"])
    assert report["updated"] == 1 and report["ai_requests"] == 0
    assert len(requests) == 1
    assert after["cover_url"].endswith("photo.jpg")
    assert after["preview_checked_at"]
    assert (after["state"], after["next_try"], after["attempts"]) == (before["state"], before["next_try"], before["attempts"])
    assert core.decorate(entry, entry["user_id"])["ai"]["preview_error"] is None


@pytest.mark.asyncio
async def test_preview_failure_cooldown_does_not_change_ai_state(db, entry, monkeypatch):
    core.discover([entry])
    core.update(entry["id"], state="ai_error", attempts=3, next_try=1234)
    async def fail(*args, **kwargs):
        raise RuntimeError("secret URL must never be persisted")
    monkeypatch.setattr(preview_worker, "discover_original_cover", fail)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=entry))) as client:
        report = await preview_worker.run_once(client)
    saved = row(entry["id"])
    assert report["failed"] == 1
    assert saved["preview_error"] == "RuntimeError"
    assert (saved["state"], saved["attempts"], saved["next_try"]) == ("ai_error", 3, 1234)
    assert preview_worker.pending_previews(saved["preview_checked_at"] + 21599) == []
    assert len(preview_worker.pending_previews(saved["preview_checked_at"] + 21600)) == 1


@pytest.mark.asyncio
async def test_product_with_existing_cover_gets_source_once(db, entry, monkeypatch):
    entry = {**entry, "url": "https://www.producthunt.com/products/example"}
    core.discover([entry])
    core.update(entry["id"], cover_url="https://example.org/old.jpg", state="pending")
    calls = []
    async def enrich(client, received, backend, headers):
        calls.append(received["id"])
        return {"content": "product description", "cover_url": "https://example.org/new.jpg", "cover_source": "product_page", "content_source": "product_page", "updated": True}
    monkeypatch.setitem(sys.modules, "product_source", SimpleNamespace(enrich_product_entry=enrich))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=entry))) as client:
        assert (await preview_worker.run_once(client))["updated"] == 1
        assert (await preview_worker.run_once(client))["processed"] == 0
    assert calls == [1]
    saved = row(1)
    assert saved["state"] == "pending"
    assert saved["content_source"] == "product_page"


@pytest.mark.asyncio
async def test_background_loop_runs_when_ai_disabled(db, monkeypatch):
    core.save_settings({"enabled": False})
    monkeypatch.setenv("MINIFLUX_API_KEY", "test-only")
    called = []
    async def once(client):
        called.append(True)
        raise asyncio.CancelledError()
    monkeypatch.setattr(preview_worker, "run_once", once)
    with pytest.raises(asyncio.CancelledError):
        await preview_worker.run_preview_worker()
    assert called == [True]


def test_batch_cap_and_feed_scope(db, entry):
    core.discover([{**entry, "id": i} for i in range(1, 21)])
    assert len(preview_worker.pending_previews(time.time(), limit=100)) == 12
    assert preview_worker.pending_previews(time.time(), feed_id=999) == []
