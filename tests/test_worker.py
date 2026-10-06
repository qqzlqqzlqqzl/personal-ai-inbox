import json
import httpx
import pytest
import core, worker, content_input


async def execute(entry, html, result, monkeypatch, mode="ok"):
    calls = []

    async def no_external_cover(_url, _title=""):
        return None, None

    monkeypatch.setattr(worker, "discover_original_cover", no_external_cover)

    def transport(request):
        calls.append(str(request.url))
        if request.url.path.endswith("/fetch-content"):
            return (
                httpx.Response(500)
                if mode == "fetch_error"
                else httpx.Response(200, json={"content": html})
            )
        if request.url.path.endswith("/chat/completions"):
            content = "bad json" if mode == "bad_json" else json.dumps(result)
            return httpx.Response(
                200,
                json={
                    "usage": {"total_tokens": 123},
                    "choices": [
                        {"message": {"content": content}, "finish_reason": "stop"}
                    ],
                },
            )
        return httpx.Response(200, json=entry)

    core.discover([entry])
    with core.connect() as c:
        row = c.execute(
            "SELECT * FROM analyses WHERE entry_id=?", (entry["id"],)
        ).fetchone()
    monkeypatch.setenv("ARK_API_KEY", "isolated-test-value")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await worker.process_one(client, row, core.settings())
    with core.connect() as c:
        after = dict(
            c.execute(
                "SELECT * FROM analyses WHERE entry_id=?", (entry["id"],)
            ).fetchone()
        )
    return calls, after


@pytest.mark.asyncio
async def test_real_content_is_input_not_rss_teaser(
    db, entry, full_html, model_result, monkeypatch
):
    calls, after = await execute(entry, full_html, model_result, monkeypatch)
    assert after["state"] == "done"
    assert after["image_count"] == 1
    assert after["input_chars"] > len(entry["content"])
    assert "Only a teaser" not in after["source_text"]
    assert after["content_source"] == "original_url"


@pytest.mark.asyncio
async def test_fetch_failure_never_calls_ai(
    db, entry, full_html, model_result, monkeypatch
):
    calls, after = await execute(
        entry, full_html, model_result, monkeypatch, "fetch_error"
    )
    assert after["state"] == "fetch_error"
    assert not any("/chat/completions" in u for u in calls)
    assert after["attempts"] == 1 and after["next_try"] > 0


@pytest.mark.asyncio
async def test_bad_json_accounts_usage(db, entry, full_html, model_result, monkeypatch):
    _, after = await execute(entry, full_html, model_result, monkeypatch, "bad_json")
    assert after["state"] == "ai_error"
    with core.connect() as c:
        assert c.execute("SELECT actual FROM usage").fetchone()[0] == 123


@pytest.mark.asyncio
async def test_fabricated_evidence_rejected(
    db, entry, full_html, model_result, monkeypatch
):
    model_result["evidence"] = "This sentence does not exist in the original."
    _, after = await execute(entry, full_html, model_result, monkeypatch)
    assert after["state"] == "ai_error" and not after["result"]


@pytest.mark.asyncio
async def test_duplicate_content_reuses_analysis(
    db, entry, full_html, model_result, monkeypatch
):
    await execute(entry, full_html, model_result, monkeypatch)
    other = {**entry, "id": 2, "url": "https://example.org/duplicate"}
    calls, after = await execute(other, full_html, model_result, monkeypatch)
    assert after["state"] == "done" and after["duplicate_of"] == 1
    assert not any("/chat/completions" in u for u in calls)


@pytest.mark.asyncio
async def test_more_than_200_entries_discovered_once(db, entry):
    entries = [
        {**entry, "id": i, "url": f"https://example.org/{i}"} for i in range(1, 351)
    ]

    def handle(request):
        cursor = int(request.url.params.get("after_entry_id", 0))
        found = [e for e in entries if e["id"] > cursor][:100]
        return httpx.Response(200, json={"entries": found, "total": len(entries)})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as c:
        assert await worker.discover_pending(c) == 350
        assert await worker.discover_pending(c) == 0
    with core.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM analyses").fetchone()[0] == 350
    assert core.get_meta("entry_cursor") == 350


@pytest.mark.asyncio
async def test_social_original_post_does_not_require_blog_fetch(
    db, entry, full_html, model_result, monkeypatch
):
    entry["feed"]["feed_url"] = "http://127.0.0.1:1200/telegram/channel/telegram"
    entry["content"] = full_html
    calls, after = await execute(entry, full_html, model_result, monkeypatch)
    assert after["state"] == "done" and after["content_source"] == "social_adapter_post"
    assert not any("/fetch-content" in u for u in calls)


@pytest.mark.asyncio
async def test_paper_abstract_never_claimed_as_full_paper(
    db, entry, full_html, model_result, monkeypatch
):
    entry["url"] = "https://arxiv.org/abs/2609.12345"
    calls, after = await execute(entry, full_html, model_result, monkeypatch)
    assert after["state"] == "requires_fulltext_adapter"
    assert not any("/chat/completions" in url for url in calls)
    assert not after["result"]


@pytest.mark.parametrize("srcset", [
    "https://example.org/hero.jpg 1x,",
    "/small.jpg 1x, /hero.jpg 2x",
    "/small.jpg 1x, /hero.jpg 2x,   ",
    " , /small.jpg 1x, , /hero.jpg 2x, , ",
])
def test_cover_srcset_uses_last_nonempty_candidate(srcset):
    html = f'<main><img srcset="{srcset}"></main>'
    assert content_input.select_cover_from_page(html, "https://example.org/article") == (
        "https://example.org/hero.jpg", "page_first_image")


@pytest.mark.parametrize("srcset", ["", "   ", ",", ", ,   ,"])
def test_cover_empty_srcset_keeps_social_fallback(srcset):
    html = f'<meta property="og:image" content="/social.jpg"><main><img srcset="{srcset}"></main>'
    assert content_input.select_cover_from_page(html, "https://example.org/article") == (
        "https://example.org/social.jpg", "social_meta")


@pytest.mark.parametrize("attributes,expected", [
    ('data-src="/data.jpg" data-lazy-src="/lazy.jpg" src="/src.jpg"', "/data.jpg"),
    ('data-lazy-src="/lazy.jpg" src="/src.jpg"', "/lazy.jpg"),
    ('src="/src.jpg"', "/src.jpg"),
])
def test_cover_srcset_keeps_existing_attribute_priority(attributes, expected):
    html = f'<main><img {attributes} srcset="/srcset.jpg 2x,"></main>'
    assert content_input.select_cover_from_page(html, "https://example.org/article") == (
        "https://example.org" + expected, "page_first_image")


@pytest.mark.parametrize("candidate", [
    "javascript:alert(1)", "https://user:pass@example.org/photo.jpg", "/avatar.jpg",
])
def test_cover_srcset_retains_url_safety_and_decoration_filters(candidate):
    html = f'<meta property="og:image" content="/social.jpg"><main><img srcset="{candidate} 2x,"></main>'
    assert content_input.select_cover_from_page(html, "https://example.org/article") == (
        "https://example.org/social.jpg", "social_meta")


def test_cover_selection_rejects_small_decorative_image():
    html = """
    <html><head><meta property="og:image" content="/og/social.png"></head><body>
      <img src="/editorial-motion/panda/poster.webp" width="96" height="96">
      <img src="https://cdn.example.com/article-cover.webp" alt="当 AI 拿走一切之后">
    </body></html>
    """
    url, source = content_input.select_cover_from_page(
        html, "https://example.com/posts/ai/", "当 AI 拿走一切之后"
    )
    assert url == "https://cdn.example.com/article-cover.webp"
    assert source == "page_hero"


def test_cover_selection_uses_social_meta_when_page_has_no_real_image():
    html = """
    <html><head><meta property="og:image" content="/og/article.png"></head><body>
      <img src="/avatar.png" class="avatar" width="80" height="80">
    </body></html>
    """
    url, source = content_input.select_cover_from_page(
        html, "https://example.com/posts/no-image/", "No image"
    )
    assert url == "https://example.com/og/article.png"
    assert source == "social_meta"


def test_extracted_cover_rejects_tracking_and_avatar_images():
    html = """
    <img src="https://example.com/avatar.png" class="profile-avatar" width="80" height="80">
    <img src="https://example.com/content.jpg">
    """
    assert content_input.first_image_src(html) == "https://example.com/content.jpg"


def test_cover_skips_header_and_prefers_later_article_hero():
    html = '<header><img src="/banner.jpg"></header><main><img src="/chart.jpg"><img class="hero" src="/cover.jpg"></main>'
    assert content_input.select_cover_from_page(html, "https://example.org", "Title") == ("https://example.org/cover.jpg", "page_hero")


def test_cover_prefers_social_metadata_over_unrelated_page_image():
    html = '<meta property="og:image" content="/article.jpg"><img src="/advert.jpg"><main><img src="/diagram.jpg"></main>'
    assert content_input.select_cover_from_page(html, "https://example.org")[0] == "https://example.org/article.jpg"


def test_cover_never_falls_back_to_global_banner():
    assert content_input.select_cover_from_page('<img src="/banner.jpg">', "https://example.org") == (None, None)


def test_cover_rejects_tracking_pixel_but_allows_silicon():
    html = '<main><img src="/track.gif" width="1" height="200"><img src="/silicon.jpg"></main>'
    assert content_input.select_cover_from_page(html,"https://example.org")[0] == "https://example.org/silicon.jpg"


def test_cover_rejects_generic_site_mascot_even_through_proxy():
    import base64
    original="https://go.dev/doc/gopher/runningsquare.jpg"
    encoded=base64.urlsafe_b64encode(original.encode()).decode()
    assert content_input.first_image_src(f'<img src="/mf/proxy/hash/{encoded}">') is None
    assert content_input.select_cover_from_page(f'<meta property="og:image" content="{original}">',"https://go.dev/blog/test")== (None,None)


def test_cover_can_fall_back_to_social_when_hero_is_unavailable():
    html='<meta property="og:image" content="https://rss.example.org/cover.jpg"><main><img class="hero" src="https://cdn.example.org/cover.jpg"></main>'
    assert content_input.select_cover_from_page(html,"https://example.org",prefer_social=True)==("https://rss.example.org/cover.jpg","social_meta")


@pytest.mark.asyncio
async def test_original_cover_checks_image_and_falls_back_after_403(monkeypatch):
    original = httpx.AsyncClient
    calls=[]
    def transport(req):
        calls.append(str(req.url))
        if req.url.path=="/article":
            return httpx.Response(200,text='<meta property="og:image" content="/social.jpg"><main><img class="hero" src="/hero.jpg"></main>')
        if req.url.path=="/hero.jpg": return httpx.Response(403)
        if req.url.path=="/social.jpg": return httpx.Response(200,headers={"content-type":"image/jpeg"},content=b"image")
        raise AssertionError("unexpected URL")
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kwargs:original(transport=httpx.MockTransport(transport),**kwargs))
    result=await content_input.discover_original_cover("https://example.org/article",strict=True)
    assert result==("https://example.org/social.jpg","social_meta")
    assert calls==["https://example.org/article","https://example.org/hero.jpg","https://example.org/social.jpg"]


@pytest.mark.asyncio
async def test_original_cover_distinguishes_failed_page_from_no_image(monkeypatch):
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,"AsyncClient",lambda **kwargs:original(transport=httpx.MockTransport(lambda req:httpx.Response(403)),**kwargs))
    with pytest.raises(httpx.HTTPStatusError):
        await content_input.discover_original_cover("https://example.org/article",strict=True)
    assert await content_input.discover_original_cover("https://example.org/article")== (None,None)


@pytest.mark.asyncio
async def test_backfill_keeps_extracted_fallback_when_original_fails(db, entry, monkeypatch):
    import asyncio,backfill_covers
    core.discover([entry])
    entry["content"]='<img src="https://example.org/article-cover.jpg">'
    async def unavailable(*args,**kwargs):raise httpx.ConnectTimeout("unavailable")
    monkeypatch.setattr(backfill_covers,"discover_original_cover",unavailable)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=entry))) as c:
        result=await backfill_covers.one(c,asyncio.Semaphore(1),{"entry_id":entry["id"]})
    assert result=="updated"
    with core.connect() as c:
        row=c.execute("SELECT cover_url,cover_source FROM analyses WHERE entry_id=?",(entry["id"],)).fetchone()
    assert tuple(row)==("https://example.org/article-cover.jpg","extracted_content")
