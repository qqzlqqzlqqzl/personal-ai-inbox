"""Read-only source history: reader storage and a bounded feed-window snapshot.

A feed window is evidence about one fetched document, never archive coverage.
No entry content or credentials are returned to the browser.
"""
import asyncio
import ipaddress
import re
import socket
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

MAX_FEED_BYTES = 2 * 1024 * 1024
SNAPSHOT_TTL = 300
CACHE_LIMIT = 256
_snapshot_cache = {}


def timestamp(value):
    """Normalize a declared timestamp; missing/invalid dates stay unknown."""
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    # Do not invent a timezone for a source that did not declare one.
    if result.tzinfo is None:
        return None
    try:
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def iso(value):
    return value.isoformat().replace("+00:00", "Z") if value else None


def feed_window(data):
    if len(data) > MAX_FEED_BYTES:
        raise ValueError("feed_too_large")
    # Feed documents do not need DTDs or custom XML entities.
    if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", data.replace(b"\x00", b""), re.IGNORECASE):
        raise ValueError("unsupported_xml_declaration")
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, LookupError) as exc:
        raise ValueError("invalid_feed") from exc
    if root.tag == "rss":
        if root.find("channel") is None:
            raise ValueError("invalid_feed")
        entries = root.findall("./channel/item")
        date_tags = ("pubDate", "{http://purl.org/dc/elements/1.1/}date")
    elif root.tag == "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF":
        entries = root.findall("{http://purl.org/rss/1.0/}item")
        date_tags = ("{http://purl.org/dc/elements/1.1/}date",)
    elif root.tag in ("{http://www.w3.org/2005/Atom}feed", "feed"):
        prefix = "{http://www.w3.org/2005/Atom}" if root.tag.startswith("{") else ""
        entries = root.findall(prefix + "entry")
        date_tags = (prefix + "published", prefix + "updated")
    else:
        raise ValueError("unsupported_feed_format")
    dates = []
    updated_fallback_count = 0
    for entry in entries:
        for date_tag in date_tags:
            date = timestamp(entry.findtext(date_tag))
            if date:
                dates.append(date)
                updated_fallback_count += date_tag.endswith("updated")
                break
    oldest, newest = (min(dates), max(dates)) if dates else (None, None)
    return {
        "state": "ok", "count": len(entries), "dated_count": len(dates),
        "undated_count": len(entries) - len(dates),
        "updated_fallback_count": updated_fallback_count,
        "oldest_at": iso(oldest), "newest_at": iso(newest),
        "span_days": round((newest - oldest).total_seconds() / 86400, 2) if dates else None,
    }


async def stored_history(client, backend, headers, feed_id):
    """Ask Miniflux, not the incomplete AI analysis ledger, for retained rows."""
    async def endpoint(direction):
        response = await client.get(
            f"{backend}/v1/feeds/{feed_id}/entries", headers=headers,
            params=[("limit", "1"), ("order", "published_at"), ("direction", direction),
                    ("status", "read"), ("status", "unread"), ("status", "removed")],
            timeout=8,
        )
        response.raise_for_status()
        body = response.json()
        count = body["total"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("invalid_entry_count")
        entries = body["entries"]
        if not isinstance(entries, list) or bool(entries) != bool(count):
            raise ValueError("invalid_entry_page")
        value = timestamp(entries[0].get("published_at")) if entries else None
        return count, iso(value)

    try:
        # Refresh once if collection changed between the two read-only queries.
        for _ in range(2):
            oldest, newest = await asyncio.gather(endpoint("asc"), endpoint("desc"))
            if oldest[0] == newest[0] and (not oldest[1] or not newest[1] or timestamp(oldest[1]) <= timestamp(newest[1])):
                return {
                    "state": "ok", "count": oldest[0],
                    "oldest_published_at": oldest[1], "newest_published_at": newest[1],
                    "includes_removed": True, "checked_at": iso(datetime.now(timezone.utc)),
                }
        return {"state": "changing", "count": None}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return {"state": "unavailable", "count": None}


def supported_url(value):
    """Only read enrolled HTTP feeds; never credentials or other local services."""
    try:
        url = urlsplit(value)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
            return False
        if url.hostname == "127.0.0.1" and url.scheme == "http":
            # Existing, dedicated RSSHub and X guest adapters only.
            return (url.port == 1200 or (url.port == 17911 and url.path.startswith("/x/")))
        if url.hostname.lower().rstrip(".") in ("localhost", "metadata.google.internal") or url.hostname.lower().rstrip(".").endswith((".local", ".localhost")):
            return False
        try:
            return ipaddress.ip_address(url.hostname).is_global
        except ValueError:
            return True
    except ValueError:
        return False


async def resolved_addresses(host, port):
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


async def pinned_destination(value):
    """Pin checked DNS answers so aliases/rebinding cannot reach private services."""
    if not supported_url(value):
        raise ValueError("unsafe_feed_address")
    url = httpx.URL(value)
    if url.host == "127.0.0.1" and urlsplit(value).hostname == "127.0.0.1":
        # supported_url restricts this exact literal to our dedicated adapters.
        return url, {}, {}
    addresses = await resolved_addresses(url.host, url.port or (443 if url.scheme == "https" else 80))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError("unsafe_feed_address")
    # Prefer IPv4 where both families are available, preserving Host and TLS SNI
    # while connecting only to the validated IP. Every redirect repeats this.
    address = next((value for value in addresses if ":" not in value), addresses[0])
    return url.copy_with(host=address), {"Host": url.netloc.decode("ascii")}, {"sni_hostname": url.host}


def feed_client():
    # A separate client is essential: reader Authorization/X-Auth-Token headers
    # and Miniflux per-feed passwords must never reach this probe.
    # Probe directly: proxy-side DNS cannot be pinned safely through this client.
    # Unreachable feeds remain unknown; Miniflux storage queries are unaffected.
    return httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False,
                             limits=httpx.Limits(max_keepalive_connections=0, max_connections=2),
                             headers={"User-Agent": "PersonalReaderFeedHistory/1.0"})


async def probe_feed(feed, user_id):
    checked_at = iso(datetime.now(timezone.utc))
    url = feed.get("feed_url", "")
    if feed.get("username") or feed.get("password") or feed.get("cookie") or not supported_url(url):
        return {"state": "unavailable", "reason": "unsupported_or_authenticated_feed", "checked_at": checked_at}
    key = (user_id, feed["id"], url)
    cached = _snapshot_cache.get(key)
    if cached and time.monotonic() - cached[0] < SNAPSHOT_TTL:
        return {**cached[1], "cached": True}
    try:
        # Bound total elapsed time as well as each socket read. Slow streams
        # cannot keep a source-history request alive indefinitely.
        async with asyncio.timeout(15):
            async with feed_client() as client:
                for redirects in range(4):
                    destination, headers, extensions = await pinned_destination(url)
                    client.cookies.clear()
                    async with client.stream("GET", destination, headers=headers, extensions=extensions) as response:
                        if response.is_redirect:
                            target = str(httpx.URL(url).join(response.headers.get("location", "")))
                            if redirects == 3 or not response.headers.get("location") or not supported_url(target):
                                raise ValueError("unsupported_redirect")
                            # A public feed may not redirect into a local adapter.
                            if urlsplit(url).hostname != "127.0.0.1" and urlsplit(target).hostname == "127.0.0.1":
                                raise ValueError("unsupported_redirect")
                            url = target
                            continue
                        response.raise_for_status()
                        chunks, size = [], 0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_FEED_BYTES:
                                raise ValueError("feed_too_large")
                            chunks.append(chunk)
                        result = {**feed_window(b"".join(chunks)), "checked_at": checked_at, "cached": False}
                        break
    except httpx.HTTPStatusError as exc:
        result = {"state": "unavailable", "reason": f"http_{exc.response.status_code}", "checked_at": checked_at}
    except (httpx.HTTPError, TimeoutError, ValueError, OSError) as exc:
        # Do not echo exception text: it can contain private feed URLs/tokens.
        reason = str(exc) if type(exc) is ValueError and str(exc) in {
            "unsupported_redirect", "feed_too_large", "unsupported_xml_declaration",
            "invalid_feed", "unsupported_feed_format", "unsafe_feed_address",
        } else "fetch_failed"
        result = {"state": "unavailable", "reason": reason, "checked_at": checked_at}
    if len(_snapshot_cache) >= CACHE_LIMIT:
        _snapshot_cache.pop(next(iter(_snapshot_cache)))
    _snapshot_cache[key] = (time.monotonic(), result)
    return result
