"""Determine the actual input source; never treat a blog RSS summary as full text."""
from work_admission import check,options,http_client,AdmissionStopped

import os
from urllib.parse import urlsplit
from bs4 import BeautifulSoup

SOCIAL_ROUTES = ("/telegram/channel/", "/twitter/user/", "/instagram/2/")


def is_our_social_feed(feed_url):
    u = urlsplit(feed_url)
    if u.scheme != "http" or u.hostname != "127.0.0.1" or u.username:
        return False
    rsshub_social = u.port == 1200 and u.path.startswith(SOCIAL_ROUTES)
    x_guest_feed = u.port == 17911 and u.path.startswith("/x/user/")
    return rsshub_social or x_guest_feed


def content_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    return soup.get_text(" ", strip=True), len(soup.find_all("img"))


def _safe_image_url(src, base_url=None):
    from urllib.parse import urljoin

    src = str(src or "").strip()
    if not src or src.startswith(("data:", "blob:")):
        return None
    if src.startswith("/mf/proxy/"):
        return src[:4000]
    absolute = urljoin(base_url or "", src)
    parsed = urlsplit(absolute)
    if parsed.scheme in ("http", "https") and parsed.hostname and not parsed.username:
        return absolute[:4000]
    return None


def _image_is_decorative(img, src):
    # Miniflux rewrites image URLs. Inspect the encoded original for decorations too.
    import base64
    if "/mf/proxy/" in (src or ""):
        try:
            encoded = urlsplit(src).path.rsplit("/", 1)[-1]
            src += " " + base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8")
        except (ValueError, UnicodeError):
            pass
    text = " ".join(
        [
            src or "",
            " ".join(img.get("class", []) if isinstance(img.get("class"), list) else []),
            str(img.get("id", "")),
            str(img.get("alt", "")),
        ]
    ).lower()
    bad = (
        "avatar",
        "gravatar",
        "favicon",
        "logo",
        "emoji",
        "sprite",
        "/panda/",
        "/counter/",
        "/doc/gopher/",
        "/static/images/rust-social",
        "/img/featured/featured-espressif",
    )
    if any(token in text for token in bad):
        return True
    try:
        width = float(img.get("width") or 0)
        height = float(img.get("height") or 0)
        if (width and width <= 2) or (height and height <= 2) or (width and height and max(width, height) <= 180):
            return True
    except (TypeError, ValueError):
        pass
    return False


def first_image_src(html):
    """Pick the first non-decorative image already present in extracted HTML."""
    soup = BeautifulSoup(html or "", "html.parser")
    for img in soup.find_all("img"):
        src = _safe_image_url(img.get("src"))
        if src and not _image_is_decorative(img, src):
            return src
    return None


def select_cover_from_page(raw_html, final_url, title="", prefer_social=False):
    """Prefer explicit article heroes; never use a global header/banner as fallback."""
    soup = BeautifulSoup(raw_html, "html.parser")
    scoped = soup.select("article img, main img, [role=main] img")
    candidates = []
    for img in soup.find_all("img"):
        if img.find_parent(["header", "nav", "footer", "aside"]):
            continue
        urls = [img.get("data-src"), img.get("data-lazy-src"), img.get("src")]
        srcset = str(img.get("srcset") or "").strip()
        if srcset:
            srcset_candidates = [candidate.split() for candidate in srcset.split(",") if candidate.strip()]
            if srcset_candidates:
                urls.append(srcset_candidates[-1][0])
        src = next((u for candidate in urls if (u := _safe_image_url(candidate, final_url))
                    and not _image_is_decorative(img, u)), None)
        if not src:
            continue
        alt = str(img.get("alt") or "").strip().casefold()
        classes = " ".join(img.get("class") or []).lower()
        hero = bool(title and alt and title.strip().casefold() in alt) or any(
            word in classes for word in ("hero", "cover", "featured", "post-image"))
        in_article = any(img is item for item in scoped)
        candidates.append((src, hero, in_article))
    for src, hero, in_article in sorted(candidates, key=lambda c: not c[2]):
        if hero and not prefer_social:
            return src, "page_hero"
    for attrs in ({"property": "og:image:secure_url"}, {"property": "og:image"},
                  {"name": "twitter:image"}, {"name": "twitter:image:src"}):
        tag = soup.find("meta", attrs=attrs)
        src = _safe_image_url(tag.get("content") if tag else None, final_url)
        if src and not _image_is_decorative(soup.new_tag("img"), src):
            return src, "social_meta"
    for src, _, in_article in candidates:
        if in_article:
            return src, "page_first_image"
    return None, None


async def _discover_original_cover(entry_url, title="", strict=False, *,admission=None):
    """Fetch the article page and choose its hero image; OG/Twitter is fallback."""
    check(admission)
    import httpx

    url = urlsplit(entry_url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username:
        return None, None
    try:
        async with http_client(admission,
            timeout=12,
            follow_redirects=True,
            max_redirects=3,
            trust_env=False,
            proxy=os.environ.get("AI_NEWS_OUTBOUND_PROXY") or None,
            headers={"User-Agent": "Mozilla/5.0 (compatible; PersonalAIInbox/1.0)"},
        ) as external:
            async with external.stream("GET", entry_url) as response:
                if response.status_code != 200:
                    if strict:
                        response.raise_for_status()
                    return None, None
                chunks = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 3 * 1024 * 1024:
                        return None, None
                    chunks.append(chunk)
                raw = b"".join(chunks)
                final_url = str(response.url)
            selected = select_cover_from_page(raw, final_url, title)
            if not selected[0]:
                return selected
            # Article CDN links can reject hotlinking while its declared social image works.
            choices = [selected, select_cover_from_page(raw, final_url, title, prefer_social=True)]
            seen = set()
            for cover, source in choices:
                if not cover or cover in seen:
                    continue
                seen.add(cover)
                try:
                    async with external.stream("GET", cover, timeout=6) as image:
                        if image.status_code == 200 and image.headers.get("content-type", "").lower().startswith("image/"):
                            return cover, source
                except httpx.HTTPError:
                    continue
            if strict:
                raise ValueError("cover_image_unavailable")
            return None, None
    except AdmissionStopped:
        raise
    except Exception:
        if strict:
            raise
        return None, None


async def discover_original_cover(entry_url, title="", strict=False, *,admission=None):
    check(admission)
    import asyncio
    try:
        return await asyncio.wait_for(_discover_original_cover(entry_url, title, strict,**options(admission)), timeout=18)
    except asyncio.TimeoutError:
        if strict:
            raise
        return None, None


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


async def add_original_cover(client, entry, html, backend_url, headers, *,admission=None):
    """Use the original page's declared cover only when extracted content has no image."""
    check(admission)
    from html import escape
    from urllib.parse import urljoin

    if "<img" in html.lower():
        return html
    url = urlsplit(entry["url"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username:
        return html
    try:
        import httpx

        async with http_client(admission,
            timeout=10, follow_redirects=True, max_redirects=3, trust_env=False,
            proxy=os.environ.get("AI_NEWS_OUTBOUND_PROXY") or None,
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
        check(admission)
        saved = await client.put(
            backend_url + f"/v1/entries/{entry['id']}",
            headers=headers,
            json={"content": enriched},
            timeout=15,
        )
        if saved.status_code != 200:
            return html
        check(admission)
        fresh = await client.get(
            backend_url + f"/v1/entries/{entry['id']}", headers=headers, timeout=15
        )
        return fresh.json().get("content", html) if fresh.status_code == 200 else html
    except AdmissionStopped:
        raise
    except Exception:
        return (
            html  # Cover fetching is optional; it cannot invalidate the original text.
        )
