"""Site-specific article inputs. A missing rule/body is an explicit failure.

Rules were checked against saved publisher HTML, not RSS excerpts. They select
the complete body container; they do not expand links to different articles.
"""
from work_admission import check,options,http_client,AdmissionStopped
import asyncio
import hashlib
import os
import re
import html
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit, urljoin

from bs4 import BeautifulSoup


class FulltextUnavailable(ValueError):
    pass


# selector, removable site furniture, repeated body components allowed
RULES = {
    'spectrum.ieee.org': ('@reader:generic', '', False),
    'openai.com': ('@reader', '', False),
    'arstechnica.com': ('@reader', '', False),
    'blog.google': ('article.uni-article-wrapper', '', False),
    'blog.adafruit.com': ('@feed:adafruit', '', False),
    'interrupt.memfault.com': ('article#post-page .content', '', False),
    'www.nordicsemi.com': ('@reader:nordic', '', False),
    'nordicsemi.com': ('@reader:nordic', '', False),
    'www.oschina.net': ('@browser:.news-content .editor.heti', '', False),
    'www.ruanyifeng.com': ('#main-content', '', False),
    'deepmind.google': ('article.uni-article-wrapper,main#page-content .grid.section-default .rich-text', 'uni-portal,.article-tags', True),
    'developers.googleblog.com': ('@reader:googledev', '', False),
    'www.mr-wu.cn': ('article .entry-content', '#ftwp-container', False),
    'www.ti.com': ('main .richText', '', True),
    'www.quectel.com': ('article .article__main__inner', '', False),
    'martinfowler.com': ('main', '', False),
    'developer.espressif.com': ('.article-content', '', False),
    'www.jeffgeerling.com': ('.post-content', '', False),
    'simonwillison.net': ('.entry.entryPage', '', False),
    'www.infoq.cn': ('article .ProseMirror', '', False),
    'blog.st.com': ('article .entry-content', '.wp-block-uagb-table-of-contents', False),
    'techcrunch.com': ('.entry-content.wp-block-post-content', '', False),
    'aws.amazon.com': ('section.blog-post-content', '', False),
    'hackaday.com': ('article .entry-content', '', False),
    'www.tomshardware.com': ('#article-body', '#utility-bar,.slice-container-newsletterForm', False),
    'blog.cloudflare.com': ('.article-content', '', False),
    'www.theverge.com': ('.duet--article--article-body-component', '', True),
    'sspai.com': ('@reader:generic', '', False),
    'www.raspberrypi.com': ('.c-wysiwyg.c-post-content__wysiwyg', '', False),
    'blog.arduino.cc': ('#content .entry', '.bottom-article', False),
    'developer.nvidia.com': ('.entry-content', '', False),
    'github.blog': ('@reader:generic', '', False),
    'www.solidot.org': ('.articleBox .p_mainnew', '', False),
    'www.cnx-software.com': ('article .entry-content', '.saboxplugin-wrap', False),
    'www.postgresql.org': ('#pgContentWrap', '', False),
    'blog.rust-lang.org': ('.post', '', False),
    'go.dev': ('.Article .markdown', '', False),
    'karpathy.github.io': ('article.post-content', '', False),
    'lilianweng.github.io': ('article.post-single .post-content', '.toc', False),
    'blog.python.org': ('article.prose', 'aside,nav', False),
    'www.nidec.com': ('section#main', '.bottom-local,.local-nav', False),
    'analogdevicesinc.github.io': ('div#top-anchor.bodywrapper div.body', '.sphinxsidebar,.related', False),
    'embeddedartistry.com': ('@reader:generic', '', False),
    'www.technologyreview.com': ('@reader:generic', '', False),
    'technologyreview.com': ('@reader:generic', '', False),
    'www.anthropic.com': ('@reader:generic', '', False),
    'blog.csdn.net': ('@reader:generic', '', False),
    'oshwhub.com': ('@reader:generic', '', False),
    'jvns.ca': ('@reader:generic', '', False),
    'www.brandsninja.com': ('@reader:generic', '', False),
    'www.ruanx.net': ('@reader:generic', '', False),
    'engineering.fb.com': ('@reader:generic', '', False),
    'dropbox.tech': ('@reader:generic', '', False),
    'devblogs.microsoft.com': ('@reader:generic', '', False),
    'www.renesas.com': ('@reader:generic', '', False),
    'www.microsoft.com': ('@reader:generic', '', False),
    'www.maxongroup.com': ('@reader:generic', '', False),
    'slack.engineering': ('@reader:generic', '', False),
    'bair.berkeley.edu': ('@reader:generic', '', False),
    'www.microchip.com': ('@reader:generic', '', False),
    'customerstories.rainmaker.espressif.com': ('@reader:generic', '', False),
    'netflixtechblog.com': ('@feed:netflix', '', False),
}

TRUSTED_REDIRECTS = {('deepmind.google','blog.google'),('deepmind.google','developers.googleblog.com')}

READER_PATHS = {
    'embeddedartistry.com': r'^/blog/',
    'www.technologyreview.com': r'^/\d{4}/\d{2}/\d{2}/',
    'technologyreview.com': r'^/\d{4}/\d{2}/\d{2}/',
    'www.anthropic.com': r'^/(?:engineering|research|news|company)/',
    'blog.csdn.net': r'/article/details/\d+',
    'oshwhub.com': r'^/[^/]+/[^/]+',
    'jvns.ca': r'^/blog/',
    'www.brandsninja.com': r'^/brands/',
    'www.ruanx.net': r'^/[^/]+/?$',
    'engineering.fb.com': r'^/\d{4}/\d{2}/\d{2}/',
    'dropbox.tech': r'^/[^/]+/[^/]+',
    'devblogs.microsoft.com': r'^/[^/]+/[^/]+',
    'www.renesas.com': r'^/en/blogs/',
    'www.microsoft.com': r'^/en-us/research/blog/',
    'www.maxongroup.com': r'^/en/knowledge-and-support/blog/',
    'github.blog': r'^/(?:engineering|ai-and-ml|security)/',
    'slack.engineering': r'^/[^/]+/?$',
    'bair.berkeley.edu': r'^/blog/\d{4}/\d{2}/\d{2}/',
    'www.microchip.com': r'^/en-us/(?:solutions/technologies/motor-control-and-drive/applications-and-reference-designs|tools-resources/reference-designs)/',
    'customerstories.rainmaker.espressif.com': r'^/[^/]+/?$',
    'spectrum.ieee.org': r'^/[^/]+/?$',
    'sspai.com': r'^/(?:post/\d+|prime/story/)',
}
READER_HOSTS={host for host,rule in RULES.items() if rule[0].startswith('@reader')}

_ADAFRUIT_FEED_CACHE={}
_ADAFRUIT_FEED_PAGES=set()
_ADAFRUIT_CONTENT='{http://purl.org/rss/1.0/modules/content/}encoded'
_NETFLIX_FEED_CACHE={}
_NETFLIX_FEED_LOADED=False


def _canonical_url(url):
    parsed=urlsplit(url)
    return (parsed.scheme+'://'+parsed.netloc+parsed.path).rstrip('/')


async def fetch_netflix_feed(url, *,admission=None):
    import httpx
    global _NETFLIX_FEED_LOADED
    target=_canonical_url(url)
    proxy=os.environ.get('AI_NEWS_OUTBOUND_PROXY') or None
    if not _NETFLIX_FEED_LOADED:
        async with http_client(admission,timeout=30,trust_env=False,proxy=proxy,
                headers={'User-Agent':'PersonalAIInbox/1.0'}) as client:
            response=await client.get('https://netflixtechblog.com/feed')
        if response.status_code!=200:
            raise FulltextUnavailable('feed_http_'+str(response.status_code))
        try:root=ET.fromstring(response.content)
        except ET.ParseError as exc:raise FulltextUnavailable('feed_invalid_xml') from exc
        for item in root.findall('.//item'):
            link=(item.findtext('link') or '').strip()
            encoded=item.findtext(_ADAFRUIT_CONTENT) or ''
            if link and encoded:
                _NETFLIX_FEED_CACHE[_canonical_url(link)]=encoded
        _NETFLIX_FEED_LOADED=True
    raw=_NETFLIX_FEED_CACHE.get(target)
    if raw:
        result=_extract_html('<article>'+raw+'</article>',url,'article','',False)
        result['receipt']['requested_url']=url
        result['receipt']['transport']='Netflix TechBlog RSS content:encoded'
        return result
    # Medium-backed Netflix posts include the immutable 12-hex post id in the
    # publisher URL. Use that exact id only; never guess an article by title.
    match=re.search(r'-([0-9a-f]{12})(?:$|[/?#])',urlsplit(url).path)
    if not match:raise FulltextUnavailable('feed_article_not_found')
    medium='https://medium.com/p/'+match.group(1)
    async with http_client(admission,timeout=30,trust_env=False,proxy=proxy) as client:
        response=await client.get('https://r.jina.ai/'+medium,headers={'x-cache-tolerance':'31536000000'})
    if response.status_code!=200:raise FulltextUnavailable('reader_http_'+str(response.status_code))
    raw_reader=response.text
    if not raw_reader.startswith('Title: ') or f'URL Source: {medium}\n' not in raw_reader or '\nMarkdown Content:\n' not in raw_reader:
        raise FulltextUnavailable('reader_source_mismatch')
    text=raw_reader.split('\nMarkdown Content:\n',1)[1].strip()
    if len(text)<500 or re.search(r'just a moment|security verification|enable javascript and cookies|access denied',text,re.I):
        raise FulltextUnavailable('reader_returned_challenge_or_warning')
    return {'source_text':text,'html':'<article>'+html.escape(text)+'</article>',
            'image_count':len(re.findall(r'!\[',text)),
            'receipt':{'url':medium,'requested_url':url,'selector':'reviewed-reader-markdown',
                'transport':'Netflix Medium permalink via Jina Reader','components':1,'chars':len(text),'rule_version':1,
                'page_sha256':hashlib.sha256(raw_reader.encode()).hexdigest(),
                'body_sha256':hashlib.sha256(text.encode()).hexdigest()}}


async def fetch_adafruit_feed(url, *,admission=None):
    import httpx
    target=_canonical_url(url)
    proxy=os.environ.get('AI_NEWS_OUTBOUND_PROXY') or None
    async with http_client(admission,timeout=30,trust_env=False,proxy=proxy,
            headers={'User-Agent':'PersonalAIInbox/1.0'}) as client:
        for page in range(1,9):
            if target in _ADAFRUIT_FEED_CACHE:
                break
            if page in _ADAFRUIT_FEED_PAGES:
                continue
            feed='https://blog.adafruit.com/feed/' + (f'?paged={page}' if page>1 else '')
            response=await client.get(feed)
            if response.status_code!=200:
                raise FulltextUnavailable('feed_http_'+str(response.status_code))
            try:
                root=ET.fromstring(response.content)
            except ET.ParseError as exc:
                raise FulltextUnavailable('feed_invalid_xml') from exc
            items=root.findall('.//item')
            _ADAFRUIT_FEED_PAGES.add(page)
            if not items:
                break
            for item in items:
                link=(item.findtext('link') or '').strip()
                encoded=item.findtext(_ADAFRUIT_CONTENT) or ''
                if link and encoded:
                    _ADAFRUIT_FEED_CACHE[_canonical_url(link)]=encoded
        raw=_ADAFRUIT_FEED_CACHE.get(target)
    if not raw:
        raise FulltextUnavailable('feed_article_not_found')
    result=_extract_html('<article>'+raw+'</article>',url,'article','',False)
    result['receipt']['requested_url']=url
    result['receipt']['transport']='WordPress RSS content:encoded'
    return result


def rule_for(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'http') or parsed.username or parsed.password:
        raise FulltextUnavailable('invalid_original_url')
    if parsed.hostname == 'ursb.me':
        if parsed.path.startswith('/reading/'):
            raise FulltextUnavailable('reading_card_without_original_article')
        for prefix,rule in (
            ('/posts/',('article#postContent.post-content','',False)),
            ('/immersive/',('.container','',False)),
            ('/notes/',('article.prose','',False)),
            ('/playbook/',('main#exhibit','',False)),
        ):
            if parsed.path.startswith(prefix):
                return rule
    if parsed.hostname=='lilianweng.github.io' and parsed.path.rstrip('/')=='/faq':
        return ('.post-content','',False)
    if parsed.hostname=='openai.com':
        allowed=('/index/','/academy/','/global-affairs/','/business/','/signals/')
        if not (parsed.path.startswith(allowed) or parsed.path.rstrip('/')=='/openai-o1-contributions'):
            raise FulltextUnavailable('reader_requires_article_path')
    if parsed.hostname=='arstechnica.com' and not re.search(r'/\d{4}/\d{2}/[^/]+',parsed.path):
        raise FulltextUnavailable('reader_requires_article_path')
    reader_path=READER_PATHS.get(parsed.hostname)
    if reader_path and not re.search(reader_path,parsed.path):
        raise FulltextUnavailable('reader_requires_article_path')
    if parsed.hostname in ('www.technologyreview.com', 'technologyreview.com') and '/roundtables-' in parsed.path:
        raise FulltextUnavailable('roundtable_event_not_investigation_article')
    if parsed.hostname not in RULES or parsed.port not in (None, 80, 443):
        raise FulltextUnavailable('requires_site_fulltext_rule')
    return RULES[parsed.hostname]


def _extract_html(raw,url,selector,remove,repeated):
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


def extract(raw, url):
    selector, remove, repeated = rule_for(url)
    if selector.startswith('@reader'):raise FulltextUnavailable('reader_transport_required')
    if selector.startswith('@browser:'):raise FulltextUnavailable('browser_transport_required')
    if selector.startswith('@feed:'):raise FulltextUnavailable('feed_transport_required')
    return _extract_html(raw,url,selector,remove,repeated)


def extract_reader(raw,url):
    host=urlsplit(url).hostname
    if host not in READER_HOSTS:
        raise FulltextUnavailable('reader_host_not_reviewed')
    if not raw.startswith('Title: ') or re.search(r'^Warning:|^Title: (?:Just a moment|Access Denied)',raw,re.M):
        raise FulltextUnavailable('reader_returned_challenge_or_warning')
    if f'URL Source: {url}\n' not in raw or '\nMarkdown Content:\n' not in raw:
        raise FulltextUnavailable('reader_source_mismatch')
    text=raw.split('\nMarkdown Content:\n',1)[1].strip()
    if re.search(r'performing security verification|enable javascript and cookies to continue|this page couldn.t load',text,re.I):
        raise FulltextUnavailable('reader_returned_challenge_or_warning')
    if host=='arstechnica.com':
        footer=re.search(r'^\[!\[.*?\]\([^\n]+\)\]\(https://arstechnica\.com/author/[^\n]+\)',text,re.M)
        if not footer:raise FulltextUnavailable('reader_author_boundary_missing')
        text=text[:footer.start()].strip()
    if len(text)<500:raise FulltextUnavailable('reader_body_too_short')
    return {'source_text':text,'html':'<article>'+html.escape(text)+'</article>',
            'image_count':len(re.findall(r'!\[',text)),
            'receipt':{'url':url,'requested_url':url,'selector':'reviewed-reader-markdown',
                'transport':'Jina Reader anonymous API','components':1,'chars':len(text),'rule_version':1,
                'page_sha256':hashlib.sha256(raw.encode()).hexdigest(),
                'body_sha256':hashlib.sha256(text.encode()).hexdigest()}}


def _browser_executable():
    explicit=os.environ.get('AI_NEWS_CHROME_PATH')
    if explicit and Path(explicit).is_file():
        return explicit
    candidates=[p for p in Path.home().glob('.claude-server-commander/puppeteer-cache/chrome/linux-*/chrome-linux64/chrome') if p.is_file()]
    if not candidates:
        raise FulltextUnavailable('browser_runtime_missing')
    return str(max(candidates,key=lambda p:p.stat().st_mtime))


async def _browser_slot(timeout=80, *,admission=None):
    import fcntl
    check(admission)
    folder=Path('/home/ubuntu/ai-news/state/kaggle-month-dispatch')
    folder.mkdir(parents=True,exist_ok=True)
    stream=(folder/'browser-fetch.lock').open('a')
    deadline=time.monotonic()+timeout
    try:
        while True:
            check(admission)
            try:
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                return stream,fcntl
            except BlockingIOError:
                if time.monotonic()>=deadline:
                    stream.close()
                    raise FulltextUnavailable('browser_slot_timeout')
                await asyncio.sleep(.25)
    except BaseException:
        stream.close()
        raise


async def _fetch_browser_locked(url,selector,remove,repeated, *,admission=None):
    try:
        from playwright.async_api import async_playwright,TimeoutError as PlaywrightTimeoutError
    except ImportError as exc:
        raise FulltextUnavailable('browser_runtime_missing') from exc
    css=selector.split(':',1)[1]
    launch={'headless':True,'executable_path':_browser_executable(),
            'args':['--no-sandbox','--disable-dev-shm-usage']}
    proxy=os.environ.get('AI_NEWS_OUTBOUND_PROXY')
    if proxy:
        launch['proxy']={'server':proxy}
    try:
        async with async_playwright() as playwright:
            check(admission)
            browser=await playwright.chromium.launch(**launch)
            try:
                page=await browser.new_page(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36',
                    **({'service_workers':'block'} if admission is not None else {}))
                stopped=[]
                if admission is not None:
                    async def admit_request(route):
                        try:check(admission)
                        except AdmissionStopped as exc:
                            stopped.append(exc)
                            await route.abort()
                            return
                        await route.continue_()
                    await page.route('**/*',admit_request)
                check(admission)
                try:
                    response=await page.goto(url,wait_until='domcontentloaded',timeout=45000)
                except Exception:
                    if stopped:raise stopped[0]
                    check(admission)
                    raise
                if stopped:raise stopped[0]
                check(admission)
                if response is not None and response.status>=400:
                    title=(await page.title()).lower()
                    if 'just a moment' in title or 'access denied' in title:
                        raise FulltextUnavailable('browser_returned_challenge_or_warning')
                    raise FulltextUnavailable('browser_http_'+str(response.status))
                try:
                    await page.locator(css).first.wait_for(state='attached',timeout=20000)
                except PlaywrightTimeoutError:
                    raise FulltextUnavailable('browser_body_selector_missing_or_ambiguous') from None
                try:
                    await page.wait_for_function(
                        "(sel) => { const e=document.querySelector(sel); return e && (e.innerText || '').trim().length >= 120; }",
                        arg=css,timeout=15000)
                except PlaywrightTimeoutError:
                    raise FulltextUnavailable('browser_body_too_short') from None
                final_url=page.url
                if urlsplit(final_url).hostname!=urlsplit(url).hostname:
                    raise FulltextUnavailable('browser_redirected_to_different_site')
                if stopped:raise stopped[0]
                check(admission)
                raw=await page.content()
                if len(raw.encode())>8*1024*1024:
                    raise FulltextUnavailable('browser_page_too_large')
                result=_extract_html(raw,final_url,css,remove,repeated)
                result['receipt']['requested_url']=url
                result['receipt']['transport']='Playwright Chromium'
                return result
            finally:
                await browser.close()
    except FulltextUnavailable:
        raise
    except (OSError,PlaywrightTimeoutError) as exc:
        check(admission)
        raise FulltextUnavailable('browser_fetch_'+type(exc).__name__) from exc


async def fetch_browser(url,selector,remove,repeated, *,admission=None):
    slot,fcntl=await _browser_slot(**options(admission))
    try:
        return await _fetch_browser_locked(url,selector,remove,repeated,**options(admission))
    finally:
        try:fcntl.flock(slot,fcntl.LOCK_UN)
        finally:slot.close()


async def fetch_reader(url, *,admission=None):
    import httpx
    parsed=urlsplit(url);host=parsed.hostname
    candidates=[url]
    # Some legacy OpenAI routes return a tiny client-side error page without
    # the canonical trailing slash, while the slash variant exposes the article.
    if host=='openai.com' and not parsed.query and parsed.path and not parsed.path.endswith('/'):
        candidates=[url+'/',url]
    headers={'x-no-cache':'true'}
    if host in ('www.nordicsemi.com','nordicsemi.com','developers.googleblog.com') or host in READER_PATHS:
        # These sites can intermittently serve anti-bot pages to the reader.
        # Prefer a recent cached publisher snapshot instead of forcing a refetch.
        headers={'x-cache-tolerance':'86400000'}
    proxy=os.environ.get('AI_NEWS_OUTBOUND_PROXY') or None
    last=None
    routes=[proxy]
    if proxy:routes.append(None)  # A local proxy outage must not strand a public reader request.
    for route in routes:
        try:
            async with http_client(admission,timeout=30,trust_env=False,proxy=route) as client:
                for candidate in candidates:
                    try:
                        response=await client.get('https://r.jina.ai/'+candidate,headers=headers)
                        if response.status_code!=200:
                            last=FulltextUnavailable('reader_http_'+str(response.status_code));continue
                        result=extract_reader(response.text,candidate)
                        result['receipt']['requested_url']=url
                        result['receipt']['reader_route']='proxy' if route else 'direct'
                        if candidate!=url:result['receipt']['canonicalized_url']=candidate
                        return result
                    except FulltextUnavailable as exc:
                        last=exc
        except httpx.HTTPError as exc:
            last=FulltextUnavailable('reader_fetch_'+type(exc).__name__)
            continue
    raise last or FulltextUnavailable('reader_fetch_failed')


async def fetch(url, *,admission=None):
    """Bounded source fetch using only reviewed per-site transports."""
    import httpx
    selector,remove,repeated=rule_for(url)
    async def request():
        if selector=='@feed:adafruit':
            return await fetch_adafruit_feed(url,**options(admission))
        if selector=='@feed:netflix':
            return await fetch_netflix_feed(url,**options(admission))
        if selector.startswith('@feed:'):
            raise FulltextUnavailable('feed_transport_not_implemented')
        if selector.startswith('@browser:'):
            return await fetch_browser(url,selector,remove,repeated,**options(admission))
        if selector.startswith('@reader'):
            return await fetch_reader(url,**options(admission))
        async with http_client(admission,timeout=25, trust_env=False, follow_redirects=False,
                proxy=os.environ.get('AI_NEWS_OUTBOUND_PROXY') or None,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; PersonalAIInbox/1.0)'}) as client:
            target = url
            for _ in range(5):
                async with client.stream('GET', target) as response:
                    if response.is_redirect:
                        next_target=urljoin(target,response.headers['location'])
                        source_host=urlsplit(url).hostname
                        next_host=urlsplit(next_target).hostname
                        if next_host!=source_host and (source_host,next_host) not in TRUSTED_REDIRECTS:
                            raise FulltextUnavailable('original_redirected_to_different_site')
                        next_selector,_,_=rule_for(next_target)
                        if next_selector.startswith('@'):
                            result=await fetch(next_target,**options(admission))
                            result['receipt']['requested_url']=url
                            result['receipt']['redirected_url']=next_target
                            return result
                        target=next_target
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
