import httpx
import pytest
import content_input
import x_source


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy", [None, "http://127.0.0.1:17890"])
async def test_cover_external_proxy_and_direct_miniflux(monkeypatch, proxy):
    if proxy:
        monkeypatch.setenv("AI_NEWS_OUTBOUND_PROXY", proxy)
    else:
        monkeypatch.delenv("AI_NEWS_OUTBOUND_PROXY", raising=False)
    original = httpx.AsyncClient
    external_calls, internal_calls, proxies = [], [], []
    def external(req):
        external_calls.append(req)
        assert req.url.host == "example.org"
        assert "x-auth-token" not in req.headers
        assert "authorization" not in req.headers
        if req.url.path == "/article":
            return httpx.Response(200, text='<meta property="og:image" content="/cover.jpg">')
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=b"image")
    def factory(**kwargs):
        proxies.append(kwargs.pop("proxy", None))
        assert kwargs["trust_env"] is False
        return original(transport=httpx.MockTransport(external), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    assert await content_input.discover_original_cover("https://example.org/article", strict=True) == ("https://example.org/cover.jpg", "social_meta")
    def internal(req):
        internal_calls.append(req)
        assert req.url.host == "127.0.0.1"
        assert req.headers["x-auth-token"] == "private-test-token"
        return httpx.Response(200, json={"content": "saved body"})
    async with original(transport=httpx.MockTransport(internal), trust_env=False) as client:
        result = await content_input.add_original_cover(client, {"id": 1, "url": "https://example.org/article"}, "body", "http://127.0.0.1:8091/mf", {"X-Auth-Token": "private-test-token"})
    assert result == "saved body"
    assert proxies == [proxy, proxy]
    assert [r.method for r in internal_calls] == ["PUT", "GET"]
    assert len(external_calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy", [None, "http://127.0.0.1:17890"])
async def test_x_network_proxy_does_not_proxy_local_rsshub(monkeypatch, proxy):
    if proxy:
        monkeypatch.setenv("AI_NEWS_OUTBOUND_PROXY", proxy)
    else:
        monkeypatch.delenv("AI_NEWS_OUTBOUND_PROXY", raising=False)
    monkeypatch.setattr(x_source, "read_env", lambda _: {})
    original = httpx.AsyncClient
    calls = []
    def factory(**kwargs):
        selected = kwargs.pop("proxy", None)
        assert kwargs["trust_env"] is False
        def respond(req):
            calls.append((req.url.host, selected))
            if req.url.host == "x.com":
                assert selected == proxy
                return httpx.Response(200, text="X page")
            assert req.url.host == "127.0.0.1"
            assert selected is None
            return httpx.Response(503)
        return original(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    result = await x_source.probe("OpenAI")
    assert result["network_reachable"] is True
    assert result["posts_returned"] is False
    assert result["route_http"] == 503
    assert sorted(calls) == sorted([("x.com", proxy), ("127.0.0.1", None)])
