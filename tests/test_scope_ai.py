import json
import copy
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio

import api
import core


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['unchanged', 'insert', 'rescore'])
async def test_ai_result_revision_restarts_changed_order_without_extra_scan(scoped_reader, monkeypatch, mutation):
    client, records, _ = scoped_reader
    for eid in range(1, 53):
        if eid not in records:
            records[eid] = {**copy.deepcopy(records[1]), 'id':eid, 'title':f'Unique {eid}',
                            'url':f'https://example.org/{eid}'}
        core.discover([records[eid]])
        core.update(eid, state='done', score=10-eid/100, technical_score=8, business_score=5)
    scans, bodies = [], []
    original_rows, original_get = api.reader_rows, api.app.state.client.get
    def rows(sql, values):
        if ' AS ai_order_value FROM analyses WHERE ' in sql: scans.append(sql)
        return original_rows(sql, values)
    async def get(url, **kwargs):
        if url.rsplit('/',1)[-1].isdigit() and '/entries/' in url: bodies.append(url)
        return await original_get(url, **kwargs)
    monkeypatch.setattr(api, 'reader_rows', rows)
    monkeypatch.setattr(api.app.state.client, 'get', get)
    async def page(offset=0, revision=None):
        params = dict(ai_view='recommended', ai_min=8, ai_sort='score', direction='desc', limit=24, offset=offset)
        if revision is not None: params['ai_revision'] = revision
        before = len(scans)
        response = await client.get('/mf/v1/entries', params=params)
        assert response.status_code == 200, response.text
        assert len(scans) == before + 1, 'one existing candidate query per request'
        return response.json()
    legacy = await page()
    assert set(legacy) == {'total','entries'}, 'old clients retain the original contract'
    first = await page(revision='initial')
    assert [entry['id'] for entry in first['entries']] == list(range(1,25))
    revision = first['ai_revision']
    assert len(revision) == 64
    if mutation == 'insert':
        records[99] = {**copy.deepcopy(records[1]), 'id':99, 'title':'New 99','url':'https://example.org/99'}
        core.discover([records[99]])
        core.update(99, state='done', score=10, technical_score=8, business_score=5)
    elif mutation == 'rescore':
        core.update(50, score=10)
    before = len(bodies)
    next_page = await page(24, revision)
    if mutation == 'unchanged':
        assert next_page['ai_revision'] == revision
        assert [entry['id'] for entry in next_page['entries']] == list(range(25,49))
    else:
        assert next_page['ai_result_changed'] is True and next_page['entries'] == []
        assert next_page['ai_revision'] != revision
        assert len(bodies) == before, 'changed result must not hydrate a discarded page'
    fresh = await page(revision='initial')
    actual = [entry['id'] for entry in fresh['entries']]
    response = fresh
    while len(actual) < response['total']:
        response = await page(len(actual), fresh['ai_revision'])
        actual.extend(entry['id'] for entry in response['entries'])
    expected = ([99] + list(range(1,53)) if mutation == 'insert' else
                [50] + [eid for eid in range(1,53) if eid != 50] if mutation == 'rescore' else list(range(1,53)))
    assert actual == expected and len(actual) == len(set(actual)) == response['total']
    print(json.dumps({'case':mutation,'initial_ids':[e['id'] for e in first['entries']],
                      'continuation_ids':[e['id'] for e in next_page['entries']],
                      'changed':next_page.get('ai_result_changed',False),
                      'refreshed_ids':actual,'total':response['total']}))


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
        {"id": 1, "user_id": 1, "hide_globally": False, "category": {"id": 10, "user_id": 1, "title": "Category 10", "hide_globally": False}},
        {"id": 2, "user_id": 1, "hide_globally": False, "category": {"id": 10, "user_id": 1, "title": "Category 10", "hide_globally": False}},
        {"id": 3, "user_id": 1, "hide_globally": False, "category": {"id": 20, "user_id": 1, "title": "Category 20", "hide_globally": False}},
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
            offset, limit = int(req.url.params.get("offset", 0)), int(req.url.params.get("limit", 10000))
            return httpx.Response(200, json={"entry_ids": ids[offset:offset+limit], "total": len(ids)})
        if path.endswith("/v1/feeds"):
            return httpx.Response(200, json=feeds)
        if path.endswith("/v1/categories"):
            return httpx.Response(200, json=[feeds[0]["category"], feeds[2]["category"],
                {"id": 30, "user_id": 1, "hide_globally": False}])
        if path.endswith("/entries/metadata"):
            ids = json.loads(req.content)["entry_ids"]
            return httpx.Response(200, json={"entries": [
                {**{key: records[eid].get(key) for key in api.notes_metadata.FIELDS},
                 "changed_at": records[eid].get("changed_at", records[eid]["published_at"])}
                for eid in ids if eid in records]}, headers={"X-Reader-Entry-Metadata": "1"})
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


@pytest.mark.asyncio
async def test_ai_sidebar_counts_are_complete_on_first_request_without_bodies(scoped_reader, monkeypatch):
    client, _, _ = scoped_reader
    calls = []
    original = api.app.state.client.request
    async def request(method, url, **kwargs):
        calls.append((method, str(url)))
        return await original(method, url, **kwargs)
    monkeypatch.setattr(api.app.state.client, "request", request)
    def forbidden(*args, **kwargs):
        raise AssertionError("counts must not hydrate or enqueue article bodies")
    monkeypatch.setattr(api, "enrich_reader_entries", forbidden)
    response = await client.get("/mf/v1/ai/scope-counts", params={
        "ai_view": "recommended", "ai_min": 0, "today_after": 1790553600, "status": "unread"})
    assert response.status_code == 200, response.text
    assert response.json() == {"scope_counts": {"all": 4, "today": 4, "starred": 1, "history": 1,
        "category": {"10": 3, "20": 1, "30": 0}, "feed": {"1": 1, "2": 2, "3": 1}}}
    assert sum(url.endswith("/entries/metadata") for _, url in calls) == 1
    assert not any("/entries/" in url and url.rsplit("/", 1)[-1].isdigit() for _, url in calls)
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_ai_sidebar_pending_notes_and_starred_lenses(scoped_reader):
    client, _, _ = scoped_reader
    params = {"today_after": 1790553600, "ai_min": 0}
    response = await client.get("/mf/v1/ai/scope-counts", params={**params, "ai_view": "pending"})
    assert response.json()["scope_counts"] == {"all": 1, "today": 1, "starred": 0, "history": 0,
        "category": {"10": 0, "20": 1, "30": 0}, "feed": {"1": 0, "2": 0, "3": 1}}
    with core.connect() as c:
        c.executemany("INSERT INTO entry_notes(user_id,entry_id,note,created_at,updated_at) VALUES (?,?,?,?,?)",
                      [(1, 7, "motor note", 1, 2), (2, 1, "other user's motor note", 1, 2)])
    response = await client.get("/mf/v1/ai/scope-counts", params={**params, "ai_view": "notes", "search": "motor"})
    assert response.json()["scope_counts"]["all"] == 1
    assert response.json()["scope_counts"]["feed"] == {"1": 0, "2": 1, "3": 0}
    response = await client.get("/mf/v1/ai/scope-counts", params={**params, "ai_view": "recommended", "starred": "true"})
    assert response.json()["scope_counts"]["all"] == response.json()["scope_counts"]["starred"] == 1
    assert response.json()["scope_counts"]["history"] == 0


@pytest.mark.asyncio
async def test_ai_sidebar_quality_exclusions_and_current_url_binding(scoped_reader):
    from content_quality import bind, POLICY
    client, records, _ = scoped_reader
    source = "assessed synthetic paid source"
    quality = bind({"policy_version": POLICY, "recommendation_eligible": False,
                    "reason_codes": ["paid_subscribers"], "access": "paid_subscription", "information": "unknown"},
                   entry_id=3, user_id=1, url=records[3]["url"], content_hash="bound", source_text=source)
    core.update(3, source_text=source, content_hash="bound", content_quality=json.dumps(quality))
    params = {"today_after": 1790553600, "ai_view": "recommended", "ai_min": 0}
    response = await client.get("/mf/v1/ai/scope-counts", params=params)
    assert response.status_code == 200, response.text
    assert response.json()["scope_counts"]["all"] == 4
    assert response.json()["scope_counts"]["starred"] == 0
    records[3]["url"] = "https://example.org/changed-source"
    response = await client.get("/mf/v1/ai/scope-counts", params=params)
    assert response.json()["scope_counts"]["all"] == 5, "stale quality cannot bind a changed source"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["capability", "owner", "body", "incomplete-ids"])
async def test_ai_sidebar_incomplete_snapshot_never_returns_fake_zero(scoped_reader, monkeypatch, failure):
    client, _, _ = scoped_reader
    original_get, original_post = api.app.state.client.get, api.app.state.client.post
    async def get(url, **kwargs):
        response = await original_get(url, **kwargs)
        if failure == "incomplete-ids" and url.endswith("/entries/ids"):
            return httpx.Response(200, json={"entry_ids": [], "total": 1}, request=response.request)
        return response
    async def post(url, **kwargs):
        response = await original_post(url, **kwargs)
        payload = response.json()
        if failure == "owner": payload["entries"][0]["user_id"] = 2
        if failure == "body": payload["entries"][0]["content"] = "unexpected body"
        return httpx.Response(200, json=payload, request=response.request,
            headers={} if failure == "capability" else response.headers)
    monkeypatch.setattr(api.app.state.client, "get", get)
    monkeypatch.setattr(api.app.state.client, "post", post)
    response = await client.get("/mf/v1/ai/scope-counts", params={
        "ai_view": "recommended", "ai_min": 0, "today_after": 1790553600})
    assert response.status_code == 503, response.text
    assert "scope_counts" not in response.json()


@pytest.mark.asyncio
async def test_ai_sidebar_auth_and_invalid_filters_do_not_return_counts(scoped_reader):
    client, _, _ = scoped_reader
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://testserver") as anonymous:
        response = await anonymous.get("/mf/v1/ai/scope-counts", params={
            "ai_view": "recommended", "today_after": 1790553600})
        assert response.status_code == 401
    for query in ({"ai_view": "all", "today_after": 1790553600},
                  {"ai_view": "recommended"},
                  {"ai_view": "recommended", "today_after": 1790553600, "ai_min": "nan"}):
        response = await client.get("/mf/v1/ai/scope-counts", params=query)
        assert response.status_code == 400, response.text
        assert "scope_counts" not in response.json()
