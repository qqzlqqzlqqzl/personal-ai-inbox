import asyncio
import errno
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

import x_feed_server as server

from x_feed_server import render_atom


def test_render_atom_decodes_upstream_html_entities_once():
    xml = render_atom(
        "rasbt",
        [
            {
                "id": "123",
                "url": "https://x.com/rasbt/status/123",
                "created_at": "2026-09-09T13:26:10Z",
                "text": "Models &amp; systems",
                "author": {"name": "Sebastian Raschka"},
                "media": [],
            }
        ],
    )
    root = ET.fromstring(xml)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    assert entry.findtext("a:title", namespaces=ns) == "Models & systems"
    content = entry.findtext("a:content", namespaces=ns)
    assert "Models &amp; systems" in content
    assert "&amp;amp;" not in content


def mock_provider(monkeypatch, body, *, profile_status=404):
    requests = []
    client_type = httpx.AsyncClient

    def respond(request):
        requests.append(request.url.path)
        if request.url.path.startswith("/v1/user/"):
            return httpx.Response(profile_status)
        return httpx.Response(200, content=body)

    def client(**kwargs):
        return client_type(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(server.httpx, "AsyncClient", client)
    return requests


def cached_timeline():
    return {"fetched_at": 0, "handle": "fixture", "tweets": [{
        "id": "1", "text": "retained fixture", "created_at": "2026-10-08T00:00:00Z",
        "author": {"username": "fixture", "name": "Fixture"}, "media": [],
    }]}


INVALID_ROWS = [None, [], "not an object", 1, False]
INVALID_AUTHORS = [None, [], "not an object", 1, False]
INVALID_TIMELINES = [json.dumps(row) for row in INVALID_ROWS] + [
    json.dumps({"kind": "tweet", "author": author}) for author in INVALID_AUTHORS
]


@pytest.mark.parametrize("body", INVALID_TIMELINES)
def test_invalid_structure_retains_stale_cache_without_writing(monkeypatch, body):
    prior = cached_timeline()
    snapshot = json.dumps(prior, sort_keys=True)
    monkeypatch.setattr(server, "load_cache", lambda handle: prior)
    monkeypatch.setattr(server, "allowed_handles", lambda: {})

    def forbidden(*args):
        pytest.fail("invalid upstream data must not replace the cache")

    monkeypatch.setattr(server, "save_cache", forbidden)
    requests = mock_provider(monkeypatch, body)
    result = asyncio.run(server.x_feed("fixture", refresh=True))
    assert result.status_code == 200
    assert result.headers["x-x-feed-source"] == "stale-cache"
    assert result.headers["x-x-feed-items"] == "1"
    assert b"retained fixture" in result.body
    assert json.dumps(prior, sort_keys=True) == snapshot
    assert requests == ["/v1/timeline/fixture"]


@pytest.mark.parametrize("body", INVALID_TIMELINES)
@pytest.mark.parametrize("profile_status", [200, 404])
def test_invalid_structure_without_cache_keeps_deferred_or_503(monkeypatch, body, profile_status):
    monkeypatch.setattr(server, "load_cache", lambda handle: None)
    monkeypatch.setattr(server, "allowed_handles", lambda: {})

    def forbidden(*args):
        pytest.fail("invalid upstream data must not create a cache")

    monkeypatch.setattr(server, "save_cache", forbidden)
    requests = mock_provider(monkeypatch, body, profile_status=profile_status)
    if profile_status == 200:
        result = asyncio.run(server.x_feed("fixture"))
        assert result.status_code == 200
        assert result.headers["x-x-feed-source"] == "deferred-empty"
        assert result.headers["x-x-feed-items"] == "0"
    else:
        with pytest.raises(HTTPException) as caught:
            asyncio.run(server.x_feed("fixture"))
        assert caught.value.status_code == 503
        assert caught.value.detail == "ValueError"
    assert requests == ["/v1/timeline/fixture", "/v1/user/fixture"]


@pytest.mark.parametrize("row,message", [
    (None, "invalid X timeline row"),
    ({"kind": "tweet", "author": "PRIVATE_RESPONSE_MARKER"}, "invalid X timeline author"),
])
def test_invalid_structure_raises_fixed_value_error(monkeypatch, row, message):
    mock_provider(monkeypatch, json.dumps(row))
    with pytest.raises(ValueError, match="^" + message + "$"):
        asyncio.run(server.fetch_timeline("fixture"))


def test_valid_tweets_keep_filter_sort_limit_and_live_cache(monkeypatch):
    rows = [{"kind": "status", "author": "ignored non-tweet metadata"}]
    rows += [{"kind": "tweet", "author": {"username": "other"}}]
    rows += [{"kind": "tweet", "id": str(i), "created_at": f"2026-10-{i:02d}T00:00:00Z",
              "author": {"username": "Fixture"}} for i in range(1, 26)]
    requests = mock_provider(monkeypatch, "\n".join(json.dumps(row) for row in rows))
    monkeypatch.setattr(server, "load_cache", lambda handle: None)
    saved = []

    def save(handle, tweets):
        saved.append((handle, tweets))
        return {"handle": handle, "tweets": tweets, "fetched_at": 1}

    monkeypatch.setattr(server, "save_cache", save)
    result, source = asyncio.run(server.timeline("fixture"))
    assert source == "live"
    assert [row["id"] for row in result["tweets"]] == [str(i) for i in range(25, 5, -1)]
    assert saved == [("fixture", result["tweets"])]
    assert requests == ["/v1/timeline/fixture"]


def test_existing_error_record_still_uses_stale_cache(monkeypatch):
    prior = cached_timeline()
    monkeypatch.setattr(server, "load_cache", lambda handle: prior)
    requests = mock_provider(monkeypatch, '{"kind":"error"}')
    result, source = asyncio.run(server.timeline("fixture", force=True))
    assert result is prior and source == "stale-cache"
    assert requests == ["/v1/timeline/fixture"]


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("phase,error_number", [
    (None, None), ("write", errno.ENOSPC), ("write", errno.EACCES), ("replace", errno.EIO),
])
def test_native_fresh_data_survives_cache_write_errors(tmp_path, cached, phase, error_number):
    cache = tmp_path / "cache"
    cache.mkdir()
    target = cache / "fixture.json"
    temporary = target.with_suffix(".tmp")
    if cached:
        target.write_text(json.dumps(cached_timeline()))
    before = target.read_bytes() if cached else None
    fresh = {"kind": "tweet", "id": "fresh", "text": "fresh upstream fixture",
             "created_at": "2026-10-08T01:00:00Z", "author": {"username": "fixture"}, "media": []}
    requests = []
    client_type = httpx.AsyncClient
    write_text, replace = Path.write_text, Path.replace

    def upstream(request):
        requests.append(request.url.path)
        assert request.url.path == "/v1/timeline/fixture", "no extra profile or retry request"
        return httpx.Response(200, content=json.dumps(fresh))

    def client(**kwargs):
        return client_type(transport=httpx.MockTransport(upstream), **kwargs)

    def write(path, *args, **kwargs):
        if phase == "write" and path == temporary:
            raise OSError(error_number, "synthetic private storage failure")
        return write_text(path, *args, **kwargs)

    def move(path, destination):
        if phase == "replace" and path == temporary:
            raise OSError(error_number, "synthetic private storage failure")
        return replace(path, destination)

    with patch.object(server, "CACHE_DIR", cache), patch.object(server, "allowed_handles", return_value={}), \
         patch.object(server.httpx, "AsyncClient", client), patch.object(Path, "write_text", write), \
         patch.object(Path, "replace", move):
        result = asyncio.run(server.x_feed("fixture", refresh=True))
    assert result.status_code == 200
    assert result.headers["x-x-feed-source"] == ("native-uncached" if phase else "live")
    assert result.headers["x-x-feed-items"] == "1"
    assert b"fresh upstream fixture" in result.body and b"retained fixture" not in result.body
    assert b"synthetic private storage failure" not in result.body
    assert requests == ["/v1/timeline/fixture"]
    if phase:
        if cached:
            assert target.read_bytes() == before
        else:
            assert not target.exists()
    else:
        assert json.loads(target.read_text())["tweets"] == [fresh]


def test_cache_fallback_does_not_swallow_programming_errors():
    async def fresh(handle):
        return [{"id": "fresh"}]

    failure = RuntimeError("synthetic programming failure")
    with patch.object(server, "load_cache", return_value=None), \
         patch.object(server, "fetch_timeline", fresh), \
         patch.object(server, "save_cache", side_effect=failure):
        try:
            asyncio.run(server.timeline("fixture"))
        except RuntimeError as caught:
            assert caught is failure
        else:
            raise AssertionError("Only cache-write OSError may use native-uncached")
