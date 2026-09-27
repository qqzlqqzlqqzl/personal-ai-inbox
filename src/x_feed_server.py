"""Local X-to-Atom adapter backed by x-cli guest reads."""
import json, os, re, socket, time
from datetime import datetime, timezone
from html import escape, unescape
from pathlib import Path
from urllib.parse import quote
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response

ROOT = Path(os.environ.get("AI_NEWS_ROOT", "/home/ubuntu/ai-news"))
ROSTER = ROOT / "x_sources.catalog.json"
CACHE_DIR = ROOT / "state" / "x-feed-cache"
PROVIDER = os.environ.get("X_PROVIDER_URL", "http://127.0.0.1:17910").rstrip("/")
CACHE_TTL = int(os.environ.get("X_FEED_CACHE_TTL", "7200"))
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
def allowed_handles():
    try:
        data = json.loads(ROSTER.read_text())
    except (OSError, ValueError):
        return {}
    return {str(x.get("handle", "")).casefold(): x for x in data.get("sources", [])}

def cache_path(handle):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / (handle.casefold() + ".json")

def load_cache(handle):
    try:
        data = json.loads(cache_path(handle).read_text())
        if isinstance(data, dict) and isinstance(data.get("tweets"), list):
            return data
    except (OSError, ValueError):
        pass
    return None

def save_cache(handle, tweets):
    payload = {"fetched_at": time.time(), "handle": handle, "tweets": tweets}
    target = cache_path(handle)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(target)
    return payload
async def fetch_timeline(handle):
    url = PROVIDER + "/v1/timeline/" + quote(handle, safe="")
    async with httpx.AsyncClient(timeout=35, trust_env=False) as client:
        response = await client.get(url)
        response.raise_for_status()
    tweets = []
    for line in response.text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        author = row.get("author") or {}
        if row.get("kind") != "tweet":
            continue
        if str(author.get("username", "")).casefold() != handle.casefold():
            continue
        tweets.append(row)
    tweets.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
    return tweets[:20]

async def profile_exists(handle):
    url = PROVIDER + "/v1/user/" + quote(handle, safe="")
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        response = await client.get(url)
    return response.status_code == 200

async def timeline(handle, force=False):
    cached = load_cache(handle)
    if not force and cached and time.time() - float(cached.get("fetched_at", 0)) < CACHE_TTL:
        return cached, "fresh-cache"
    try:
        tweets = await fetch_timeline(handle)
        return save_cache(handle, tweets), "live"
    except (httpx.HTTPError, ValueError):
        if cached:
            return cached, "stale-cache"
        if await profile_exists(handle):
            return {"fetched_at": 0, "handle": handle, "tweets": []}, "deferred-empty"
        raise
def atom_time(value=None):
    if value:
        return str(value)
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def media_html(tweet):
    out = []
    for item in tweet.get("media") or []:
        url = str(item.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        if item.get("type") in {"photo", "video", "animated_gif"}:
            out.append(f'<p><img src="{escape(url, quote=True)}" alt=""></p>')
    return "".join(out)

def render_atom(handle, tweets):
    updated = atom_time(tweets[0].get("created_at")) if tweets else atom_time()
    parts = ['<?xml version="1.0" encoding="utf-8"?>',
             '<feed xmlns="http://www.w3.org/2005/Atom">']
    parts += [f"<id>tag:personal-ai-inbox.local,x:{escape(handle)}</id>",
              f"<title>X · @{escape(handle)}</title>",
              f"<updated>{escape(updated)}</updated>",
              f'<link rel="alternate" href="https://x.com/{escape(handle, quote=True)}"/>',
              f'<link rel="self" href="http://127.0.0.1:17911/x/user/{escape(handle, quote=True)}"/>']
    for tweet in tweets:
        text = unescape(str(tweet.get("text") or "")).strip()
        first = " ".join(text.split())[:140] or ("X post " + str(tweet.get("id", "")))
        url = str(tweet.get("url") or f"https://x.com/{handle}/status/{tweet.get('id','')}")
        created = atom_time(tweet.get("created_at"))
        author = tweet.get("author") or {}
        name = str(author.get("name") or handle)
        body = escape(text).replace("\n", "<br>") + media_html(tweet)
        parts += ["<entry>",
                  f"<id>tag:x.com,tweet:{escape(str(tweet.get('id','')))}</id>",
                  f"<title>{escape(first)}</title>",
                  f'<link rel="alternate" href="{escape(url, quote=True)}"/>',
                  f"<published>{escape(created)}</published>",
                  f"<updated>{escape(created)}</updated>",
                  "<author>",
                  f"<name>{escape(name)} (@{escape(handle)})</name>",
                  "</author>",
                  f'<summary type="text">{escape(text[:500])}</summary>',
                  f'<content type="html">{escape(body)}</content>',
                  "</entry>"]
    parts.append("</feed>")
    return "".join(parts).encode("utf-8")

@app.get("/x/user/{handle}")
async def x_feed(handle: str, refresh: bool = False):
    if not HANDLE_RE.fullmatch(handle):
        raise HTTPException(400, "invalid X handle")
    allowed = allowed_handles()
    item = allowed.get(handle.casefold())
    canonical = str((item or {}).get("handle") or handle)
    try:
        data, source = await timeline(canonical, force=refresh)
    except httpx.HTTPError as exc:
        raise HTTPException(503, type(exc).__name__)
    body = render_atom(canonical, data["tweets"])
    return Response(body, media_type="application/atom+xml",
                    headers={"Cache-Control": "private, max-age=300",
                             "X-X-Feed-Source": source,
                             "X-X-Feed-Items": str(len(data["tweets"]))})

@app.get("/healthz")
async def health():
    provider_up = False
    try:
        with socket.create_connection(("127.0.0.1", 17910), timeout=1):
            provider_up = True
    except OSError:
        pass
    handles = allowed_handles()
    cached = len(list(CACHE_DIR.glob("*.json"))) if CACHE_DIR.exists() else 0
    result = {"ready": provider_up and bool(handles), "provider": provider_up,
              "roster": len(handles), "cached_handles": cached}
    return JSONResponse(result, status_code=200 if result["ready"] else 503)
