"""Backfill article preview images without using the AI model or its budget."""
import argparse, asyncio, json, time
import httpx
import core
from content_input import discover_original_cover, first_image_src
from initialize_secrets import read_env

MF = "http://127.0.0.1:8091/mf"


async def one(client, sem, row, extracted_only=False):
    async with sem:
        entry_id = row["entry_id"]
        try:
            response = await client.get(f"{MF}/v1/entries/{entry_id}")
            response.raise_for_status()
            entry = response.json()
            cover, source = None, None
            discovery_failed = False
            if not extracted_only:
                try:
                    cover, source = await discover_original_cover(
                        entry.get("url", ""), entry.get("title", ""), strict=True
                    )
                except Exception:
                    discovery_failed = True
            if not cover:
                cover = first_image_src(entry.get("content", ""))
                source = "extracted_content" if cover else None
            if not cover:
                for enclosure in entry.get("enclosures") or []:
                    mime = str(enclosure.get("mime_type") or enclosure.get("type") or "")
                    candidate = str(enclosure.get("url") or "").strip()
                    if mime.startswith("image/") and candidate.startswith(("http://", "https://")):
                        cover, source = candidate[:4000], "enclosure"
                        break
            if cover:
                core.update(entry_id, cover_url=cover, cover_source=source)
                return "updated"
            return "failed" if discovery_failed else "no_cover"
        except Exception:
            return "failed"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--retry-report")
    parser.add_argument("--extracted-only", action="store_true")
    parser.add_argument("--report", default="artifacts/cover-backfill.json")
    parser.add_argument("--feed", type=int, action="append", default=[])
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()
    core.init_db()
    core.migrate()
    where = ["cover_url IS NULL"]
    values = []
    if args.feed:
        where.append("feed_id IN (" + ",".join("?" for _ in args.feed) + ")")
        values.extend(args.feed)
    if args.retry_report:
        report = json.loads((core.ROOT / args.retry_report).read_text())
        retry_ids = sorted({e["entry_id"] for e in report["entries"] if e["result"] == "failed"})
        where.append("entry_id IN (SELECT value FROM json_each(?))")
        values.append(json.dumps(retry_ids))
    with core.connect() as c:
        rows = c.execute(
            "SELECT entry_id,feed_id FROM analyses WHERE "
            + " AND ".join(where)
            + " ORDER BY published_at DESC,entry_id DESC LIMIT ?",
            (*values, max(1, min(args.limit, 5000))),
        ).fetchall()
    token = read_env("ai.env")["MINIFLUX_API_KEY"]
    sem = asyncio.Semaphore(max(1, min(args.concurrency, 8)))
    started = time.perf_counter()
    async with httpx.AsyncClient(
        headers={"X-Auth-Token": token},
        timeout=20,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        results = [None] * len(rows)
        async def indexed(index, row):
            return index, await one(client, sem, row, args.extracted_only)
        tasks = [asyncio.create_task(indexed(i, row)) for i, row in enumerate(rows)]
        processed = 0
        for task in asyncio.as_completed(tasks):
            index, result = await task
            results[index] = result
            processed += 1
            if processed % 50 == 0:
                progress = {"requested":len(rows), "processed":processed, "complete":False,
                            "updated":results.count("updated"), "no_cover":results.count("no_cover"),
                            "failed":results.count("failed"), "seconds":round(time.perf_counter()-started,2),
                            "ai_requests":0, "extracted_only":args.extracted_only}
                (core.ROOT / args.report).write_text(json.dumps(progress, indent=2))
                print(json.dumps(progress), flush=True)
    summary = {
        "requested": len(rows),
        "processed": len(rows),
        "complete": True,
        "updated": results.count("updated"),
        "no_cover": results.count("no_cover"),
        "failed": results.count("failed"),
        "seconds": round(time.perf_counter() - started, 2),
        "ai_requests": 0,
        "extracted_only": args.extracted_only,
    }
    report = {**summary, "at": time.time(), "entries": [{"entry_id": row["entry_id"], "result": result} for row, result in zip(rows, results)]}
    (core.ROOT / args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
