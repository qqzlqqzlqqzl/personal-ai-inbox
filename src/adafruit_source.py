"""Follow only explicit Adafruit article attribution, retaining the summary on failure.

This adapter is intentionally restricted to reviewed publisher body selectors.
It never follows discussion links, recursively crawls links, or retries analysis.
"""
import asyncio
import hashlib
import os
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

import bleach
import httpx
from bs4 import BeautifulSoup

ADAFRUIT = 'blog.adafruit.com'
SELECTORS = {'medium.com': 'article', 'ep-news.web.cern.ch': '.entry-content.wp-block-post-content'}
MAX_BYTES = 3 * 1024 * 1024
MAX_CHARS = 200000


class OriginalUnavailable(ValueError):
    pass


def checked_url(url, hosts):
    try:
        parsed = urlsplit(url)
        if (parsed.scheme not in ('http', 'https') or parsed.hostname not in hosts
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 80, 443) or '\\' in url
                or any(ord(c) < 32 for c in url) or len(url) > 4000):
            raise ValueError()
    except (TypeError, ValueError):
        raise OriginalUnavailable('unsupported_or_unsafe_url') from None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))


def is_adafruit(url):
    try:
        checked_url(url, {ADAFRUIT})
        return True
    except OriginalUnavailable:
        return False


def see_more_link(html):
    """Match the anchor immediately after ‘See more at’, not a later Via link."""
    soup = BeautifulSoup(html, 'html.parser')
    found = set()
    for anchor in soup.find_all('a', href=True):
        prefix = ''.join(str(node.get_text(' ', strip=True) if hasattr(node, 'get_text') else node)
                         for node in reversed(list(anchor.previous_siblings)))
        label = anchor.get_text(' ', strip=True)
        if not (re.search(r'\bsee\s+more\s+at\s*$', prefix, re.IGNORECASE)
                or re.match(r'^see\s+more\s+at\b', label, re.IGNORECASE)):
            continue
        try:
            found.add(checked_url(anchor['href'], SELECTORS))
        except OriginalUnavailable:
            continue
    return next(iter(found)) if len(found) == 1 else None


def _identity(url):
    parsed = urlsplit(url)
    return parsed.hostname, parsed.path.rstrip('/')


def clean_body(html, base):
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup.select('script,style,noscript,form,button,nav,footer,aside'):
        node.decompose()
    for node in soup.select('pre,code'):
        if node.parent is not None:
            literal = node.get_text('', strip=False)
            node.clear()
            node.append(literal)
    for node in soup.select('[href],[src]'):
        for attr in ('href', 'src'):
            if node.has_attr(attr):
                absolute = urljoin(base, node[attr])
                p = urlsplit(absolute)
                if p.scheme not in ('http', 'https') or p.username is not None or p.password is not None:
                    del node[attr]
                else:
                    node[attr] = absolute
    return bleach.clean(str(soup), tags={'article','section','div','p','span','h1','h2','h3','h4',
        'h5','h6','blockquote','pre','code','ul','ol','li','a','img','figure','figcaption',
        'table','thead','tbody','tr','td','th','strong','em','b','i','br','hr','sup','sub'},
        attributes={'a':['href','title'], 'img':['src','alt','width','height']},
        protocols={'http','https'}, strip=True)


def _text(html):
    return BeautifulSoup(html, 'html.parser').get_text(' ', strip=True)


def extract_original(raw, url, requested, title, summary):
    host = urlsplit(checked_url(url, SELECTORS)).hostname
    soup = BeautifulSoup(raw, 'html.parser')
    canonical = soup.find('link', rel='canonical')
    og = soup.find('meta', property='og:url')
    declared = [node.get(attr) for node, attr in ((canonical,'href'), (og,'content')) if node]
    if not declared or any(_identity(checked_url(urljoin(url, item or ''), SELECTORS))
                           != _identity(requested) for item in declared):
        raise OriginalUnavailable('original_identity_mismatch')
    nodes = soup.select(SELECTORS[host])
    if len(nodes) != 1:
        raise OriginalUnavailable('original_body_missing_or_ambiguous')
    body = clean_body(str(nodes[0]), url)
    text = _text(body)
    if (re.search(r'\b(member.only story|create an account to read|sign in to read this story)\b',
                  soup.get_text(' ', strip=True), re.IGNORECASE)
            or re.search(r'"isAccessibleForFree"\s*:\s*false', str(soup), re.IGNORECASE)):
        raise OriginalUnavailable('original_requires_access')
    if len(text) < max(600, len(_text(summary)) * 1.35) or len(text) > MAX_CHARS:
        raise OriginalUnavailable('original_not_more_complete')
    # Canonical identity alone does not prove a challenge/login page is an article.
    words = lambda value: set(re.findall(r'[a-z0-9]{4,}', value.casefold()))
    expected = words(title)
    heading = ' '.join(n.get_text(' ',strip=True) for n in soup.select('h1'))
    if expected and len(expected & words(heading)) < min(3, len(expected)):
        raise OriginalUnavailable('original_title_mismatch')
    return body, text


async def _get(client, url):
    target = checked_url(url, SELECTORS)
    for redirects in range(4):
        async with client.stream('GET', target, follow_redirects=False) as response:
            if response.is_redirect:
                if redirects == 3:
                    raise OriginalUnavailable('original_redirect_limit')
                next_url = checked_url(urljoin(target, response.headers.get('location', '')), SELECTORS)
                if urlsplit(next_url).hostname != urlsplit(url).hostname:
                    raise OriginalUnavailable('original_cross_site_redirect')
                target = next_url
                continue
            if response.status_code != 200:
                raise OriginalUnavailable('original_http_' + str(response.status_code))
            if 'html' not in response.headers.get('content-type', '').lower():
                raise OriginalUnavailable('original_not_html')
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > MAX_BYTES:
                    raise OriginalUnavailable('original_too_large')
            return bytes(raw), target
    raise OriginalUnavailable('original_redirect_limit')


async def resolve(entry, client=None):
    """Resolve an existing entry; failures keep its original Adafruit content."""
    url = checked_url(entry['url'], {ADAFRUIT})
    summary = entry.get('content') or ''
    if len(summary.encode()) > MAX_BYTES or len(_text(summary)) < 120:
        raise OriginalUnavailable('adafruit_summary_unavailable')
    target = see_more_link(summary)
    receipt = {'requested_url':url, 'url':url, 'source':'adafruit_summary',
               'rule_version':1, 'follow_status':'no_supported_explicit_link'}
    result = {'html':summary, 'source_text':_text(summary), 'receipt':receipt,
              'image_count':len(BeautifulSoup(summary,'html.parser').find_all('img'))}
    if not target:
        receipt.update(chars=len(result['source_text']), body_sha256=hashlib.sha256(result['source_text'].encode()).hexdigest())
        return result
    receipt['target_url'] = target
    try:
        async def retrieve():
            if client is not None:
                return await _get(client, target)
            async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False,
                    proxy=os.environ.get('AI_NEWS_OUTBOUND_PROXY') or None,
                    headers={'User-Agent':'Mozilla/5.0 (compatible; PersonalAIInbox/1.0)'}) as external:
                return await _get(external, target)
        raw, final = await asyncio.wait_for(retrieve(), timeout=45)
        body, text = extract_original(raw, final, target, entry.get('title',''), summary)
        receipt.update(url=final, source='adafruit_linked_original', follow_status='selected',
                       page_sha256=hashlib.sha256(raw).hexdigest())
        result.update(html=body, source_text=text,
                      image_count=len(BeautifulSoup(body,'html.parser').find_all('img')))
    except (ValueError, httpx.InvalidURL, httpx.HTTPError, asyncio.TimeoutError) as exc:
        receipt['follow_status'] = (str(exc) if isinstance(exc,OriginalUnavailable)
            else 'original_invalid_url' if isinstance(exc,(ValueError,httpx.InvalidURL)) else type(exc).__name__)
    receipt.update(chars=len(result['source_text']), body_sha256=hashlib.sha256(result['source_text'].encode()).hexdigest())
    return result
