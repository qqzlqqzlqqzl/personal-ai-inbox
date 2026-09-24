"""Site-specific article inputs. A missing rule/body is an explicit failure.

Rules were checked against saved publisher HTML, not RSS excerpts. They select
the complete body container; they do not expand links to different articles.
"""
import asyncio
import hashlib
import os
from urllib.parse import urlsplit, urljoin

from bs4 import BeautifulSoup


class FulltextUnavailable(ValueError):
    pass


# selector, removable site furniture, repeated body components allowed
RULES = {
    'www.infoq.cn': ('article .ProseMirror', '', False),
    'blog.st.com': ('article .entry-content', '.wp-block-uagb-table-of-contents', False),
    'techcrunch.com': ('.entry-content.wp-block-post-content', '', False),
    'aws.amazon.com': ('section.blog-post-content', '', False),
    'hackaday.com': ('article .entry-content', '', False),
    'www.tomshardware.com': ('#article-body', '#utility-bar,.slice-container-newsletterForm', False),
    'blog.cloudflare.com': ('.article-content', '', False),
    'www.theverge.com': ('.duet--article--article-body-component', '', True),
    'sspai.com': ('.article__main__content.wangEditor-txt', '', False),
    'www.raspberrypi.com': ('.c-wysiwyg.c-post-content__wysiwyg', '', False),
    'blog.arduino.cc': ('#content .entry', '.bottom-article', False),
    'developer.nvidia.com': ('.entry-content', '', False),
    'github.blog': ('.PostContent-main', '', False),
    'www.solidot.org': ('.articleBox .p_mainnew', '', False),
    'www.cnx-software.com': ('article .entry-content', '.saboxplugin-wrap', False),
    'www.postgresql.org': ('#pgContentWrap', '', False),
    'blog.rust-lang.org': ('.post', '', False),
    'go.dev': ('.Article .markdown', '', False),
    'karpathy.github.io': ('article.post-content', '', False),
}


def rule_for(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'http') or parsed.username or parsed.password:
        raise FulltextUnavailable('invalid_original_url')
    if parsed.hostname == 'ursb.me' and parsed.path.startswith('/reading/'):
        raise FulltextUnavailable('reading_card_without_original_article')
    if parsed.hostname in ('www.technologyreview.com', 'technologyreview.com') and '/roundtables-' in parsed.path:
        raise FulltextUnavailable('roundtable_event_not_investigation_article')
    if parsed.hostname not in RULES or parsed.port not in (None, 80, 443):
        raise FulltextUnavailable('requires_site_fulltext_rule')
    return RULES[parsed.hostname]


def extract(raw, url):
    selector, remove, repeated = rule_for(url)
    soup = BeautifulSoup(raw, 'html.parser')
    nodes = soup.select(selector)
    if not nodes or (not repeated and len(nodes) != 1):
        raise FulltextUnavailable('body_selector_missing_or_ambiguous')
    # Reject overlapping components so text cannot be duplicated by nested matches.
    if any(any(parent is other for parent in node.parents) for node in nodes for other in nodes if node is not other):
        raise FulltextUnavailable('overlapping_body_components')
    body = BeautifulSoup(''.join(str(node) for node in nodes), 'html.parser')
    for node in body.select('script,style,noscript,form,button' + (',' + remove if remove else '')):
        node.decompose()
    # Syntax highlighters split code into spans. Joining each span with spaces
    # corrupts identifiers, strings and indentation (e.g. f"..." -> f "...").
    for node in body.select('pre,code'):
        if node.parent is None:
            continue
        literal=node.get_text('',strip=False)
        node.clear()
        node.append(literal)
    text = body.get_text(' ', strip=True)
    if len(text) < 120:
        raise FulltextUnavailable('body_too_short_requires_review')
    return {'source_text': text, 'html': str(body), 'image_count': len(body.find_all('img')),
            'receipt': {'url': url, 'selector': selector, 'components': len(nodes),
                        'page_sha256': hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode()).hexdigest(),
                        'body_sha256': hashlib.sha256(text.encode()).hexdigest(),
                        'chars': len(text), 'rule_version': 1}}


async def fetch(url):
    """Bounded HTTP fetch; no RSS fallback and no cross-site redirect guessing."""
    import httpx
    rule_for(url)
    async def request():
        async with httpx.AsyncClient(timeout=25, trust_env=False, follow_redirects=False,
                proxy=os.environ.get('AI_NEWS_OUTBOUND_PROXY') or None,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; PersonalAIInbox/1.0)'}) as client:
            target = url
            for _ in range(5):
                async with client.stream('GET', target) as response:
                    if response.is_redirect:
                        target = urljoin(target, response.headers['location'])
                        rule_for(target)
                        if urlsplit(target).hostname != urlsplit(url).hostname:
                            raise FulltextUnavailable('original_redirected_to_different_site')
                        continue
                    if response.status_code != 200:
                        raise FulltextUnavailable('original_http_' + str(response.status_code))
                    if 'html' not in response.headers.get('content-type', '').lower():
                        raise FulltextUnavailable('original_is_not_html')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 6 * 1024 * 1024:
                            raise FulltextUnavailable('original_page_too_large')
                    result = extract(bytes(raw), target)
                    result['receipt']['requested_url'] = url
                    return result
            raise FulltextUnavailable('too_many_original_redirects')
    try:
        return await asyncio.wait_for(request(), timeout=90)
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        raise FulltextUnavailable('original_fetch_' + type(exc).__name__) from exc
