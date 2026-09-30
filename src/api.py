"""Same-origin reader gateway and authenticated AI extension API."""

import asyncio, json, os, time, contextlib, logging, uuid, mimetypes, math, re, shutil
from contextlib import asynccontextmanager
from pathlib import Path
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, Response, FileResponse, HTMLResponse
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.gzip import IdentityResponder
from starlette.datastructures import Headers
from core import (
    ROOT,
    init_db,
    init_usage,
    connect,
    settings,
    save_settings,
    decorate,
    update,
    event,
    status_summary,
    migrate,
    get_meta,
    put_meta,
)
from worker import run_worker, MF
from preview_worker import run_preview_worker, run_discovery_worker
from content_input import first_image_src
from card_translation import enqueue as enqueue_cards, run_translation_worker


@asynccontextmanager
async def lifespan(app):
    init_db()
    init_usage()
    migrate()
    # Only the legacy worker may recover its old work at web startup.
    # Live Kaggle preparations are managed by their own claim ledger.
    if settings().get("enabled"):
        with connect() as c:
            c.execute(
                "UPDATE analyses SET state='pending' WHERE state IN ('fetching','analyzing') AND updated_at<?",
                (time.time()-600,),
            )
    app.state.client = httpx.AsyncClient(
        timeout=80, follow_redirects=False, trust_env=False
    )
    task = asyncio.create_task(run_worker())
    preview_task = asyncio.create_task(run_preview_worker())
    discovery_task = asyncio.create_task(run_discovery_worker())
    translation_task = asyncio.create_task(run_translation_worker())
    yield
    task.cancel()
    preview_task.cancel()
    discovery_task.cancel()
    translation_task.cancel()
    for background_task in (task, preview_task, discovery_task, translation_task):
        with contextlib.suppress(asyncio.CancelledError):
            await background_task
    await app.state.client.aclose()


app = FastAPI(
    title="Personal AI News add-on",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
access_log = logging.getLogger("uvicorn.error")


def accepts_gzip_encoding(value):
    for part in value.lower().split(","):
        coding, *params = part.strip().split(";")
        if coding == "gzip":
            try:
                return all(float(p.strip()[2:]) > 0 for p in params if p.strip().startswith("q="))
            except ValueError:
                return False
    return False


class NegotiatedGZipMiddleware(GZipMiddleware):
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not accepts_gzip_encoding(
            Headers(scope=scope).get("accept-encoding", "")
        ):
            responder = IdentityResponder(
                self.app, self.minimum_size,
                exclude_content_types=self.exclude_content_types,
            )
            await responder(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(NegotiatedGZipMiddleware, minimum_size=512, compresslevel=5)
from request_limits import RequestLimits
app.add_middleware(RequestLimits)


def auth_headers(request):
    return {
        k: request.headers[k]
        for k in ["authorization", "x-auth-token"]
        if k in request.headers
    }


async def authorize(request, *, admin=False):
    if not auth_headers(request):
        raise HTTPException(401, "请先登录阅读器")
    try:
        r = await app.state.client.get(
            MF + "/v1/me", headers=auth_headers(request), timeout=8
        )
    except httpx.HTTPError:
        raise HTTPException(503, "Miniflux 尚未完成连接配置")
    if r.status_code != 200:
        raise HTTPException(401, "认证失败")
    identity = r.json()
    if admin and identity.get("is_admin") is not True:
        raise HTTPException(403, "仅管理员可以修改服务器全局配置")
    return identity["id"]


@app.middleware("http")
async def security_headers(request, call_next):
    started = time.perf_counter()
    request_id = uuid.uuid4().hex[:12]
    origin = request.headers.get("origin")
    if request.method not in ["GET", "HEAD", "OPTIONS"] and (
        request.headers.get("sec-fetch-site") == "cross-site"
        or (origin and origin != str(request.base_url).rstrip("/"))
    ):
        return JSONResponse(
            {"error_message": "Cross-origin writes are not allowed"}, status_code=403
        )
    try:
        size = int(request.headers.get("content-length", "0") or 0)
    except ValueError:
        return JSONResponse(
            {"error_message": "Invalid Content-Length"}, status_code=400
        )
    if size > 2 * 1024 * 1024:
        return JSONResponse({"error_message": "Request too large"}, status_code=413)
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - started) * 1000
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Request-ID"] = request_id
    response.headers["Server-Timing"] = f"app;dur={elapsed_ms:.1f}"
    if "/v1/" in request.url.path:
        response.headers["Cache-Control"] = "no-store"
    if request.url.path.startswith("/mf/v1/") or elapsed_ms >= 500:
        access_log.info(
            "ai-news request id=%s method=%s path=%s status=%s duration_ms=%.1f response_bytes=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            response.headers.get("content-length", "-"),
        )
    return response


@app.get("/healthz")
async def health():
    checks = {"gateway": True, "reader": False, "rsshub": False}
    for key, url in [
        ("reader", MF + "/healthcheck"),
        ("rsshub", "http://127.0.0.1:1200/healthz"),
    ]:
        try:
            checks[key] = (
                await app.state.client.get(url, timeout=3)
            ).status_code == 200
        except httpx.HTTPError:
            pass
    return {
        "services": checks,
        "ready": all(checks.values()),
        "model_configured": bool(os.environ.get("ARK_API_KEY")),
        "reader_worker_configured": bool(os.environ.get("MINIFLUX_API_KEY")),
    }


@app.get("/readyz")
async def ready():
    value = await health()
    return JSONResponse(value, status_code=200 if value["ready"] else 503)


@app.get("/mf/v1/ai/settings")
async def get_settings(request: Request):
    await authorize(request, admin=True)
    return {
        **settings(),
        "api_key_configured": bool(os.environ.get("ARK_API_KEY")),
        "key_management": "server_environment",
    }


@app.put("/mf/v1/ai/settings")
async def put_settings(request: Request):
    await authorize(request, admin=True)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected an object")
    campaign_config = ROOT / "src/kaggle_batch/cloud-config-month-primary.json"
    if (body.get("enabled") is True or body.get("translation_enabled") is True) and campaign_config.exists():
        campaign = json.loads(campaign_config.read_text())
        if campaign.get("queue_scope") == "all_enabled_feeds":
            raise HTTPException(409, "Kaggle 接管中，不能同时开启备用付费分析任务")
    if "api_key" in body:
        raise HTTPException(
            400, "密钥请在服务器受限环境配置中设置，接口不接收或回显密钥"
        )
    try:
        result = save_settings(body)
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(400, "设置格式错误，请检查模型地址与预算")
    event("settings_saved")
    return result


@app.get("/mf/v1/ai/status")
async def ai_status(request: Request):
    uid = await authorize(request, admin=True)
    from month_control import status as month_status
    campaign = await asyncio.to_thread(month_status)
    summary = status_summary(uid)
    reader_total = summary["coverage"].get("total_articles", 0)
    source_count = 0
    try:
        entries_response, feeds_response = await asyncio.gather(
            app.state.client.get(
                MF + "/v1/entries", headers=auth_headers(request), params={"limit": 1}, timeout=8
            ),
            app.state.client.get(MF + "/v1/feeds", headers=auth_headers(request), timeout=8),
        )
        if entries_response.status_code == 200:
            reader_total = int(entries_response.json().get("total", reader_total))
        if feeds_response.status_code == 200:
            source_count = len(feeds_response.json())
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    summary["coverage"] = {
        **summary["coverage"],
        "reader_total": reader_total,
        "source_count": source_count,
    }
    disk = shutil.disk_usage(ROOT)
    mem_total = mem_available = 0
    try:
        memory = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            memory[key] = int(value.strip().split()[0]) * 1024
        mem_total = memory.get("MemTotal", 0)
        mem_available = memory.get("MemAvailable", 0)
    except (OSError, ValueError, IndexError):
        pass
    database = ROOT / "state" / "analysis.sqlite3"
    summary["resources"] = {
        "disk_total_bytes": disk.total,
        "disk_used_bytes": disk.used,
        "disk_free_bytes": disk.free,
        "memory_total_bytes": mem_total,
        "memory_used_bytes": max(0, mem_total - mem_available),
        "memory_available_bytes": mem_available,
        "analysis_db_bytes": database.stat().st_size if database.exists() else 0,
    }
    return {**summary, **(await health()), "kaggle": campaign}


@app.post("/mf/v1/ai/retry")
async def retry(request: Request):
    uid = await authorize(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected an object")
    with connect() as c:
        if body.get("entry_id"):
            ids = [
                x[0]
                for x in c.execute(
                    "SELECT entry_id FROM analyses WHERE user_id=? AND entry_id=?",
                    (uid, positive_id(body["entry_id"])),
                )
            ]
        else:
            ids = [
                x[0]
                for x in c.execute(
                    "SELECT entry_id FROM analyses WHERE user_id=? AND state IN ('fetch_error','ai_error')",
                    (uid,),
                )
            ]
    for eid in ids:
        update(eid, state="pending", attempts=0, next_try=0, error=None)
    event("retry_requested", detail=str(len(ids)) + " articles")
    return {"queued": len(ids)}


async def list_upstream_headers(request, uid):
    """Reuse the worker token only for this already-authenticated same-user request."""
    supplied = auth_headers(request)
    token = os.environ.get("MINIFLUX_API_KEY")
    if (
        not token
        or not supplied.get("authorization", "").lower().startswith("basic ")
        or supplied.get("x-auth-token")
    ):
        return supplied
    worker = {"X-Auth-Token": token}
    try:
        response = await app.state.client.get(MF + "/v1/me", headers=worker, timeout=8)
        response.raise_for_status()
        identity = response.json()
        if (
            isinstance(identity, dict)
            and type(identity.get("id")) is int
            and identity["id"] == uid
        ):
            return worker
    except (httpx.HTTPError, ValueError):
        access_log.warning("ai-news list worker identity unavailable; retaining user authentication")
    return supplied


async def require_readable_entry(request, uid, entry_id, *, upstream_headers=None):
    """Resolve a real Miniflux entry using the authenticated user's reader identity."""
    headers = upstream_headers or await list_upstream_headers(request, uid)
    try:
        response = await app.state.client.get(
            MF + f"/v1/entries/{entry_id}", headers=headers, timeout=8
        )
    except httpx.HTTPError:
        raise HTTPException(503, "阅读后端不可用")
    if response.status_code in {401, 403, 404}:
        raise HTTPException(404, "Article not found")
    if response.status_code != 200:
        raise HTTPException(503, "阅读后端不可用")
    try:
        entry = response.json()
    except ValueError:
        raise HTTPException(503, "阅读后端返回了无效文章数据")
    if not isinstance(entry, dict) or entry.get("id") != entry_id:
        raise HTTPException(404, "Article not found")
    if entry.get("user_id") is not None and entry.get("user_id") != uid:
        raise HTTPException(404, "Article not found")
    return entry


def entry_published_timestamp(entry):
    from datetime import datetime

    value = entry.get("published_at")
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return 0.0


async def note_entries(request, uid, allowed_ids, upstream_headers, *, feed_id=None, category_id=None):
    """List user notes without requiring an AI-analysis row for the article."""
    p = request.query_params
    try:
        limit = max(1, min(100, int(p.get("limit", 40))))
        offset = max(0, int(p.get("offset", 0)))
    except ValueError:
        raise HTTPException(400, "Invalid AI filter")
    direction = p.get("direction", "desc")
    sort_key = p.get("ai_sort") or "note_updated"
    if direction not in {"asc", "desc"} or sort_key not in {"note_updated", "time"}:
        raise HTTPException(400, "Invalid sort field or direction")

    bounds = []
    for key, op in [("published_after", ">="), ("published_before", "<="), ("after", ">="), ("before", "<=")]:
        if p.get(key):
            try:
                bounds.append((op, int(p[key])))
            except (ValueError, OverflowError):
                raise HTTPException(400, "Invalid date filter")

    with connect() as c:
        notes = [
            dict(row)
            for row in c.execute(
                """SELECT entry_id,note,updated_at FROM entry_notes
                   WHERE user_id=? AND length(trim(note))>0""",
                (uid,),
            )
            if row["entry_id"] in allowed_ids
        ]
    if not notes:
        return {"total": 0, "entries": []}

    feeds_response = await app.state.client.get(MF + "/v1/feeds", headers=upstream_headers)
    feeds_response.raise_for_status()
    feeds = feeds_response.json()
    hidden = {
        int(feed["id"])
        for feed in feeds
        if feed.get("hide_globally") or (feed.get("category") or {}).get("hide_globally")
    }
    scope_feed_ids = None
    if feed_id is not None:
        scope_feed_ids = {int(feed_id)}
    elif category_id is not None:
        scope_feed_ids = {
            int(feed["id"])
            for feed in feeds
            if int((feed.get("category") or {}).get("id", 0)) == int(category_id)
        }

    semaphore = asyncio.Semaphore(8)

    async def fetch_note(row):
        async with semaphore:
            try:
                entry = await require_readable_entry(
                    request, uid, row["entry_id"], upstream_headers=upstream_headers
                )
            except HTTPException as exc:
                if exc.status_code == 404:
                    return None
                raise
            return row, entry

    pairs = [item for item in await asyncio.gather(*(fetch_note(row) for row in notes)) if item]
    term = (p.get("search") or "")[:200].casefold()
    filtered = []
    for row, entry in pairs:
        current_feed = int(entry.get("feed_id") or (entry.get("feed") or {}).get("id", 0))
        if scope_feed_ids is not None and current_feed not in scope_feed_ids:
            continue
        if p.get("globally_visible") == "true" and current_feed in hidden:
            continue
        published = entry_published_timestamp(entry)
        if any((op == ">=" and published < bound) or (op == "<=" and published > bound) for op, bound in bounds):
            continue
        if term and term not in str(entry.get("title", "")).casefold() and term not in row["note"].casefold():
            continue
        filtered.append((row, entry, published))

    reverse = direction == "desc"
    if sort_key == "note_updated":
        filtered.sort(key=lambda item: (float(item[0]["updated_at"]), int(item[1]["id"])), reverse=reverse)
    else:
        filtered.sort(key=lambda item: (item[2], int(item[1]["id"])), reverse=reverse)
    total = len(filtered)
    result = []
    for _, entry, _ in filtered[offset : offset + limit]:
        enqueue_cards([entry], priority=30)
        result.append(decorate(entry, uid))
    return {"total": total, "entries": result}


async def ai_entries(request, uid, *, feed_id=None, category_id=None):
    p = request.query_params
    if p.get("ai_view") not in ["recommended", "pending", "notes"]:
        raise HTTPException(400, "Invalid AI view")
    if p.get("status") and p["status"] not in ["read", "unread"]:
        raise HTTPException(400, "Invalid status")
    try:
        minimum = float(p.get("ai_min", settings()["minimum_score"]))
        limit = max(1, min(100, int(p.get("limit", 40))))
        offset = max(0, int(p.get("offset", 0)))
        if not 0 <= minimum <= 10:
            raise ValueError()
    except ValueError:
        raise HTTPException(400, "Invalid AI filter")
    has_note = p.get("has_note")
    if has_note not in (None, "true", "false"):
        raise HTTPException(400, "Invalid note filter")
    upstream_headers = await list_upstream_headers(request, uid)
    params = {}
    status = p.get("status")
    if status in ["read", "unread"]:
        params["status"] = status
    if p.get("starred") in ["true", "false"]:
        params["starred"] = p["starred"]
    allowed_ids = set()
    for st in [status] if status in ["read", "unread"] else ["read", "unread"]:
        start = 0
        while True:
            response = await app.state.client.get(
                MF + "/v1/entries/ids",
                headers=upstream_headers,
                params={**params, "status": st, "limit": 10000, "offset": start},
            )
            response.raise_for_status()
            data = response.json()
            ids = data.get("entry_ids", [])
            allowed_ids.update(ids)
            start += len(ids)
            if not ids or start >= data.get("total", 0):
                break
    if p.get("ai_view") == "notes":
        if has_note == "false":
            return {"total": 0, "entries": []}
        return await note_entries(
            request,
            uid,
            allowed_ids,
            upstream_headers,
            feed_id=feed_id,
            category_id=category_id,
        )
    where = ["user_id=?"]
    values = [uid]
    # Respect the same date bounds as the native reader.
    from datetime import datetime, timezone

    for key, op in [
        ("published_after", ">="),
        ("published_before", "<="),
        ("after", ">="),
        ("before", "<="),
    ]:
        if p.get(key):
            try:
                stamp = datetime.fromtimestamp(int(p[key]), timezone.utc).isoformat()
            except (ValueError, OverflowError, OSError):
                raise HTTPException(400, "Invalid date filter")
            where.append(f"julianday(published_at){op}julianday(?)")
            values.append(stamp)
    note_exists_sql = """EXISTS (
            SELECT 1 FROM entry_notes n
            WHERE n.entry_id=analyses.entry_id AND n.user_id=analyses.user_id
              AND length(trim(n.note))>0
        )"""
    if p.get("ai_view") == "pending":
        where.append("state NOT IN ('done','removed')")
    elif p.get("ai_view") == "notes":
        where.append(note_exists_sql)
    else:
        # minimum_score is the effective user threshold. Requiring
        # worth_reading=true here would make scores 4-6 impossible to surface.
        where += ["state='done'", "score>=?"]
        values.append(minimum)

    if has_note == "true" and p.get("ai_view") != "notes":
        where.append(note_exists_sql)
    elif has_note == "false":
        where.append("NOT " + note_exists_sql)

    if p.get("search"):
        term = "%" + p["search"][:200] + "%"
        if p.get("ai_view") == "notes":
            where.append("""(title LIKE ? OR EXISTS (
                SELECT 1 FROM entry_notes n
                WHERE n.entry_id=analyses.entry_id AND n.user_id=analyses.user_id
                  AND n.note LIKE ?
            ))""")
            values += [term, term]
        elif has_note == "true":
            where.append("""(title LIKE ? OR result LIKE ? OR EXISTS (
                SELECT 1 FROM entry_notes n
                WHERE n.entry_id=analyses.entry_id AND n.user_id=analyses.user_id
                  AND n.note LIKE ?
            ))""")
            values += [term, term, term]
        else:
            where.append("(title LIKE ? OR result LIKE ?)")
            values += [term, term]

    sort_key = p.get("ai_sort")
    direction = p.get("direction", "desc")
    if direction not in {"asc", "desc"}:
        raise HTTPException(400, "Invalid sort field or direction")
    if p.get("ai_view") == "pending":
        sort_key = "time"
    elif p.get("ai_view") == "notes" and sort_key is None:
        sort_key = "note_updated"
    else:
        sort_key = sort_key or "score"
    allowed_sorts = {"score", "technical", "business", "time"}
    if p.get("ai_view") == "notes" or has_note == "true":
        allowed_sorts.add("note_updated")
    if sort_key not in allowed_sorts:
        raise HTTPException(400, "Invalid sort field or direction")
    order = {
        "score": "score",
        "technical": "technical_score",
        "business": "business_score",
        "time": "julianday(published_at)",
        "note_updated": """COALESCE((SELECT n.updated_at FROM entry_notes n
                   WHERE n.entry_id=analyses.entry_id AND n.user_id=analyses.user_id),0)""",
    }[sort_key]
    sql_direction = direction.upper()
    with connect() as c:
        candidates = [
            dict(x)
            for x in c.execute(
                "SELECT entry_id,feed_id FROM analyses WHERE "
                + " AND ".join(where)
                + " ORDER BY "
                + order
                + f" {sql_direction},entry_id {sql_direction}",
                values,
            )
        ]
    feeds_response = await app.state.client.get(
        MF + "/v1/feeds", headers=upstream_headers
    )
    feeds_response.raise_for_status()
    feeds = feeds_response.json()
    hidden = {
        f["id"] for f in feeds
        if f.get("hide_globally") or (f.get("category") or {}).get("hide_globally")
    }
    scope_feed_ids = None
    if feed_id is not None:
        scope_feed_ids = {int(feed_id)}
    elif category_id is not None:
        scope_feed_ids = {
            int(f["id"]) for f in feeds
            if int((f.get("category") or {}).get("id", 0)) == int(category_id)
        }
    ids = [
        x["entry_id"]
        for x in candidates
        if x["entry_id"] in allowed_ids
        and (scope_feed_ids is None or x["feed_id"] in scope_feed_ids)
        and (p.get("globally_visible") != "true" or x["feed_id"] not in hidden)
    ]
    semaphore = asyncio.Semaphore(8)

    async def fetch_entry(eid):
        async with semaphore:
            r = await app.state.client.get(
                MF + f"/v1/entries/{eid}", headers=upstream_headers
            )
            r.raise_for_status()
            raw_entry = r.json()
            enqueue_cards([raw_entry], priority=30)
            item = decorate(raw_entry, uid)
            if p.get("ai_view") == "recommended":
                ai = item.setdefault("ai", {})
                if not ai.get("cover_url"):
                    cover = first_image_src(item.get("content", ""))
                    if cover:
                        ai["cover_url"] = cover
                        ai["cover_source"] = "extracted_content"
                item["content"] = ""
                item["content_deferred"] = True
            return item

    entries = await asyncio.gather(
        *(fetch_entry(eid) for eid in ids[offset : offset + limit])
    )
    return {"total": len(ids), "entries": entries}


@app.post("/mf/v1/ai/feedback")
async def feedback(request: Request):
    uid = await authorize(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected an object")
    value = body.get("value")
    eid = positive_id(body.get("entry_id", 0))
    if value not in ["useful", "not_relevant"]:
        raise HTTPException(400, "Invalid feedback")
    with connect() as c:
        if not c.execute(
            "SELECT 1 FROM analyses WHERE entry_id=? AND user_id=?", (eid, uid)
        ).fetchone():
            raise HTTPException(404)
        c.execute(
            "INSERT OR REPLACE INTO feedback VALUES (?,?,?)", (eid, value, time.time())
        )
    return {"saved": True}


@app.post("/mf/v1/ai/reading-session")
async def reading_session(request: Request):
    uid = await authorize(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected an object")
    action = body.get("action")
    if action not in ["open", "heartbeat", "close"]:
        raise HTTPException(400, "Invalid reading action")
    try:
        sid = str(uuid.UUID(str(body.get("session_id", ""))))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(400, "Invalid reading session id")
    eid = positive_id(body.get("entry_id", 0))
    try:
        active_ms = max(0, min(24 * 60 * 60 * 1000, int(body.get("active_ms", 0))))
        scroll = max(0.0, min(100.0, float(body.get("max_scroll_pct", 0))))
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(400, "Invalid reading metrics")
    starred = body.get("starred")
    if starred is not None and not isinstance(starred, bool):
        raise HTTPException(400, "Invalid starred state")
    read_status = body.get("read_status")
    if read_status not in [None, "read", "unread"]:
        raise HTTPException(400, "Invalid read status")
    if not math.isfinite(float(body.get("max_scroll_pct", 0))):
        raise HTTPException(400, "Invalid reading metrics")
    now = time.time()
    with connect() as c:
        if not c.execute("SELECT 1 FROM analyses WHERE entry_id=? AND user_id=?", (eid, uid)).fetchone():
            raise HTTPException(404, "Article not found")
        previous = c.execute("SELECT * FROM reading_sessions WHERE session_id=?", (sid,)).fetchone()
        if previous and (previous['user_id'] != uid or previous['entry_id'] != eid):
            raise HTTPException(409, "Reading session belongs to a different article")
        if action == "open":
            active_ms = 0
        elif not previous:
            raise HTTPException(409, "Open reading session first")
        else:
            # Never accept more active time than wall time since server-observed open.
            active_ms = min(active_ms, max(0, int((now - previous['opened_at']) * 1000)))
        c.execute(
            """INSERT OR IGNORE INTO reading_sessions
               (session_id,user_id,entry_id,opened_at,last_seen_at)
               VALUES (?,?,?,?,?)""",
            (sid, uid, eid, now, now),
        )
        c.execute(
            """UPDATE reading_sessions
               SET last_seen_at=?,
                   closed_at=CASE WHEN ?='close' THEN ? ELSE closed_at END,
                   active_ms=MAX(active_ms,?),
                   max_scroll_pct=MAX(max_scroll_pct,?),
                   starred=COALESCE(?,starred),
                   read_status=COALESCE(?,read_status)
               WHERE session_id=? AND user_id=? AND entry_id=?""",
            (
                now,
                action,
                now,
                active_ms,
                scroll,
                None if starred is None else int(starred),
                read_status,
                sid,
                uid,
                eid,
            ),
        )
    return {"saved": True}


@app.get("/mf/v1/ai/notes/{entry_id}")
async def get_note(entry_id: int, request: Request):
    uid = await authorize(request)
    eid = positive_id(entry_id)
    await require_readable_entry(request, uid, eid)
    with connect() as c:
        row = c.execute(
            "SELECT note,created_at,updated_at FROM entry_notes WHERE user_id=? AND entry_id=?",
            (uid, eid),
        ).fetchone()
    return {
        "entry_id": eid,
        "note": row["note"] if row else "",
        "created_at": row["created_at"] if row else None,
        "updated_at": row["updated_at"] if row else None,
        "has_note": bool(row and row["note"].strip()),
    }


@app.put("/mf/v1/ai/notes/{entry_id}")
async def put_note(entry_id: int, request: Request):
    uid = await authorize(request)
    eid = positive_id(entry_id)
    body = await request.json()
    if (
        not isinstance(body, dict)
        or set(body) != {"note"}
        or not isinstance(body.get("note"), str)
    ):
        raise HTTPException(400, "Expected a note string")
    note = body["note"].replace("\r\n", "\n").replace("\r", "\n")
    if "\x00" in note or len(note) > 20000:
        raise HTTPException(400, "笔记最多 20000 字符，且不能包含空字符")
    now = time.time()
    await require_readable_entry(request, uid, eid)
    with connect() as c:
        if not note.strip():
            c.execute("DELETE FROM entry_notes WHERE user_id=? AND entry_id=?", (uid, eid))
            return {"saved": True, "entry_id": eid, "has_note": False, "updated_at": now}
        c.execute(
            """INSERT INTO entry_notes(user_id,entry_id,note,created_at,updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id,entry_id) DO UPDATE SET
                 note=excluded.note,updated_at=excluded.updated_at""",
            (uid, eid, note, now, now),
        )
    return {"saved": True, "entry_id": eid, "has_note": True, "updated_at": now}


@app.delete("/mf/v1/ai/notes/{entry_id}")
async def delete_note(entry_id: int, request: Request):
    uid = await authorize(request)
    eid = positive_id(entry_id)
    await require_readable_entry(request, uid, eid)
    with connect() as c:
        c.execute("DELETE FROM entry_notes WHERE user_id=? AND entry_id=?", (uid, eid))
    return {"saved": True, "entry_id": eid, "has_note": False}


@app.get("/mf/v1/ai/catalog")
async def catalog(request: Request):
    await authorize(request, admin=True)
    p = ROOT / "sources.catalog.json"
    rows = json.loads(p.read_text()) if p.exists() else []
    r = await app.state.client.get(MF + "/v1/feeds", headers=auth_headers(request))
    r.raise_for_status()
    feeds = {x["feed_url"]: x for x in r.json()}
    catalog_urls = {item["url"] for item in rows}
    # Manual subscriptions and renamed/redirected feeds must be inspectable too.
    rows.extend({
        "name": feed.get("title") or feed["feed_url"],
        "url": feed["feed_url"],
        "category": (feed.get("category") or {}).get("title") or "手动来源",
        "status": "subscribed",
    } for url, feed in feeds.items() if url not in catalog_urls)
    for item in rows:
        item["analysis_supported"] = item.get("category") != "论文与前沿"
        feed = feeds.get(item["url"], {})
        item.update(
            subscribed=bool(feed),
            feed_id=feed.get("id"),
            live_error=feed.get("parsing_error_message", ""),
            disabled=feed.get("disabled", False),
        )
    return rows


@app.get("/mf/v1/ai/feeds/{feed_id}/history")
async def source_history(feed_id: int, request: Request):
    from feed_history import probe_feed, stored_history

    uid = await authorize(request, admin=True)
    feed_id = positive_id(feed_id)
    headers = auth_headers(request)
    try:
        response = await app.state.client.get(
            f"{MF}/v1/feeds/{feed_id}", headers=headers, timeout=8,
        )
        if response.status_code in (403, 404):
            raise HTTPException(404, "来源不存在或不可访问")
        response.raise_for_status()
        feed = response.json()
        if feed.get("id") != feed_id or feed.get("user_id", uid) != uid:
            raise HTTPException(404, "来源不存在或不可访问")
    except (httpx.HTTPError, ValueError, AttributeError):
        raise HTTPException(502, "暂时无法读取来源")
    stored, exposed = await asyncio.gather(
        stored_history(app.state.client, MF, headers, feed_id),
        probe_feed(feed, uid),
    )
    return {"feed_id": feed_id, "stored": stored, "feed_window": exposed,
            "archive_complete": False}


@app.get("/mf/v1/ai/tools")
async def get_tools(request: Request):
    await authorize(request, admin=True)
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE name='tools'").fetchone()
    if row:
        return json.loads(row[0])
    p = ROOT / "tools.catalog.json"
    return json.loads(p.read_text()) if p.exists() else []


@app.put("/mf/v1/ai/tools")
async def put_tools(request: Request):
    from urllib.parse import urlparse

    await authorize(request, admin=True)
    body = await request.json()
    if not isinstance(body, list) or len(body) > 100:
        raise HTTPException(400, "工具列表格式错误")
    result = []
    for item in body:
        if not isinstance(item, dict):
            raise HTTPException(400, "Invalid tool item")
        url = str(item.get("url", ""))
        u = urlparse(url)
        if (
            u.scheme not in ["http", "https"]
            or not u.hostname
            or u.username
            or u.password
        ):
            raise HTTPException(400, "工具链接必须是网页 URL")
        result.append({"name": str(item.get("name", ""))[:100], "url": url[:1000]})
    with connect() as c:
        c.execute(
            "INSERT OR REPLACE INTO settings VALUES ('tools',?)",
            (json.dumps(result, ensure_ascii=False),),
        )
    return {"saved": len(result)}


@app.api_route("/mf/cache-reset", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def cache_reset():
    """Escape an obsolete /inbox/ service worker without touching auth/data."""
    html = """<!doctype html><meta charset="utf-8">
<title>正在更新个人信息箱</title>
<body style="font-family:sans-serif;padding:2rem">正在切换到最新版…</body>
<script>
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.getRegistrations()
    .then(regs => regs
      .filter(r => r.scope.endsWith("/inbox/"))
      .forEach(r => r.unregister()))
    .catch(() => {});
}
setTimeout(() => location.replace("/inbox/?updated=1"), 350);
</script>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/mf/v1/ai/x/roster")
async def x_roster(request: Request):
    await authorize(request, admin=True)
    path = ROOT / "x_sources.catalog.json"
    data = json.loads(path.read_text()) if path.exists() else {"version": 1, "sources": []}
    sources = data.get("sources", [])
    return {
        **data,
        "counts": {
            "total": len(sources),
            "x_active": sum(x.get("x_status") == "active" for x in sources),
            "timeline_nonempty": sum(x.get("timeline_status") == "nonempty" for x in sources),
            "timeline_empty": sum(x.get("timeline_status") == "empty" for x in sources),
            "empty_with_fallback": sum(
                x.get("timeline_status") == "empty" and x.get("status") == "fallback_active"
                for x in sources
            ),
            "profile_unavailable": sum(x.get("x_status") == "profile_unavailable" for x in sources),
            "fallback_active": sum(x.get("status") == "fallback_active" for x in sources),
        },
    }


@app.post("/mf/v1/ai/x/probe")
async def x_probe(request: Request):
    await authorize(request)
    from x_source import probe
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected an object")
    try:
        return await probe(body.get("handle"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/mf/v1/ai/subscribe")
async def subscribe(request: Request):
    await authorize(request, admin=True)
    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("category_id"), int):
        raise HTTPException(400, "Invalid subscription")
    url = str(body.get("url", ""))
    if body.get("x_handle"):
        from x_source import probe
        try:
            checked = await probe(body["x_handle"])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if not checked.get("feed_ready") or not checked.get("profile_valid"):
            return JSONResponse({"error_message": checked["message"], "probe": checked}, status_code=409)
        url = checked["feed_url"]
    from urllib.parse import urlparse

    if urlparse(url).scheme not in ["http", "https"]:
        raise HTTPException(400, "Invalid feed URL")
    response = await app.state.client.post(
        MF + "/v1/feeds",
        headers=auth_headers(request),
        json={
            "feed_url": url,
            "category_id": int(body["category_id"]),
            "crawler": bool(body.get("crawler", False)),
        },
        timeout=70,
    )
    return Response(
        response.content,
        status_code=response.status_code,
        media_type="application/json",
    )


@app.api_route(
    "/mf/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(path: str, request: Request):
    try:
        if request.method not in ("GET", "HEAD", "OPTIONS") and path.startswith(("v1/feeds", "v1/import")):
            await authorize(request, admin=True)
        ai_scope = None
        if request.method == "GET" and request.query_params.get("ai_view"):
            if path == "v1/entries":
                ai_scope = {}
            else:
                scoped = re.fullmatch(r"v1/(feeds|categories)/(\d+)/entries", path)
                if scoped:
                    key = "feed_id" if scoped.group(1) == "feeds" else "category_id"
                    ai_scope = {key: int(scoped.group(2))}
        if ai_scope is not None:
            uid = await authorize(request)
            data = await ai_entries(request, uid, **ai_scope)
            content = json.dumps(data, ensure_ascii=False).replace(
                "http://127.0.0.1:8092/mf", "/mf"
            ).encode()
            return Response(
                content,
                media_type="application/json",
            )
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower()
            in [
                "authorization",
                "x-auth-token",
                "content-type",
                "accept",
                "cookie",
                "if-none-match",
                "if-modified-since",
                "referer",
                "origin",
            ]
        }
        headers["host"] = request.headers.get("host", "127.0.0.1:8092")
        r = await app.state.client.request(
            request.method,
            MF + "/" + path,
            params=request.query_params,
            headers=headers,
            content=await request.body(),
        )
        content = r.content
        content_type = r.headers.get("content-type", "application/octet-stream")
        if (
            r.status_code == 200
            and content_type.startswith("application/json")
            and path.startswith("v1/")
        ):
            data = r.json()
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                enqueue_cards(data["entries"], priority=30)
                data["entries"] = [decorate(e, e["user_id"]) for e in data["entries"]]
            elif (
                isinstance(data, dict)
                and "content" in data
                and "user_id" in data
                and "id" in data
            ):
                enqueue_cards([data], priority=40)
                data = decorate(data, data["user_id"], include_source_fallback=True)
            content = json.dumps(data, ensure_ascii=False).encode()
        if "json" in content_type or "text/html" in content_type:
            content = content.replace(b"http://127.0.0.1:8092/mf", b"/mf")
        keep = {
            k: v
            for k, v in r.headers.items()
            if k.lower()
            in ["content-type", "cache-control", "last-modified", "location"]
        }
        if "location" in keep:
            keep["location"] = keep["location"].replace(
                "http://127.0.0.1:8092/mf", "/mf"
            )
        response = Response(content, status_code=r.status_code, headers=keep)
        for cookie in r.headers.get_list("set-cookie"):
            response.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))
        return response
    except httpx.HTTPError:
        return JSONResponse(
            {
                "error_message": "阅读后端不可用：请先完成服务器上的 Miniflux 初始化。详见 /deployment。"
            },
            status_code=503,
        )


@app.get("/deployment")
async def deployment():
    return FileResponse(
        ROOT / "docs/ops/deployment.html", headers={"Cache-Control": "no-store"}
    )


@app.api_route("/", methods=["GET", "HEAD"])
@app.api_route("/inbox", methods=["GET", "HEAD"])
async def home():
    from fastapi.responses import RedirectResponse

    return RedirectResponse("/inbox/", status_code=308)


@app.api_route("/inbox/{path:path}", methods=["GET", "HEAD"])
async def frontend(path: str, request: Request):
    webroot = (ROOT / "upstream/reactflux/build").resolve()
    if any(part.startswith(".") for part in Path(path).parts):
        raise HTTPException(404)
    target = (webroot / path).resolve()
    if not target.is_relative_to(webroot):
        raise HTTPException(404)
    if target.is_file():
        headers = {"Vary": "Accept-Encoding"}
        if path in {"sw.js", "registerSW.js", "manifest.webmanifest"}:
            headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        elif path.startswith(("assets/", "fonts/")):
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        accepts_gzip = accepts_gzip_encoding(request.headers.get("accept-encoding", ""))
        gz_target = target.with_name(target.name + ".gz")
        if accepts_gzip and gz_target.is_file():
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
            media_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            return FileResponse(gz_target, media_type=media_type, headers=headers)
        return FileResponse(target, headers=headers)
    index = webroot / "index.html"
    if Path(path).suffix:
        raise HTTPException(404)
    if index.exists():
        return FileResponse(index, headers={"Cache-Control": "no-store"})
    return JSONResponse(
        {"error_message": "Frontend build not available"}, status_code=503
    )


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({"error_message": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(json.JSONDecodeError)
async def invalid_json(request, exc):
    return JSONResponse({"error_message": "请求不是有效 JSON"}, status_code=400)


def positive_id(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        n = int(value)
        if n < 1:
            raise ValueError()
        return n
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(400, "Invalid entry id")


from month_control import router as month_control_router
app.include_router(month_control_router)

