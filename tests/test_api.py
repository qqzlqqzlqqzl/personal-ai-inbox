import json
import httpx
import pytest, pytest_asyncio
import api, core


@pytest_asyncio.fixture
async def browser_api(db, monkeypatch, entry):
    monkeypatch.delenv("MINIFLUX_API_KEY", raising=False)

    def upstream(req):
        if req.url.path.endswith("/v1/me"):
            return (
                httpx.Response(200, json={"id": 1})
                if req.headers.get("x-auth-token") == "test-session"
                else httpx.Response(401)
            )
        if req.url.path.endswith("/entries/ids"):
            return httpx.Response(200, json={"entry_ids": [1], "total": 1})
        if req.url.path.endswith("/v1/feeds"):
            return httpx.Response(200, json=[entry["feed"]])
        if req.url.path.endswith("/entries/1"):
            return httpx.Response(200, json=entry)
        return httpx.Response(200, json={})

    async with api.lifespan(api.app):
        await api.app.state.client.aclose()
        api.app.state.client = httpx.AsyncClient(
            transport=httpx.MockTransport(upstream)
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
        ) as c:
            yield c


@pytest.mark.asyncio
async def test_unauthorized_settings_blocked(browser_api):
    assert (await browser_api.get("/mf/v1/ai/settings")).status_code == 401


@pytest.mark.asyncio
async def test_cross_site_write_blocked(browser_api):
    r = await browser_api.put(
        "/mf/v1/ai/settings",
        headers={"Origin": "https://evil.example"},
        json={"enabled": False},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_private_paths_not_served(browser_api):
    for path in ["/.private/ai.env", "/.git/config", "/missing.js"]:
        assert (await browser_api.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_invalid_tool_input_is_400(browser_api):
    h = {"X-Auth-Token": "test-session"}
    for body in [[{"name": "bad", "url": "javascript:alert(1)"}], ["bad"]]:
        assert (
            await browser_api.put("/mf/v1/ai/tools", headers=h, json=body)
        ).status_code == 400


@pytest.mark.asyncio
async def test_settings_dont_accept_secrets(browser_api):
    h = {"X-Auth-Token": "test-session"}
    r = await browser_api.put(
        "/mf/v1/ai/settings", headers=h, json={"api_key": "do-not-store"}
    )
    assert r.status_code == 400
    r = await browser_api.get("/mf/v1/ai/settings", headers=h)
    assert "api_key" not in r.json()


@pytest.mark.asyncio
async def test_ai_filter_score_and_date(browser_api, entry, model_result):
    core.discover([entry])
    core.update(
        1,
        state="done",
        score=8,
        technical_score=8,
        business_score=5,
        result=json.dumps(model_result),
    )
    h = {"X-Auth-Token": "test-session"}
    for query, expected in [
        ("ai_min=8", 1),
        ("ai_min=9", 0),
        ("published_after=2000000000", 0),
        ("published_before=1000000000", 0),
    ]:
        r = await browser_api.get(
            "/mf/v1/entries?ai_view=recommended&" + query, headers=h
        )
        assert r.status_code == 200
        assert r.json()["total"] == expected
    assert (
        await browser_api.get(
            "/mf/v1/entries?ai_view=recommended&published_after=bad", headers=h
        )
    ).status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["retry", "feedback"])
async def test_invalid_entry_identifier_is_400(browser_api, path):
    r = await browser_api.post(
        "/mf/v1/ai/" + path,
        headers={"X-Auth-Token": "test-session"},
        json={"entry_id": "not-an-id", "value": "useful"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query", ["ai_view=unknown", "ai_view=recommended&status=unknown"]
)
async def test_invalid_filter_is_400(browser_api, query):
    r = await browser_api.get(
        "/mf/v1/entries?" + query, headers={"X-Auth-Token": "test-session"}
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_metadata_only_sources_are_not_advertised_as_fulltext(
    browser_api, db, monkeypatch
):
    monkeypatch.setattr(api, "ROOT", db)
    (db / "sources.catalog.json").write_text(
        json.dumps(
            [
                {
                    "category": "论文与前沿",
                    "name": "arXiv",
                    "url": "https://arxiv.org/rss/cs.AI",
                    "status": "ok",
                },
                {
                    "category": "技术博客",
                    "name": "Example",
                    "url": "https://example.org/feed",
                    "status": "ok",
                },
            ]
        )
    )
    r = await browser_api.get(
        "/mf/v1/ai/catalog", headers={"X-Auth-Token": "test-session"}
    )
    assert r.status_code == 200
    assert [x["analysis_supported"] for x in r.json()] == [False, True]
