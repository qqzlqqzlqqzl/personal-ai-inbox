"""Same-origin reader gateway and authenticated AI extension API."""

import asyncio, json, os, time, contextlib, logging, uuid, mimetypes
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
    with connect() as c:
        c.execute(
            "UPDATE analyses SET state='pending' WHERE state IN ('fetching','analyzing')"
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


def auth_headers(request):
    return {
        k: request.headers[k]
        for k in ["authorization", "x-auth-token"]
        if k in request.headers
    }


async def authorize(request):
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
    return r.json()["id"]


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
    await authorize(request)
    return {
        **settings(),
        "api_key_configured": bool(os.environ.get("ARK_API_KEY")),
        "key_management": "server_environment",
    }


@app.put("/mf/v1/ai/settings")
async def put_settings(request: Request):
    await authorize(request)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Expected an object")
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
    uid = await authorize(request)
    return {**status_summary(uid), **(await health())}


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


async def ai_entries(request, uid):
    p = request.query_params
    if p.get("ai_view") not in ["recommended", "pending"]:
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
    if p.get("ai_view") == "pending":
        where.append("state NOT IN ('done','removed')")
    else:
        where += [
            "state='done'",
            "json_extract(result,'$.worth_reading')=1",
            "score>=?",
        ]
        values.append(minimum)
    if p.get("search"):
        where.append("(title LIKE ? OR result LIKE ?)")
        values += ["%" + p["search"][:200] + "%"] * 2
    sort_key = p.get("ai_sort", "score")
    direction = p.get("direction", "desc")
    if sort_key not in {"score", "technical", "business", "time"} or direction not in {"asc", "desc"}:
        raise HTTPException(400, "Invalid sort field or direction")
    # Pending items have no meaningful score. Dates must compare instants, not ISO strings.
    if p.get("ai_view") == "pending":
        sort_key = "time"
    order = {
        "score": "score",
        "technical": "technical_score",
        "business": "business_score",
        "time": "julianday(published_at)",
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
    hidden = {
        f["id"] for f in feeds_response.json()
        if f.get("hide_globally") or (f.get("category") or {}).get("hide_globally")
    }
    ids = [
        x["entry_id"]
        for x in candidates
        if x["entry_id"] in allowed_ids
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


@app.get("/mf/v1/ai/catalog")
async def catalog(request: Request):
    await authorize(request)
    p = ROOT / "sources.catalog.json"
    rows = json.loads(p.read_text()) if p.exists() else []
    r = await app.state.client.get(MF + "/v1/feeds", headers=auth_headers(request))
    r.raise_for_status()
    feeds = {x["feed_url"]: x for x in r.json()}
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


@app.get("/mf/v1/ai/tools")
async def get_tools(request: Request):
    await authorize(request)
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE name='tools'").fetchone()
    if row:
        return json.loads(row[0])
    p = ROOT / "tools.catalog.json"
    return json.loads(p.read_text()) if p.exists() else []


@app.put("/mf/v1/ai/tools")
async def put_tools(request: Request):
    from urllib.parse import urlparse

    await authorize(request)
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
    await authorize(request)
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
        if not checked["posts_returned"]:
            return JSONResponse({"error_message": checked["message"], "probe": checked}, status_code=409)
        url = "http://127.0.0.1:1200" + checked["route"]
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
        if (
            path == "v1/entries"
            and request.method == "GET"
            and request.query_params.get("ai_view")
        ):
            uid = await authorize(request)
            data = await ai_entries(request, uid)
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
                data = decorate(data, data["user_id"])
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
