"""History queries use mocked feeds and temporary state, never live services."""
import httpx
import pytest
import pytest_asyncio

import api
import feed_history as history


@pytest.fixture(autouse=True)
def empty_snapshot_cache(monkeypatch):
    async def public_dns(host, port):
        return ["93.184.216.34"]
    monkeypatch.setattr(history, "resolved_addresses", public_dns)
    history._snapshot_cache.clear()
    yield
    history._snapshot_cache.clear()


def test_rss_window_is_dates_not_feed_order_and_not_archive_depth():
    data = b'''<rss><channel>
      <item><pubDate>Tue, 22 Sep 2026 11:00:00 +0800</pubDate></item>
      <item><pubDate>Mon, 21 Sep 2026 03:00:00 GMT</pubDate></item>
      <item><pubDate>invalid</pubDate></item><item/>
    </channel></rss>'''
    result = history.feed_window(data)
    assert result == {
        "state": "ok", "count": 4, "dated_count": 2, "undated_count": 2,
        "updated_fallback_count": 0, "oldest_at": "2026-09-21T03:00:00Z",
        "newest_at": "2026-09-22T03:00:00Z", "span_days": 1.0,
    }


def test_atom_published_wins_and_updated_fallback_is_explicit():
    result = history.feed_window(b'''<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><published>2026-01-01T00:00:00Z</published><updated>2026-09-22T00:00:00Z</updated></entry>
      <entry><updated>2026-01-02T00:00:00+00:00</updated></entry>
      <entry><published>2026-01-03</published></entry>
    </feed>''')
    assert result["count"] == 3
    assert result["oldest_at"] == "2026-01-01T00:00:00Z"
    assert result["newest_at"] == "2026-01-02T00:00:00Z"
    assert result["updated_fallback_count"] == 1
    assert result["undated_count"] == 1


def test_rdf_date_and_nested_non_entries_not_counted():
    result = history.feed_window(b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/">
    <channel/><item><dc:date>2026-01-01T00:00:00Z</dc:date></item></rdf:RDF>''')
    assert result["count"] == 1
    assert result["span_days"] == 0


@pytest.mark.parametrize("data", [b'<?xml version="1.0" encoding="fake"?><rss><channel/></rss>', b"<html/>", b"<rss>", b"<rss/>", b"<rss xmlns='urn:fake'><channel/></rss>", b"", b"<json/>",
    b'<!DOCTYPE rss [<!ENTITY x "a">]><rss><channel/></rss>',
    '<!DOCTYPE rss><rss><channel/></rss>'.encode('utf-16')])
def test_invalid_and_unsafe_feed_is_not_reported_empty(data):
    with pytest.raises(ValueError):
        history.feed_window(data)


def test_empty_and_undated_are_distinct():
    empty = history.feed_window(b"<rss><channel/></rss>")
    undated = history.feed_window(b"<rss><channel><item/></channel></rss>")
    assert empty["count"] == 0 and empty["span_days"] is None
    assert undated["count"] == 1 and undated["span_days"] is None
    assert history.timestamp("2026-01-01T00:00:00") is None
    assert history.timestamp("junk") is None


def test_feed_size_bound():
    with pytest.raises(ValueError, match="feed_too_large"):
        history.feed_window(b"x" * (history.MAX_FEED_BYTES + 1))


@pytest.mark.asyncio
async def test_stored_bounds_are_from_miniflux_with_all_retained_statuses():
    calls = []
    def upstream(request):
        calls.append(request)
        assert request.headers["x-auth-token"] == "session"
        assert request.url.params.get_list("status") == ["read", "unread", "removed"]
        assert request.url.params["order"] == "published_at"
        assert request.url.params["limit"] == "1"
        date = "2020-01-01T00:00:00Z" if request.url.params["direction"] == "asc" else "2026-09-22T00:00:00Z"
        return httpx.Response(200, json={"total": 8123, "entries": [{"published_at": date}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result = await history.stored_history(client, "http://reader", {"X-Auth-Token": "session"}, 12)
    assert result["count"] == 8123
    assert result["oldest_published_at"] == "2020-01-01T00:00:00Z"
    assert result["newest_published_at"] == "2026-09-22T00:00:00Z"
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,expected", [({"total": 0, "entries": []}, "ok"),
    ({"total": 2, "entries": []}, "unavailable"), ({}, "unavailable"),
    ({"total": True, "entries": []}, "unavailable"),
    ({"total": 1, "entries": [{"published_at": "bad"}]}, "ok")])
async def test_stored_empty_or_invalid(payload, expected):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=payload))) as client:
        result = await history.stored_history(client, "http://reader", {}, 1)
    assert result["state"] == expected
    if expected == "ok":
        assert result["oldest_published_at"] is None


@pytest.mark.asyncio
async def test_stored_concurrent_changes_are_not_reported_as_consistent():
    calls = []
    def upstream(req):
        calls.append(req)
        total = 1 if req.url.params["direction"] == "asc" else 2
        return httpx.Response(200, json={"total": total, "entries": [{"published_at": "2026-01-01T00:00:00Z"}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result = await history.stored_history(client, "http://reader", {}, 1)
    assert result == {"state": "changing", "count": None}
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_probe_is_unauthenticated_cached_and_user_scoped(monkeypatch):
    calls = []
    def remote(req):
        calls.append(req)
        assert "authorization" not in req.headers and "x-auth-token" not in req.headers
        assert "cookie" not in req.headers
        return httpx.Response(200, content=b"<rss><channel><item/></channel></rss>")
    monkeypatch.setattr(history, "feed_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(remote)))
    feed = {"id": 3, "feed_url": "https://example.org/feed"}
    first = await history.probe_feed(feed, 1)
    second = await history.probe_feed(feed, 1)
    assert first["count"] == 1 and first["cached"] is False
    assert second["cached"] is True and second["checked_at"] == first["checked_at"]
    await history.probe_feed(feed, 2)
    await history.probe_feed({**feed, "feed_url": "https://example.org/new"}, 1)
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{"username": "reader"}, {"password": "secret"}, {"cookie": "secret"},
    {"feed_url": "https://reader:secret@example.org/feed"}, {"feed_url": "file:///etc/passwd"},
    {"feed_url": "http://169.254.169.254/metadata"}, {"feed_url": "http://127.0.0.1:8091/mf/v1/feeds"}])
async def test_probe_does_not_use_credentials_or_other_local_services(fields, monkeypatch):
    def forbidden():
        raise AssertionError("network must not be touched")
    monkeypatch.setattr(history, "feed_client", forbidden)
    result = await history.probe_feed({"id": 3, "feed_url": "https://example.org/feed", **fields}, 1)
    assert result["reason"] == "unsupported_or_authenticated_feed"


@pytest.mark.asyncio
@pytest.mark.parametrize("status,content,state", [(503, b"", "unavailable"), (200, b"<html/>", "unavailable"),
    (200, b"<rss><channel/></rss>", "ok"), (200, b"x" * (history.MAX_FEED_BYTES + 1), "unavailable")])
async def test_probe_failure_is_distinct_from_empty(status, content, state, monkeypatch):
    monkeypatch.setattr(history, "feed_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(status, content=content))))
    result = await history.probe_feed({"id": 3, "feed_url": "https://example.org/feed"}, 1)
    assert result["state"] == state
    if state == "unavailable": assert "count" not in result


@pytest.mark.asyncio
async def test_redirects_are_bounded_and_cannot_enter_local_adapter(monkeypatch):
    calls = []
    def remote(req):
        calls.append(req)
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:1200/internal"})
    monkeypatch.setattr(history, "feed_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(remote)))
    result = await history.probe_feed({"id": 3, "feed_url": "https://example.org/feed"}, 1)
    assert result["reason"] == "unsupported_redirect"
    assert len(calls) == 1


@pytest_asyncio.fixture
async def history_api(monkeypatch, tmp_path):
    """No lifespan/background tasks, no environment keys, no real HTTP."""
    feed = {"id": 7, "user_id": 1, "title": "Manual", "feed_url": "https://example.org/manual",
            "category": {"title": "手动来源"}}
    calls = []
    def upstream(req):
        calls.append(req)
        if req.url.path.endswith("/v1/me"):
            return httpx.Response(200, json={"id": 1, "is_admin": req.headers.get("x-auth-token") != "non-admin"})
        if req.url.path.endswith("/v1/feeds"):
            return httpx.Response(200, json=[feed])
        if req.url.path.endswith("/v1/feeds/7"):
            return httpx.Response(200, json=feed)
        if req.url.path.endswith("/v1/feeds/7/entries"):
            return httpx.Response(200, json={"total": 19, "entries": [{"published_at": "2026-01-01T00:00:00Z"}]})
        return httpx.Response(404)
    monkeypatch.setattr(api, "ROOT", tmp_path)
    monkeypatch.setattr(history, "feed_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"<rss><channel><item/></channel></rss>"))))
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as upstream_client:
        monkeypatch.setattr(api.app.state, "client", upstream_client, raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://testserver") as client:
            yield client, feed, calls


@pytest.mark.asyncio
async def test_catalog_includes_manual_sources_without_probe(history_api):
    client, _feed, calls = history_api
    response = await client.get("/mf/v1/ai/catalog", headers={"X-Auth-Token": "session"})
    assert response.status_code == 200
    assert response.json()[0]["feed_id"] == 7
    assert response.json()[0]["subscribed"] is True
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_history_requires_admin_and_access_to_feed(history_api):
    client, feed, calls = history_api
    assert (await client.get("/mf/v1/ai/feeds/7/history")).status_code == 401
    assert (await client.get("/mf/v1/ai/feeds/7/history", headers={"X-Auth-Token": "non-admin"})).status_code == 403
    assert (await client.get("/mf/v1/ai/feeds/99/history", headers={"X-Auth-Token": "session"})).status_code == 404
    feed["user_id"] = 2
    assert (await client.get("/mf/v1/ai/feeds/7/history", headers={"X-Auth-Token": "session"})).status_code == 404
    assert not any(req.url.path.endswith("/entries") for req in calls)


@pytest.mark.asyncio
async def test_api_stored_and_feed_window_are_separate(history_api):
    client, _feed, calls = history_api
    response = await client.get("/mf/v1/ai/feeds/7/history", headers={"X-Auth-Token": "session"})
    assert response.status_code == 200
    result = response.json()
    assert result["stored"]["count"] == 19
    assert result["feed_window"]["count"] == 1
    assert result["archive_complete"] is False
    assert all(req.method == "GET" for req in calls)
    assert "content" not in response.text and "session" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1"])
async def test_dns_private_destinations_are_rejected(address, monkeypatch):
    async def dns(host, port):
        return [address]
    monkeypatch.setattr(history, "resolved_addresses", dns)
    with pytest.raises(ValueError, match="unsafe_feed_address"):
        await history.pinned_destination("https://example.org/feed")


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["2130706433", "127.1", "0177.0.0.1", "0x7f000001", "private.example"])
async def test_redirect_aliases_cannot_reach_local_services(host, monkeypatch):
    calls = []
    async def dns(hostname, port):
        return ["127.0.0.1"] if hostname == host else ["93.184.216.34"]
    def remote(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": f"http://{host}:8091/internal"})
    monkeypatch.setattr(history, "resolved_addresses", dns)
    monkeypatch.setattr(history, "feed_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(remote)))
    result = await history.probe_feed({"id": 3, "feed_url": "https://example.org/feed"}, 1)
    assert result["reason"] in ("unsafe_feed_address", "fetch_failed")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_public_connection_is_pinned_with_original_host_and_sni(monkeypatch):
    def remote(req):
        assert req.url.host == "93.184.216.34"
        assert req.headers["host"] == "example.org"
        assert req.extensions["sni_hostname"] == "example.org"
        return httpx.Response(200, content=b"<rss><channel/></rss>")
    monkeypatch.setattr(history, "feed_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(remote)))
    result = await history.probe_feed({"id": 3, "feed_url": "https://example.org/feed"}, 1)
    assert result["state"] == "ok"


@pytest.mark.asyncio
async def test_unsupported_encoding_preserves_stored_history(history_api, monkeypatch):
    client, _feed, _calls = history_api
    monkeypatch.setattr(history, "feed_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=b'<?xml version="1.0" encoding="fake"?><rss><channel/></rss>'))))
    response = await client.get("/mf/v1/ai/feeds/7/history", headers={"X-Auth-Token": "session"})
    assert response.status_code == 200
    assert response.json()["stored"]["count"] == 19
    assert response.json()["feed_window"]["reason"] == "invalid_feed"


@pytest.mark.asyncio
async def test_relative_redirect_preserves_original_host_and_ignores_cookies(monkeypatch):
    paths = []
    def remote(req):
        paths.append(req.url.path)
        assert req.url.host == "93.184.216.34"
        assert req.headers["host"] == "example.org"
        assert "cookie" not in req.headers
        if len(paths) == 1:
            return httpx.Response(302, headers={"Location": "../new.xml", "Set-Cookie": "session=untrusted; Path=/"})
        return httpx.Response(200, content=b"<rss><channel/></rss>")
    monkeypatch.setattr(history, "feed_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(remote)))
    result = await history.probe_feed({"id": 3, "feed_url": "https://example.org/path/feed"}, 1)
    assert result["state"] == "ok" and paths == ["/path/feed", "/new.xml"]


@pytest.mark.asyncio
async def test_cache_expires_and_cache_size_is_bounded(monkeypatch):
    now = [0]
    calls = []
    monkeypatch.setattr(history.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(history, "CACHE_LIMIT", 2)
    def remote(req):
        calls.append(req)
        return httpx.Response(200, content=b"<rss><channel/></rss>")
    monkeypatch.setattr(history, "feed_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(remote)))
    feed = {"id": 3, "feed_url": "https://example.org/feed"}
    await history.probe_feed(feed, 1)
    now[0] = history.SNAPSHOT_TTL + 1
    assert (await history.probe_feed(feed, 1))["cached"] is False
    assert len(calls) == 2
    for uid in (2, 3):
        await history.probe_feed(feed, uid)
    assert len(history._snapshot_cache) == 2


@pytest.mark.asyncio
async def test_failed_miniflux_bounds_do_not_hide_successful_feed_window(history_api, monkeypatch):
    client, feed, _calls = history_api
    original = api.app.state.client
    def failed_reader(req):
        if req.url.path.endswith("/entries"):
            return httpx.Response(503)
        if req.url.path.endswith("/me"):
            return httpx.Response(200, json={"id": 1, "is_admin": True})
        return httpx.Response(200, json=feed)
    async with httpx.AsyncClient(transport=httpx.MockTransport(failed_reader)) as reader:
        monkeypatch.setattr(api.app.state, "client", reader)
        response = await client.get("/mf/v1/ai/feeds/7/history", headers={"X-Auth-Token": "session"})
    monkeypatch.setattr(api.app.state, "client", original)
    assert response.status_code == 200
    assert response.json()["stored"]["state"] == "unavailable"
    assert response.json()["feed_window"]["count"] == 1


@pytest.mark.parametrize('url',['http://example.org:invalid/feed','http://example.org/\x01feed'])
@pytest.mark.asyncio
async def test_invalid_feed_url_keeps_available_stored_history(history_api,url):
    client,feed,_calls=history_api
    feed['feed_url']=url
    response=await client.get('/mf/v1/ai/feeds/7/history',headers={'X-Auth-Token':'session'})
    assert response.status_code==200
    value=response.json()
    assert value['stored']['state']=='ok'
    assert value['stored']['count']==19
    assert value['feed_window']['state']=='unavailable'
    assert url not in str(value['feed_window'])


@pytest.mark.asyncio
async def test_stored_legacy_miniflux_reports_supported_status_scope():
    def upstream(request):
        if "removed" in request.url.params.get_list("status"):
            return httpx.Response(400, json={"error_message": 'invalid entry status, valid status values are: "read" and "unread"'})
        assert request.url.params.get_list("status") == ["read", "unread"]
        date = "2020-01-01T00:00:00Z" if request.url.params["direction"] == "asc" else "2026-09-22T00:00:00Z"
        return httpx.Response(200, json={"total": 168, "entries": [{"published_at": date}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result = await history.stored_history(client, "http://reader", {}, 36)
    assert result["state"] == "ok" and result["count"] == 168
    assert result["includes_removed"] is False
    assert result["oldest_published_at"] == "2020-01-01T00:00:00Z"
    assert result["newest_published_at"] == "2026-09-22T00:00:00Z"


@pytest.mark.asyncio
async def test_stored_unrelated_bad_request_is_not_retried():
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(400, json={"error_message": "invalid feed"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        result = await history.stored_history(client, "http://reader", {}, 36)
    assert result == {"state": "unavailable", "count": None}
    assert len(calls) <= 2
