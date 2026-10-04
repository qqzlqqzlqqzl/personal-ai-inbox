"""Background original-content extraction and structured AI evaluation."""
from work_admission import check,options,async_call,AdmissionStopped

import asyncio, os, json, time, re, logging
import httpx
from content_input import (
    content_text,
    is_our_social_feed,
    model_payload,
    add_original_cover,
    discover_original_cover,
    first_image_src,
)
from core import (
    connect,
    discover,
    update,
    settings,
    event,
    hash_text,
    reserve_budget,
    close_budget,
    get_meta,
    put_meta,
    evidence_matches,
    next_batch,
)

MF = "http://127.0.0.1:8091/mf"
log = logging.getLogger("uvicorn.error")


def worker_headers():
    return {"X-Auth-Token": os.environ.get("MINIFLUX_API_KEY", "")}


def validate_result(data):
    if not isinstance(data, dict):
        raise ValueError("Model response must be an object")
    result = {}
    for key in ["score", "technical_score", "business_score"]:
        n = data.get(key)
        if isinstance(n, bool) or not isinstance(n, (int, float)) or not 0 <= n <= 10:
            raise ValueError("Invalid score")
        result[key] = round(n, 1)
    for key, limit in [
        ("summary", 450),
        ("reason", 400),
        ("evidence", 160),
        ("content_type", 30),
    ]:
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError("Missing field " + key)
        result[key] = data[key].strip()[:limit]
    if not isinstance(data.get("worth_reading"), bool):
        raise ValueError("Missing boolean worth_reading")
    result["worth_reading"] = data["worth_reading"]
    if not isinstance(data.get("tags"), list):
        raise ValueError("Missing tags")
    result["tags"] = [str(t)[:40] for t in data["tags"][:6] if isinstance(t, str)]
    return result


async def mf_get(client, path, **params):
    r = await client.get(MF + path, headers=worker_headers(), params=params, timeout=70)
    r.raise_for_status()
    return r.json()


async def discover_pending(client):
    cursor = int(get_meta("entry_cursor", 0))
    count = 0
    for _ in range(50):
        data = await mf_get(
            client,
            "/v1/entries",
            limit=100,
            order="id",
            direction="asc",
            after_entry_id=cursor,
        )
        entries = data.get("entries", [])
        if not entries:
            break
        discover(entries)
        cursor = max(e["id"] for e in entries)
        put_meta("entry_cursor", cursor)
        count += len(entries)
        if len(entries) < 100:
            break
    put_meta("discovered_at", time.time())
    return count


async def process_one(client, row, cfg, *,admission=None):
    check(admission)
    def mutate(function,*args,**kwargs):
        check(admission)
        return function(*args,**kwargs)
    entry_id = row["entry_id"]
    started = time.perf_counter()
    phase = "fetch_error"
    try:
        entry = await async_call(admission,mf_get,client, f"/v1/entries/{entry_id}")
        from urllib.parse import urlsplit

        original = urlsplit(entry["url"])
        if original.hostname in {"arxiv.org", "www.arxiv.org", "export.arxiv.org"}:
            mutate(update,
                entry_id,
                state="requires_fulltext_adapter",
                error="论文原文适配尚未接入，不把摘要页当成论文全文",
            )
            return
        from product_source import is_product_entry, enrich_product_entry
        product = is_product_entry(entry)
        from adafruit_source import is_adafruit, resolve as resolve_adafruit
        adafruit = is_adafruit(entry['url'])
        social = is_our_social_feed(entry.get("feed", {}).get("feed_url", ""))
        source = "social_adapter_post" if social else "original_url"
        current = entry.get("content", "")
        cover_url = row["cover_url"] if "cover_url" in row.keys() else None
        cover_source = row["cover_source"] if "cover_source" in row.keys() else None
        if adafruit:
            fulltext = await resolve_adafruit(entry,**options(admission))
            current = fulltext['html']
            source = fulltext['receipt']['source']
            if source == 'adafruit_linked_original':
                from prepared_content import remember
                remember(entry,current,source,fulltext['receipt'],**options(admission))
        if product:
            prepared = await enrich_product_entry(client, entry, MF, worker_headers(),**options(admission))
            current = prepared["content"]
            source = prepared.get("content_source") or "product_page"
            cover_url = prepared.get("cover_url") or cover_url
            cover_source = prepared.get("cover_source") or cover_source
            if cover_url:
                mutate(update,entry_id, cover_url=cover_url, cover_source=cover_source)
        if not social and not product and not cover_url:
            cover_url, cover_source = await discover_original_cover(
                entry["url"], entry.get("title", ""),**options(admission)
            )
            if cover_url:
                mutate(update,entry_id, cover_url=cover_url, cover_source=cover_source)
        if not social and not product and not adafruit and (
            not row["extracted_at"] or hash_text(current) != row["content_hash"]
        ):
            mutate(update,entry_id, state="fetching")
            fetched = await async_call(admission,mf_get,
                client, f"/v1/entries/{entry_id}/fetch-content", update_content="true"
            )
            current = fetched.get("content", "")
            from media_repair import repair_entry, needs_repair
            if needs_repair(current):
                try:
                    fixed = await repair_entry(client, {**entry, "content": current}, MF, worker_headers(),**options(admission))
                    current = fixed["content"]
                except (httpx.HTTPError, ValueError):
                    log.warning("ai-news body_image_repair deferred entry_id=%s", entry_id)
            if not current:
                raise ValueError("Original extraction returned no content")
        if not social and not product and not adafruit:
            current = await add_original_cover(
                client, entry, current, MF, worker_headers(),**options(admission)
            )
        if not cover_url:
            cover_url = first_image_src(current)
            cover_source = "extracted_content" if cover_url else None
        if not cover_url:
            for enclosure in entry.get("enclosures") or []:
                media_type = str(enclosure.get("mime_type") or enclosure.get("type") or "")
                candidate = str(enclosure.get("url") or "").strip()
                if media_type.startswith("image/") and candidate.startswith(("http://", "https://")):
                    cover_url = candidate[:4000]
                    cover_source = "enclosure"
                    break
        text, images = content_text(current)
        from content_quality import assess, serialized
        quality = assess(url=entry['url'], html_body=current, text=text,
                         extraction_state='available', observed_at=time.time())
        excluded = quality['recommendation_eligible'] is False
        if len(text) < (8 if social else 120) and not excluded:
            mutate(update,
                entry_id,
                state="insufficient_content",
                error="原文信息过少，不生成价值评分",
                source_chars=len(text),
                image_count=images,
                content_source=source,
                cover_url=cover_url,
                cover_source=cover_source,
            )
            return
        used, message = model_payload(entry, text, source, cfg)
        prompt_hash = hash_text(cfg["prompt"] + cfg["model"] + cfg["base_url"])
        mutate(update,
            entry_id,
            content_hash=hash_text(current),
            source_chars=len(text),
            input_chars=len(used),
            source_text=used,
            image_count=images,
            content_source=source,
            cover_url=cover_url,
            cover_source=cover_source,
            extracted_at=time.time(),
            truncated=int(len(text) > len(used)),
            error=None,
            content_quality=serialized(quality, {'entry_id': entry_id, 'user_id': entry['user_id'],
                'url': entry['url'], 'content_hash': hash_text(current), 'source_text': used}),
        )
        if excluded:
            mutate(update, entry_id, state='content_excluded', attempts=0, next_try=0)
            return
        if not os.environ.get("ARK_API_KEY"):
            mutate(update,entry_id, state="waiting_model")
            return
        phase = "ai_error"
        with connect() as c:
            duplicate = c.execute(
                "SELECT * FROM analyses WHERE entry_id<>? AND user_id=? AND state='done' AND source_text=? AND prompt_hash=? ORDER BY analyzed_at DESC LIMIT 1",
                (entry_id, entry["user_id"], used, prompt_hash),
            ).fetchone()
        if duplicate:
            update(
                entry_id,
                state="done",
                result=duplicate["result"],
                score=duplicate["score"],
                technical_score=duplicate["technical_score"],
                business_score=duplicate["business_score"],
                model=cfg["model"],
                prompt_hash=prompt_hash,
                tokens=0,
                analyzed_at=time.time(),
                duplicate_of=duplicate["entry_id"],
            )
            event("analysis_reused", entry_id, "相同正文复用已验证分析")
            log.info("ai-news analysis_reused entry_id=%s duplicate_of=%s duration_ms=%.1f", entry_id, duplicate["entry_id"], (time.perf_counter()-started)*1000)
            return
        usage_id = reserve_budget(entry_id, message, cfg)
        if usage_id is None:
            update(entry_id, state="budget_paused", next_try=time.time() + 3600)
            return
        update(entry_id, state="analyzing", model=cfg["model"], prompt_hash=prompt_hash)
        body = {
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": cfg["prompt"]},
                {"role": "user", "content": message},
            ],
            "max_tokens": cfg["max_output_tokens"],
        }
        if cfg["json_mode"]:
            body["response_format"] = {"type": "json_object"}
        response = await client.post(
            cfg["base_url"].rstrip("/") + "/chat/completions",
            json=body,
            headers={"Authorization": "Bearer " + os.environ["ARK_API_KEY"]},
            timeout=120,
        )
        response.raise_for_status()
        raw = response.json()
        tokens = int(raw.get("usage", {}).get("total_tokens") or 0)
        if tokens:
            close_budget(usage_id, tokens)
        content = raw["choices"][0]["message"].get("content")
        if not content:
            raise ValueError("Model returned empty content")
        if raw["choices"][0].get("finish_reason") == "length":
            raise ValueError("Model output exceeded token limit")
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        result = validate_result(json.loads(content))
        if not evidence_matches(result["evidence"], used):
            raise ValueError("Evidence quotation not present in model input")
        update(
            entry_id,
            state="done",
            result=json.dumps(result, ensure_ascii=False),
            score=result["score"],
            technical_score=result["technical_score"],
            business_score=result["business_score"],
            tokens=tokens,
            analyzed_at=time.time(),
            error=None,
            attempts=0,
            next_try=0,
        )
        event("analysis_done", entry_id, "真实原文/原帖已评价；正文图片保留")
        log.info(
            "ai-news analysis_done entry_id=%s duration_ms=%.1f tokens=%s source_chars=%s",
            entry_id,
            (time.perf_counter() - started) * 1000,
            tokens,
            len(text),
        )
    except AdmissionStopped:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        attempts = row["attempts"] + 1
        detail = type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            detail += " HTTP " + str(exc.response.status_code)
            if exc.response.status_code == 404 and "/v1/entries/" in str(
                exc.request.url
            ):
                mutate(update,entry_id, state="removed", error="条目已从阅读器移除")
                return
        elif isinstance(exc, ValueError):
            detail += ": validation failed"
        mutate(update,
            entry_id,
            state=phase,
            attempts=attempts,
            next_try=time.time() + min(86400, 300 * 2 ** min(attempts, 8)),
            error=detail,
        )
        mutate(event,phase, entry_id, detail)
        log.warning(
            "ai-news analysis_failed entry_id=%s phase=%s attempts=%s duration_ms=%.1f detail=%s",
            entry_id,
            phase,
            attempts,
            (time.perf_counter() - started) * 1000,
            detail,
        )


async def run_worker():
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as client:
        while True:
            put_meta("worker_heartbeat", time.time())
            cfg = settings()
            if not os.environ.get("MINIFLUX_API_KEY"):
                await asyncio.sleep(30)
                continue
            try:
                if cfg["enabled"]:
                    states = ["pending", "fetch_error", "ai_error", "budget_paused"]
                    if os.environ.get("ARK_API_KEY"):
                        states.append("waiting_model")
                    rows = next_batch(states, time.time())
                    for row in rows:
                        if not settings()["enabled"]:
                            break
                        await process_one(client, row, settings())
                        put_meta("worker_heartbeat", time.time())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                event("worker_error", detail=type(exc).__name__)
                # Frame locations are useful; exception messages can contain credentials/body text.
                import traceback
                frames = " > ".join(f"{f.name}:{f.lineno}" for f in traceback.extract_tb(exc.__traceback__))
                log.error("ai-news worker_loop_error type=%s frames=%s", type(exc).__name__, frames)
            await asyncio.sleep(cfg["interval_seconds"])
