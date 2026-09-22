import json
import httpx
import pytest
import core, worker


async def execute(entry, html, result, monkeypatch, mode="ok"):
    calls = []

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
