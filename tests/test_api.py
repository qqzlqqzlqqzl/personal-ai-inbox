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
    entry["content"] = (
        "<p>" + ("Full article payload for performance testing. " * 180)
        + '</p><img src="https://example.org/cover.jpg">'
    )
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
    compact = await browser_api.get(
        "/mf/v1/entries?ai_view=recommended&ai_min=8",
        headers={**h, "Accept-Encoding": "gzip"},
    )
    item = compact.json()["entries"][0]
    assert item["content"] == ""
    assert item["content_deferred"] is True
    assert item["ai"]["cover_url"] == "https://example.org/cover.jpg"
    assert compact.headers.get("content-encoding") == "gzip"
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


@pytest.mark.asyncio
async def test_cache_reset_is_outside_pwa_scope(browser_api):
    r = await browser_api.get("/mf/cache-reset")
    assert r.status_code == 200
    assert "no-store" in r.headers["cache-control"]
    assert "serviceWorker.getRegistrations" in r.text
    assert 'location.replace("/inbox/?updated=1")' in r.text


@pytest.mark.asyncio
async def test_frontend_is_scoped(browser_api, tmp_path, monkeypatch):
    monkeypatch.setattr(api, "ROOT", tmp_path)
    web = tmp_path / "upstream/reactflux/build"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("scoped frontend")
    (web / "assets/new.js").write_text("javascript")
    (web / "assets/big.js").write_text("x" * 4000)
    (web / "sw.js").write_text("service worker")
    (web / "manifest.webmanifest").write_text('{"name":"个人信息箱"}')
    for path in ["/", "/inbox"]:
        r = await browser_api.get(path)
        assert r.status_code == 308 and r.headers["location"] == "/inbox/"
    for path in ["/inbox/", "/inbox/login", "/inbox/all/entry/1"]:
        r = await browser_api.get(path)
        assert r.status_code == 200 and r.text == "scoped frontend"
        assert (await browser_api.head(path)).status_code == 200
    assert (await browser_api.get("/inbox/assets/new.js")).text == "javascript"
    compressed = await browser_api.get(
        "/inbox/assets/big.js", headers={"Accept-Encoding": "gzip"}
    )
    assert compressed.status_code == 200
    assert compressed.headers.get("content-encoding") == "gzip"
    for path in ["/inbox/sw.js", "/inbox/manifest.webmanifest"]:
        r = await browser_api.get(path)
        assert r.status_code == 200
        assert "no-store" in r.headers["cache-control"]
    for path in ["/login", "/all", "/sw.js", "/news/", "/unknown", "/assets/new.js",
                 "/inbox/.private/ai.env", "/inbox/.git/config", "/inbox/assets/missing.js",
                 "/inbox/%2e%2e/src/api.py"]:
        assert (await browser_api.get(path)).status_code == 404
    assert (await browser_api.get("/mf/v1/ai/settings")).status_code == 401
    assert (await browser_api.get("/healthz")).status_code == 200


@pytest.mark.asyncio
async def test_x_subscription_requires_real_posts(browser_api, monkeypatch):
    import x_source
    async def blocked(_):
        return {"posts_returned": False, "message": "adapter unconfigured"}
    monkeypatch.setattr(x_source, "probe", blocked)
    h={"X-Auth-Token":"test-session"}
    r=await browser_api.post("/mf/v1/ai/subscribe",json={"x_handle":"@OpenAI","category_id":1},headers=h)
    assert r.status_code == 409
    assert not r.json()["probe"]["posts_returned"]


def test_x_handle_refuses_urls_and_path_injection():
    from x_source import handle
    assert handle(" @OpenAI ")=="OpenAI"
    for value in ("", "../foo", "https://x.com/OpenAI", "x?foo=bar"):
        with pytest.raises(ValueError): handle(value)


@pytest.mark.asyncio
@pytest.mark.parametrize("worker_uid", [1, 2, None])
async def test_basic_list_reuses_only_same_user_token(browser_api, monkeypatch, entry, model_result, worker_uid):
    monkeypatch.setenv("MINIFLUX_API_KEY", "worker-test-token")
    core.discover([entry])
    core.update(1, state="done", score=8, result=json.dumps(model_result))
    calls = []
    basic = "Basic dXNlcjpwYXNz"

    def upstream(req):
        calls.append(req)
        if req.url.path.endswith("/v1/me"):
            if req.headers.get("authorization") == basic:
                return httpx.Response(200, json={"id": 1})
            if worker_uid is None:
                return httpx.Response(401)
            return httpx.Response(200, json={"id": worker_uid})
        if req.url.path.endswith("/entries/ids"):
            return httpx.Response(200, json={"entry_ids": [1], "total": 1})
        if req.url.path.endswith("/v1/feeds"):
            return httpx.Response(200, json=[entry["feed"]])
        if req.url.path.endswith("/entries/1"):
            return httpx.Response(200, json=entry)
        raise AssertionError("Unexpected upstream path")

    await api.app.state.client.aclose()
    api.app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    response = await browser_api.get("/mf/v1/entries?ai_view=recommended", headers={"Authorization": basic})
    assert response.status_code == 200
    assert response.json()["entries"][0]["id"] == 1
    assert calls[0].url.path.endswith("/v1/me")
    assert calls[0].headers.get("authorization") == basic
    data_calls = [r for r in calls if not r.url.path.endswith("/v1/me")]
    assert len(data_calls) == 4
    for request in data_calls:
        if worker_uid == 1:
            assert request.headers.get("x-auth-token") == "worker-test-token"
            assert "authorization" not in request.headers
        else:
            assert request.headers.get("authorization") == basic
            assert "x-auth-token" not in request.headers


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic aW52YWxpZA=="}])
async def test_unauthorized_list_never_uses_worker_token(browser_api, monkeypatch, headers):
    monkeypatch.setenv("MINIFLUX_API_KEY", "worker-test-token")
    calls = []

    def upstream(req):
        calls.append(req)
        return httpx.Response(401)

    await api.app.state.client.aclose()
    api.app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    response = await browser_api.get("/mf/v1/entries?ai_view=recommended", headers=headers)
    assert response.status_code == 401
    assert all(r.url.path.endswith("/v1/me") and "x-auth-token" not in r.headers for r in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding, compressed", [("gzip", True), ("gzip;q=0, identity", False), ("identity", False)])
async def test_gzip_negotiation_static_and_dynamic(browser_api, monkeypatch, db, encoding, compressed):
    import gzip
    monkeypatch.setattr(api, "ROOT", db)
    assets = db / "upstream/reactflux/build/assets"
    assets.mkdir(parents=True)
    payload = b"const greeting = 'hello';" * 100
    (assets / "test.js").write_bytes(payload)
    (assets / "test.js.gz").write_bytes(gzip.compress(payload))
    r = await browser_api.get("/inbox/assets/test.js", headers={"Accept-Encoding": encoding})
    assert r.status_code == 200
    assert r.content == payload
    assert (r.headers.get("content-encoding") == "gzip") is compressed
    r = await browser_api.get("/mf/v1/ai/settings", headers={"Accept-Encoding": encoding, "X-Auth-Token": "test-session"})
    assert r.status_code == 200
    assert len(r.content) > 512
    assert (r.headers.get("content-encoding") == "gzip") is compressed
