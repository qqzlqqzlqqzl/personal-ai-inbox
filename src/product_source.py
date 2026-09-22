"""Non-AI Product Hunt previews from the product's own structured page data."""
import json
import os
from html import escape
from urllib.parse import urlsplit, urljoin, parse_qs

import httpx
from bs4 import BeautifulSoup
from content_input import content_text, first_image_src, _safe_image_url

LABEL = '产品介绍（Product Hunt）'


def is_product_entry(entry):
    u = urlsplit(entry.get('url', ''))
    return u.scheme in ('http', 'https') and u.hostname in ('producthunt.com', 'www.producthunt.com') and u.path.startswith('/products/') and not u.username


def parse_product_page(raw, final_url):
    soup = BeautifulSoup(raw, 'html.parser')
    product = None
    def objects(value):
        if isinstance(value, list):
            for item in value:
                yield from objects(item)
        elif isinstance(value, dict):
            yield value
            yield from objects(value.get('@graph', []))
    for tag in soup.find_all('script', type='application/ld+json'):
        try:
            values = json.loads(tag.get_text())
        except (ValueError, TypeError):
            continue
        for obj in objects(values):
            u = urlsplit(str(obj.get('url') or obj.get('@id') or ''))
            if any(t in ('WebApplication', 'SoftwareApplication', 'MobileApplication', 'Product') for t in (obj.get('@type') if isinstance(obj.get('@type'), list) else [obj.get('@type')])) and u.hostname in ('www.producthunt.com', 'producthunt.com') and u.path.rstrip('/') == urlsplit(final_url).path.rstrip('/'):
                product = obj
                break
        if product:
            break
    if product is None:
        raise ValueError('matching_product_structured_data_missing')
    description = str(product.get('description') or '').strip()
    # Keep plain source text, never scripts, page menus, review counts or generated copy.
    description = BeautifulSoup(description, 'html.parser').get_text(' ', strip=True)
    if len(description) < 30 or 'just a moment' in description.lower():
        raise ValueError('product_description_unavailable')
    candidates = []
    shots = (product or {}).get('screenshot') or []
    if isinstance(shots, (str, dict)):
        shots = [shots]
    for shot in shots:
        candidates.append((shot.get('contentUrl') or shot.get('url')) if isinstance(shot, dict) else shot)
    # Do not borrow gallery/OG images from recommended products or small icons.
    images = []
    for candidate in candidates:
        url = _safe_image_url(candidate, final_url)
        query = parse_qs(urlsplit(url or '').query)
        dims = [int(v[0]) for k,v in query.items() if k in ('w','h') and v and v[0].isdigit()]
        if dims and max(dims) <= 180:
            continue
        if url and urlsplit(url).hostname == 'ph-files.imgix.net' and urlsplit(url).path not in [urlsplit(i).path for i in images]:
            images.append(url)
    website = None
    for a in soup.find_all('a', href=True):
        if a.get_text(' ', strip=True).lower() == 'visit website':
            candidate = _safe_image_url(a['href'], final_url)
            if candidate and urlsplit(candidate).hostname not in ('www.producthunt.com', 'producthunt.com'):
                website = candidate
                break
    return {'description': description[:10000], 'images': images[:4], 'website': website}


async def enrich_product_entry(client, entry, backend_url, headers):
    from prepared_content import apply as apply_prepared, remember
    entry = apply_prepared(entry)
    original = entry.get('content') or ''
    if not is_product_entry(entry):
        raise ValueError('unsupported_product_source')
    parsed_original = BeautifulSoup(original, 'html.parser')
    rss_original = original
    if parsed_original.find(['h2','h3'], string=LABEL):
        marker = parsed_original.find(['h2','h3'], string='原始 RSS 简介')
        if marker is None:
            raise ValueError('enrichment_missing_original_rss_marker')
        # A previous network failure can leave a valid introduction but no pictures.
        # Retry the product's own screenshot data without nesting another RSS copy.
        managed = []
        for node in parsed_original.contents:
            if node is marker:
                break
            managed.append(str(node))
        cover = first_image_src(''.join(managed))
        if cover:
            return {'content': original, 'cover_url': cover, 'cover_source': 'product_screenshot', 'content_source': 'product_page', 'updated': False}
        rss_original = ''.join(str(node) for node in marker.next_siblings)
    async with product_client() as external:
        async with external.stream('GET', entry['url']) as response:
            response.raise_for_status()
            if not is_product_entry({'url': str(response.url)}):
                raise ValueError('unexpected_product_redirect')
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 3 * 1024 * 1024:
                    raise ValueError('product_page_too_large')
            data = parse_product_page(bytes(body), str(response.url))
        images = []
        for candidate in data['images']:
            try:
                async with external.stream('GET', candidate, timeout=6) as response:
                    if response.status_code == 200 and response.headers.get('content-type', '').lower().startswith('image/'):
                        images.append(candidate)
            except httpx.HTTPError:
                continue
            if len(images) >= 2:
                break
    # Product listings are introductions, not long articles. Preserve the RSS below.
    html = '<h2>' + LABEL + '</h2><p>' + escape(data['description']) + '</p>'
    html += '<p><small>' + LABEL + ' · 非长文全文。</small></p>'
    for image in images:
        html += '<p><img src="' + escape(image, quote=True) + '" alt="' + escape(entry['title'] + ' 产品截图', quote=True) + '"></p>'
    if data['website']:
        html += '<p><a href="' + escape(data['website'], quote=True) + '">访问产品官网</a></p>'
    html += '<p><a href="' + escape(entry['url'], quote=True) + '">查看 Product Hunt 产品页面</a></p><hr><h3>原始 RSS 简介</h3>' + rss_original
    if html == original:
        return {'content': original, 'cover_url': None, 'cover_source': None, 'content_source': 'product_page', 'updated': False}
    saved = await client.put(backend_url + '/v1/entries/' + str(entry['id']), headers=headers, json={'content': html}, timeout=15)
    saved.raise_for_status()
    fresh = await client.get(backend_url + '/v1/entries/' + str(entry['id']), headers=headers, timeout=15)
    fresh.raise_for_status()
    content = fresh.json().get('content') or html
    remember(entry, content, 'product_page')
    return {'content': content, 'cover_url': first_image_src(content) if images else None, 'cover_source': 'product_screenshot' if images else None, 'content_source': 'product_page', 'updated': True}


def product_client():
    return httpx.AsyncClient(proxy=os.environ.get('AI_NEWS_OUTBOUND_PROXY') or None, trust_env=False, timeout=12, follow_redirects=True, max_redirects=3)
