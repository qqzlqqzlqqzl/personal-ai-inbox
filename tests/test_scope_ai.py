import json
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio

import api
import core


@pytest_asyncio.fixture
async def scoped_reader(db, entry, model_result, monkeypatch):
    monkeypatch.delenv("MINIFLUX_API_KEY", raising=False)
    specs = {
        1: (1, 10, 9.0, "2026-09-28T01:00:00Z", "unread", False, "done"),
        2: (1, 10, 5.0, "2026-09-27T09:00:00Z", "read", False, "done"),
        3: (2, 10, 8.0, "2026-09-28T02:00:00Z", "unread", True, "done"),
        4: (3, 20, 7.0, "2026-09-28T03:00:00Z", "unread", False, "done"),
        5: (3, 20, None, "2026-09-28T04:00:00Z", "unread", False, "pending"),
        6: (2, 10, 6.0, "2026-09-28T00:30:00Z", "unread", False, "done"),
    }
    records = {}
    for eid, (feed_id, category_id, score, published, status, starred, state) in specs.items():
        raw = {
            **entry,
            "id": eid,
            "feed_id": feed_id,
            "title": f"Article {eid}",
            "url": f"https://example.org/{eid}",
            "published_at": published,
            "status": status,
            "starred": starred,
            "feed": {
                "id": feed_id,
                "title": f"Feed {feed_id}",
                "feed_url": f"https://example.org/feed/{feed_id}",
                "category": {"id": category_id, "title": f"Category {category_id}"},
            },
        }
        records[eid] = raw
        core.discover([raw])
        if state == "done":
            result = {**model_result, "summary": f"Summary {eid}", "tags": [f"tag{eid}"]}
            core.update(
                eid,
                state="done",
                score=score,
                technical_score=score,
                business_score=max(0, 10 - score),
                result=json.dumps(result),
            )

    # A real-reader entry with no analyses row exercises notes independently of AI.
    records[7] = {
        **entry,
        "id": 7,
        "feed_id": 2,
        "title": "Unanalyzed motor note",
        "url": "https://example.org/7",
        "published_at": "2026-09-28T02:30:00Z",
        "status": "unread",
        "starred": False,
        "feed": {
            "id": 2,
            "title": "Feed 2",
            "feed_url": "https://example.org/feed/2",
            "category": {"id": 10, "title": "Category 10"},
        },
    }

    feeds = [
        {"id": 1, "hide_globally": False, "category": {"id": 10, "title": "Category 10", "hide_globally": False}},
        {"id": 2, "hide_globally": False, "category": {"id": 10, "title": "Category 10", "hide_globally": False}},
        {"id": 3, "hide_globally": False, "category": {"id": 20, "title": "Category 20", "hide_globally": False}},
    ]
    unexpected_scoped_native = []

    def upstream(req):
        path = req.url.path
        if path.endswith("/v1/me"):
            return httpx.Response(200, json={"id": 1, "is_admin": True})
        if path.endswith("/entries/ids"):
            status = req.url.params.get("status")
            starred = req.url.params.get("starred")
            ids = [
                eid for eid, raw in records.items()
                if (not status or raw["status"] == status)
                and (starred is None or raw["starred"] == (starred == "true"))
            ]
            return httpx.Response(200, json={"entry_ids": ids, "total": len(ids)})
        if path.endswith("/v1/feeds"):
            return httpx.Response(200, json=feeds)
        if "/v1/feeds/" in path and path.endswith("/entries"):
            unexpected_scoped_native.append(path)
            return httpx.Response(599, json={"error": "AI route leaked to native feed endpoint"})
        if "/v1/categories/" in path and path.endswith("/entries"):
            unexpected_scoped_native.append(path)
            return httpx.Response(599, json={"error": "AI route leaked to native category endpoint"})
        if "/entries/" in path:
            return httpx.Response(200, json=records[int(path.rsplit("/", 1)[-1])])
        return httpx.Response(404)

    async with api.lifespan(api.app):
        await api.app.state.client.aclose()
        api.app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app),
            base_url="http://testserver",
            headers={"X-Auth-Token": "scope-test"},
        ) as client:
            yield client, records, unexpected_scoped_native


@pytest.mark.asyncio
async def test_ai_recommended_preserves_feed_and_category_scope(scoped_reader):
    client, _, leaked = scoped_reader
    feed = await client.get(
        "/mf/v1/feeds/1/entries",
        params={"ai_view": "recommended", "ai_min": 0, "limit": 20},
    )
    assert feed.status_code == 200
    assert [x["id"] for x in feed.json()["entries"]] == [1, 2]
    assert feed.json()["total"] == 2

    category = await client.get(
        "/mf/v1/categories/10/entries",
        params={"ai_view": "recommended", "ai_min": 0, "limit": 20},
    )
    assert category.status_code == 200
    assert [x["id"] for x in category.json()["entries"]] == [1, 3, 6, 2]
    assert category.json()["total"] == 4
    assert leaked == []


@pytest.mark.asyncio
async def test_ai_scope_composes_with_status_starred_and_date(scoped_reader):
    client, _, _ = scoped_reader
    unread = await client.get(
        "/mf/v1/feeds/1/entries",
        params={"ai_view": "recommended", "ai_min": 0, "status": "unread"},
    )
    assert [x["id"] for x in unread.json()["entries"]] == [1]

    starred = await client.get(
        "/mf/v1/categories/10/entries",
        params={"ai_view": "recommended", "ai_min": 0, "starred": "true"},
    )
    assert [x["id"] for x in starred.json()["entries"]] == [3]

    after = int(datetime(2026, 9, 28, 1, 30, tzinfo=timezone.utc).timestamp())
    recent = await client.get(
        "/mf/v1/categories/10/entries",
        params={"ai_view": "recommended", "ai_min": 0, "published_after": after},
    )
    assert [x["id"] for x in recent.json()["entries"]] == [3]


@pytest.mark.asyncio
async def test_pending_view_preserves_scope(scoped_reader):
    client, _, _ = scoped_reader
    category = await client.get(
        "/mf/v1/categories/20/entries",
        params={"ai_view": "pending", "limit": 20},
    )
    assert category.status_code == 200
    assert [x["id"] for x in category.json()["entries"]] == [5]
    feed = await client.get(
        "/mf/v1/feeds/1/entries",
        params={"ai_view": "pending", "limit": 20},
    )
    assert feed.json()["entries"] == []


@pytest.mark.asyncio
async def test_notes_filter_search_and_sort_are_real(scoped_reader):
    client, _, _ = scoped_reader
    with core.connect() as c:
        c.executemany(
            "INSERT INTO entry_notes(user_id,entry_id,note,created_at,updated_at) VALUES (1,?,?,?,?)",
            [
                (1, "cache idea", 10.0, 100.0),
                (3, "hardware note", 20.0, 300.0),
                (6, "motor controller", 30.0, 200.0),
                (7, "raw unanalyzed motor note", 40.0, 400.0),
            ],
        )

    notes = await client.get(
        "/mf/v1/categories/10/entries",
        params={"ai_view": "notes", "ai_sort": "note_updated", "direction": "desc", "limit": 20},
    )
    assert notes.status_code == 200
    assert [x["id"] for x in notes.json()["entries"]] == [7, 3, 6, 1]
    raw = next(x for x in notes.json()["entries"] if x["id"] == 7)
    assert raw["ai"]["state"] == "pending" and raw["ai"]["has_note"] is True

    notes_asc = await client.get(
        "/mf/v1/categories/10/entries",
        params={"ai_view": "notes", "ai_sort": "note_updated", "direction": "asc", "limit": 20},
    )
    assert [x["id"] for x in notes_asc.json()["entries"]] == [1, 6, 3, 7]

    search = await client.get(
        "/mf/v1/categories/10/entries",
        params={"ai_view": "notes", "search": "motor", "limit": 20},
    )
    assert [x["id"] for x in search.json()["entries"]] == [7, 6]

    recommended_noted = await client.get(
        "/mf/v1/categories/10/entries",
        params={"ai_view": "recommended", "has_note": "true", "ai_min": 0, "limit": 20},
    )
    assert {x["id"] for x in recommended_noted.json()["entries"]} == {1, 3, 6}


@pytest.mark.asyncio
async def test_note_sort_and_filter_validation(scoped_reader):
    client, _, _ = scoped_reader
    bad = await client.get(
        "/mf/v1/entries",
        params={"ai_view": "recommended", "has_note": "maybe"},
    )
    assert bad.status_code == 400
    bad_sort = await client.get(
        "/mf/v1/entries",
        params={"ai_view": "recommended", "ai_sort": "note_updated"},
    )
    assert bad_sort.status_code == 400
