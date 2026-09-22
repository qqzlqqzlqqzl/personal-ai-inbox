import json
import httpx
import pytest
import product_source as product

URL = 'https://www.producthunt.com/products/example'
IMG = 'https://ph-files.imgix.net/example-screen.png?w=1000'

def page(url=URL, screenshot=None, description='A real product introduction with enough descriptive source text.'):
    data = {'@type': 'WebApplication', 'url': url, 'description': description,
            'screenshot': [IMG] if screenshot is None else screenshot}
    return '<script type="application/ld+json">' + json.dumps(data) + '</script>'

def test_matches_product_not_recommendation():
    html = page(URL + '-other', description='Wrong product introduction must not be used.') + page()
    assert product.parse_product_page(html, URL)['description'].startswith('A real product')

def test_refuses_mismatched_product_instead_of_borrowing_meta():
    with pytest.raises(ValueError):
        product.parse_product_page(page(URL+'-other')+'<meta property="og:description" content="A recommendation">', URL)

def test_only_own_screenshots_not_gallery_or_logo():
    html = page(screenshot=[IMG, 'https://ph-files.imgix.net/icon.png?w=64&h=64'])
    html += '<img alt="Gallery image" src="https://ph-files.imgix.net/another-product.png">'
    assert product.parse_product_page(html, URL)['images'] == [IMG]

def test_description_is_text_not_markup():
    assert '<' not in product.parse_product_page(page(description='<b>Valid product description that is long enough.</b>'), URL)['description']

@pytest.mark.asyncio
async def test_enrichment_idempotent_preserves_rss_and_escapes(monkeypatch):
    entry = {'id': 7, 'url': URL, 'title': 'Example', 'content': '<p>Original RSS</p>'}
    writes = []
    def upstream(req):
        if req.method == 'PUT':
            writes.append(json.loads(req.content)['content']); entry['content'] = writes[-1]
        return httpx.Response(200, json=entry)
    def external(req):
        if req.url.host == 'www.producthunt.com':
            return httpx.Response(200, text=page())
        return httpx.Response(200, content=b'image', headers={'content-type':'image/png'})
    monkeypatch.setattr(product, 'product_client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(external)))
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as c:
        first = await product.enrich_product_entry(c, entry, 'http://reader', {})
        second = await product.enrich_product_entry(c, entry, 'http://reader', {})
    assert first['updated'] and not second['updated']
    assert len(writes) == 1 and '<p>Original RSS</p>' in writes[0]
    assert '非长文全文' in writes[0] and first['cover_url'] == IMG

@pytest.mark.asyncio
async def test_external_failure_does_not_overwrite(monkeypatch):
    monkeypatch.setattr(product, 'product_client', lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(403))))
    def no_write(req):
        pytest.fail('must not write when source fails')
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_write)) as c:
        with pytest.raises(httpx.HTTPStatusError):
            await product.enrich_product_entry(c, {'id':1,'url':URL,'content':'keep','title':'Example'},'http://reader',{})


def test_mobile_application_is_a_supported_product():
    html = page().replace('WebApplication','MobileApplication')
    assert product.parse_product_page(html,URL)['images'] == [IMG]
