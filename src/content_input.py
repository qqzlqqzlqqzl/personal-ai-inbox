"""Determine the actual input source; never treat a blog RSS summary as full text."""

from urllib.parse import urlsplit
from bs4 import BeautifulSoup

SOCIAL_ROUTES = ("/telegram/channel/", "/twitter/user/", "/instagram/2/")


def is_our_social_feed(feed_url):
    u = urlsplit(feed_url)
    return (
        u.scheme == "http"
        and u.hostname == "127.0.0.1"
        and u.port == 1200
        and u.path.startswith(SOCIAL_ROUTES)
        and not u.username
    )


def content_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return soup.get_text(" ", strip=True), len(soup.find_all("img"))


def model_payload(entry, text, source, config):
    import json

    used = text[: config["max_chars"]]
    message = json.dumps(
        {
            "title": entry["title"],
            "url": entry["url"],
            "content_source": source,
            "truncated": len(text) > len(used),
            "content": used,
        },
        ensure_ascii=False,
    )
    return used, message


async def add_original_cover(client, entry, html, backend_url, headers):
    """Use the original page's declared cover only when extracted content has no image."""
    from html import escape
    from urllib.parse import urljoin

    if "<img" in html.lower():
        return html
    url = urlsplit(entry["url"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username:
        return html
    try:
        import httpx

        async with httpx.AsyncClient(
            timeout=10, follow_redirects=True, max_redirects=3, trust_env=False
        ) as external:
            async with external.stream("GET", entry["url"]) as r:
                if r.status_code != 200:
                    return html
                chunks = []
                size = 0
                async for chunk in r.aiter_bytes():
                    size += len(chunk)
                    if size > 3 * 1024 * 1024:
                        return html
                    chunks.append(chunk)
                raw = b"".join(chunks)
        soup = BeautifulSoup(raw, "html.parser")
        tag = soup.find("meta", attrs={"property": "og:image"}) or soup.find(
            "meta", attrs={"name": "twitter:image"}
        )
        if not tag or not tag.get("content"):
            return html
        cover = urljoin(str(r.url), tag["content"])
        parsed = urlsplit(cover)
        if parsed.scheme not in ("http", "https") or parsed.username:
            return html
        enriched = '<img src="' + escape(cover, quote=True) + '" alt="">' + html
        saved = await client.put(
            backend_url + f"/v1/entries/{entry['id']}",
            headers=headers,
            json={"content": enriched},
            timeout=15,
        )
        if saved.status_code != 200:
            return html
        fresh = await client.get(
            backend_url + f"/v1/entries/{entry['id']}", headers=headers, timeout=15
        )
        return fresh.json().get("content", html) if fresh.status_code == 200 else html
    except Exception:
        return (
            html  # Cover fetching is optional; it cannot invalidate the original text.
        )
