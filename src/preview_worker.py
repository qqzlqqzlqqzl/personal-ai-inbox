"""Prepare article previews independently of AI enablement and model budgets."""
import argparse
import asyncio
import json
import logging
import os
import time
from urllib.parse import urlsplit

import httpx
import core
from feed_consumption import summary_feed_policy, SUMMARY_SOURCE
from content_input import discover_original_cover, is_our_social_feed, first_image_src
from worker import MF, worker_headers, discover_pending
from media_repair import repair_entry, needs_repair

log = logging.getLogger("uvicorn.error")
RETRY_SECONDS = 6 * 3600


def is_product_entry(url):
    parsed = urlsplit(url)
    return parsed.hostname in {"producthunt.com", "www.producthunt.com"} and parsed.path.startswith("/products/")


def pending_previews(now, limit=12, feed_id=None):
    where = ["state <> 'removed'", "(preview_checked_at IS NULL OR (preview_error IS NOT NULL AND preview_checked_at <= ?))"]
    values = [now - RETRY_SECONDS]
    if feed_id is not None:
        where.append("feed_id=?")
        values.append(feed_id)
    with core.connect() as db:
        return [dict(row) for row in db.execute(
            "SELECT entry_id FROM analyses WHERE " + " AND ".join(where)
            + " ORDER BY (cover_url IS NULL) DESC,published_at DESC,entry_id DESC LIMIT ?",
            (*values, max(1, min(limit, 12))),
        )]


async def prepare_one(client, row):
    entry_id = row["entry_id"]
    try:
        response = await client.get(MF + f"/v1/entries/{entry_id}", headers=worker_headers(), timeout=20)
        response.raise_for_status()
        entry = response.json()
        fields = {}
        with core.connect() as db:
            saved = db.execute("SELECT cover_url FROM analyses WHERE entry_id=?", (entry_id,)).fetchone()
        existing_cover = saved[0] if saved else None
        policy = summary_feed_policy(entry)
        if policy:
            if policy == "identity_unverified":
                core.update(entry_id, preview_checked_at=time.time(), preview_error="UnverifiedFeedIdentity")
                return {"entry_id": entry_id, "result": "failed", "error": "UnverifiedFeedIdentity"}
            # Use only an image already present in the cached RSS description.
            # No page lookup, image probing, enrichment or body repair is allowed.
            cover = first_image_src(entry.get("content", ""))
            core.update(entry_id, cover_url=cover, cover_source=SUMMARY_SOURCE if cover else None,
                        preview_checked_at=time.time(), preview_error=None)
            return {"entry_id": entry_id, "result": "updated" if cover else "text_only"}
        if is_product_entry(entry["url"]):
            from product_source import enrich_product_entry
            prepared = await enrich_product_entry(client, entry, MF, worker_headers())
            from card_translation import enqueue
            enqueue([{**entry,"content":prepared["content"]}], priority=20)
            for key in ("cover_url", "cover_source", "content_source"):
                if prepared.get(key):
                    fields[key] = prepared[key]
            result = "updated" if prepared.get("updated") or fields.get("cover_url") else "no_preview"
        elif is_our_social_feed(entry.get("feed", {}).get("feed_url", "")):
            cover = existing_cover or first_image_src(entry.get("content", ""))
            if cover and not existing_cover:
                fields.update(cover_url=cover, cover_source="social_post_media")
            core.update(entry_id, **fields, preview_checked_at=time.time(), preview_error=None)
            return {"entry_id": entry_id, "result": "updated" if cover else "text_only"}
        else:
            cover, source = (existing_cover, None) if existing_cover else await discover_original_cover(entry["url"], entry.get("title", ""), strict=True)
            if cover and not existing_cover:
                fields.update(cover_url=cover, cover_source=source)
            result = "updated" if cover else "no_preview"
        if fields:
            core.update(entry_id, **fields)
        if not is_product_entry(entry["url"]) and needs_repair(entry.get("content", "")):
            repaired = await repair_entry(client, entry, MF, worker_headers())
            if repaired["repaired"]:
                core.event("body_images_repaired", entry_id, str(repaired["repaired"]))
        core.update(entry_id, preview_checked_at=time.time(), preview_error="NoPreview" if result == "no_preview" else None)
        log.info("ai-news preview entry_id=%s result=%s", entry_id, result)
        return {"entry_id": entry_id, "result": result}
    except Exception as exc:
        # Error bodies and URLs can contain credentials. Store only the error type.
        error = type(exc).__name__ + (" HTTP " + str(exc.response.status_code) if isinstance(exc, httpx.HTTPStatusError) else "")
        core.update(entry_id, preview_checked_at=time.time(), preview_error=error)
        log.warning("ai-news preview entry_id=%s result=failed error=%s", entry_id, error)
        return {"entry_id": entry_id, "result": "failed", "error": error}


async def run_once(client=None, *, limit=12, feed_id=None):
    rows = pending_previews(time.time(), limit, feed_id)
    sem = asyncio.Semaphore(3)
    async def limited(row):
        async with sem:
            try:
                return await asyncio.wait_for(prepare_one(client, row), timeout=55)
            except asyncio.TimeoutError:
                core.update(row['entry_id'],preview_checked_at=time.time(),preview_error='PreviewTimeout')
                return {'entry_id':row['entry_id'],'result':'failed','error':'PreviewTimeout'}
    if client is None:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=20) as local:
            client = local
            results = await asyncio.gather(*(limited(row) for row in rows))
    else:
        results = await asyncio.gather(*(limited(row) for row in rows))
    return {"at": time.time(), "processed": len(results), "updated": sum(r["result"] == "updated" for r in results),
            "failed": sum(r["result"] == "failed" for r in results), "ai_requests": 0, "entries": results}


async def run_preview_worker():
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=20) as client:
        while True:
            try:
                core.put_meta("preview_heartbeat", time.time())
                if os.environ.get("MINIFLUX_API_KEY"):
                    await run_once(client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("ai-news preview worker error type=%s", type(exc).__name__)
            await asyncio.sleep(60)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--feed", type=int)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--report")
    args = parser.parse_args()
    core.init_db()
    core.migrate()
    if not os.environ.get("MINIFLUX_API_KEY"):
        from initialize_secrets import read_env
        os.environ["MINIFLUX_API_KEY"] = read_env("ai.env")["MINIFLUX_API_KEY"]
    report = await run_once(limit=args.limit, feed_id=args.feed)
    if args.report:
        (core.ROOT / args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())


async def run_discovery_worker():
    """RSS discovery is independent of long AI requests and the enable switch."""
    async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
        while True:
            try:
                if os.environ.get("MINIFLUX_API_KEY"):
                    await discover_pending(client)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("ai-news discovery error type=%s", type(exc).__name__)
            await asyncio.sleep(60)
