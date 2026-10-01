import copy
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock
from xml.etree import ElementTree as ET
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
import vendor_sources as v
from vendor_routes import router

FIXTURES = {
 'nxp': '<h3><a dataname="Landing Page Blogs - Recent Articles Section" href="/company/about-nxp/smarter-world-blog/test">Article</a></h3>',
 'quectel': '<a class="group" href="/blog/test"><span class="text-lg">Article</span><span class="ml-auto"><p>2026-09-30</p></span></a>',
 'nidec': '<section><a href="/cn/technology/casestudy/test">Article</a></section>',
 'ti': json.dumps([{'total':1},{'headline':'Article','path':'/about-ti/behind-chip/articles/test','date':'2026-09-30'}]),
 'renesas': '<h3 class="blog-post-title"><a href="/en/blogs/test">Article</a></h3>',
 'adi': '<a class="reference internal" href="eval-cn1234-ebz/">Article</a>',
 'microchip': '### Article\n\n[Learn More](https://www.microchip.com/en-us/tools-resources/reference-designs/test)',
 'nordic': 'Customer 30 Sep 2026 Article](https://www.nordicsemi.com/Nordic-news/2026/09/test)',
 'espressif': '<main><a class="block" href="/test"><p class="font-bold">Article</p></a></main>',
 'maxon': '<div class="ImageTextStandard-module__header"><h3>Article</h3></div><a class="Button-module" href="/en/knowledge-and-support/blog/test">More</a>',
 'onsemi': '<a class="text-body" href="/company/newsroom/blog/industrial/test">Article</a>',
}

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(v, 'ROOT', tmp_path)


def seed(key='nxp'):
    cfg = v.CONFIGS[key]
    url = cfg['origin'] + cfg['prefix'] + ('test' if key != 'adi' else 'eval-cn1234-ebz/')
    entry = {'url':url, 'title':'A < B & C', 'published':None, 'discovered':'2026-09-24T01:02:03.000Z'}
    data = {'entries':[entry], 'seen':{url:entry['discovered']}, 'seeded':True, 'checkedAt':entry['discovered']}
    v.save(key, data)
    return data


@pytest.mark.parametrize('key', FIXTURES)
def test_each_official_parser(key):
    rows = v.parse_list(v.CONFIGS[key], FIXTURES[key])
    assert len(rows) == 1 and rows[0]['title'] == 'Article'
    assert rows[0]['url'].startswith(v.CONFIGS[key]['origin'])


@pytest.mark.parametrize('key', FIXTURES)
def test_source_refresh_is_idempotent_and_failures_preserve_snapshot(key):
    cfg = v.CONFIGS[key]
    first = v.refresh(key, fetch=lambda _:FIXTURES[key], now=1790810000)
    assert first['state'] == 'ok' and first['added'] == 1
    assert v.refresh(key, force=True, fetch=lambda _:FIXTURES[key], now=1790810001)['added'] == 0
    before = copy.deepcopy(v.load(key))
    result = v.refresh(key, force=True, fetch=Mock(side_effect=RuntimeError('secret')), now=1790810002)
    after = v.load(key)
    assert result['state'] == 'error' and 'secret' not in str(result)
    for name in ('entries','seen','checkedAt','last_success_at'):
        assert after[name] == before[name]
    assert after['next_run_at'] == 1790810002 + v.RETRY


def test_due_gate_does_not_fetch():
    v.refresh('nxp', fetch=lambda _:FIXTURES['nxp'], now=1790810000)
    fetch=Mock(side_effect=AssertionError('network should not run'))
    assert v.refresh('nxp', fetch=fetch, now=1790810001)['state'] == 'not_due'
    fetch.assert_not_called()


def test_lock_skips_overlapping_refresh():
    with v.locked('nxp'):
        assert v.refresh('nxp', fetch=Mock(side_effect=AssertionError()))['state'] == 'busy'


def test_allowlist_rejects_offsite_query_and_traversal():
    cfg=v.CONFIGS['nxp']
    assert v.allowed_url(cfg, 'https://www.nxp.com.evil.test/company/about-nxp/smarter-world-blog/a') is None
    assert v.allowed_url(cfg, 'http://127.0.0.1/a') is None
    assert v.allowed_url(cfg, cfg['origin']+cfg['prefix']+'a?q=1#z').endswith('/a')
    with pytest.raises(KeyError): v.load('../../etc/passwd')


def test_empty_or_unaligned_html_fails_closed():
    with pytest.raises(ValueError): v.parse_list(v.CONFIGS['nxp'], '<html>challenge</html>')
    with pytest.raises(ValueError): v.parse_list(v.CONFIGS['maxon'], '<a class="Button-module" href="/en/knowledge-and-support/blog/a">More</a>')


def test_newsletter_selection_and_seen_preserved():
    cfg=v.CONFIGS['kickstarter'];url=cfg['origin']+cfg['prefix']+'test'
    listing='<a href="/brands/kickstarter/test">Issue</a>'
    detail='<h1>Newsletter</h1><link rel="canonical" href="'+url+'"><table class="nl-container"><tr><td>EXPLORE DESIGN &amp; TECH '+'body '*60+'</td></tr></table>'
    result=v.refresh('kickstarter',fetch=lambda u:listing if u==cfg['fetch_url'] else detail,now=1790810000)
    assert result['added']==1
    assert v.refresh('kickstarter',force=True,fetch=lambda u:listing if u==cfg['fetch_url'] else detail,now=1790810001)['added']==0
    xml=ET.fromstring(v.render('kickstarter'))
    assert '第三方归档' in xml.findtext('./channel/item/description')
    assert xml.findtext('./channel/item/guid')==url


def test_newsletter_broken_body_keeps_previous_cache():
    before=seed('kickstarter');cfg=v.CONFIGS['kickstarter']
    report=v.refresh('kickstarter',force=True,fetch=lambda u:'<a href="/brands/kickstarter/new">new</a>' if u==cfg['fetch_url'] else '<h1>invalid</h1>')
    assert report['state']=='error' and v.load('kickstarter')['entries']==before['entries']


def test_xml_preserves_guid_discovery_timestamp_and_escaping():
    data=seed()
    item=ET.fromstring(v.render('nxp')).find('./channel/item')
    assert item.findtext('guid')==data['entries'][0]['url']
    assert item.findtext('title')=='A < B & C'
    assert item.findtext('pubDate')=='Thu, 24 Sep 2026 01:02:03 GMT'


def test_import_twelve_workflows_is_idempotent(tmp_path):
    dbpath=tmp_path/'legacy.sqlite'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE workflow_entity(id TEXT,staticData TEXT)')
        for key,cfg in v.CONFIGS.items():
            state=seed(key)
            db.execute('INSERT INTO workflow_entity VALUES(?,?)',(cfg['workflow_id'],json.dumps({'global':state})))
            (v.folder()/(key+'.json')).unlink()
    reports=v.import_n8n(dbpath)
    assert len(reports)==12 and all(x['state']=='imported' for x in reports)
    assert all(x['state']=='already_imported' for x in v.import_n8n(dbpath))
    assert all(len(v.load(k)['entries'])==1 for k in v.CONFIGS)


def client():
    app=FastAPI();app.include_router(router)
    return TestClient(app,client=('127.0.0.1',32100))


def test_feed_read_is_cached_etag_and_never_calls_fetch(monkeypatch):
    seed();monkeypatch.setattr(v,'fetch_text',Mock(side_effect=AssertionError()))
    c=client();r=c.get('/internal/vendor-feeds/nxp.xml')
    assert r.status_code==200 and r.headers['content-type'].startswith('application/rss+xml')
    assert c.get('/internal/vendor-feeds/nxp.xml',headers={'If-None-Match':r.headers['etag']}).status_code==304
    v.fetch_text.assert_not_called()


def test_empty_feed_503_unknown_404_and_forwarded_403():
    c=client()
    assert c.get('/internal/vendor-feeds/nxp.xml').status_code==503
    assert c.get('/internal/vendor-feeds/unknown.xml').status_code==404
    assert c.get('/internal/vendor-feeds/status',headers={'X-Forwarded-For':'127.0.0.1'}).status_code==403


def test_remote_cannot_read_feed():
    app=FastAPI();app.include_router(router)
    c=TestClient(app,client=('203.0.113.42',32100))
    assert c.get('/internal/vendor-feeds/nxp.xml').status_code==403


def test_refresh_authentication_and_error_response(monkeypatch):
    import month_control
    def auth(value):
        from fastapi import HTTPException
        if value!='test-token': raise HTTPException(401)
    monkeypatch.setattr(month_control,'authenticate',auth)
    c=client()
    assert c.post('/internal/vendor-feeds/nxp/refresh').status_code==401
    monkeypatch.setattr(v,'refresh',lambda *a,**k:{'state':'error'})
    assert c.post('/internal/vendor-feeds/nxp/refresh',headers={'X-Vendor-Refresh':'test-token'}).status_code==502


def test_invalid_state_is_not_overwritten_and_other_sources_continue():
    path=v.folder()/'nxp.json';path.write_text('{broken JSON')
    result=v.refresh('nxp',force=True,fetch=Mock(side_effect=AssertionError()))
    assert result['state']=='error' and result['state_preserved']
    assert path.read_text()=='{broken JSON'
    assert v.refresh('ti',force=True,fetch=lambda _:FIXTURES['ti'])['state']=='ok'


def test_status_route_precedes_reader_catchall():
    import api
    routes=[getattr(r, 'path', None) for r in api.app.routes]
    assert routes.index('/mf/v1/ai/vendor-sources') < routes.index('/mf/{path:path}')


def test_cutover_plan_preserves_ids_and_rejects_duplicates():
    from vendor_migration import plan,rewrite_catalog
    feeds=[{'id':i,'feed_url':'http://127.0.0.1:5678/webhook/'+c['legacy_path']} for i,c in enumerate(v.CONFIGS.values(),62)]
    changes=plan(feeds)
    assert [x['id'] for x in changes]==list(range(62,74))
    catalog=[{'url':f['feed_url'],'custom':'keep'} for f in feeds]+[{'url':'https://other.example/feed','custom':'WIP'}]
    new=rewrite_catalog(catalog,changes)
    assert new[-1]==catalog[-1] and all(r['custom']=='keep' for r in new[:-1])
    assert rewrite_catalog(new,changes,rollback=True)==catalog
    with pytest.raises(ValueError):plan(feeds+[feeds[0]])


def test_cutover_put_only_changes_url_and_proxy():
    from vendor_migration import switch
    import httpx
    state={'id':62,'feed_url':'http://old','title':'keep','disabled':True,'category':{'id':7}}
    def route(req):
        if req.method=='PUT':
            value=json.loads(req.content);assert set(value)=={'feed_url','fetch_via_proxy'};state.update(value)
        return httpx.Response(200,json=state)
    with httpx.Client(transport=httpx.MockTransport(route)) as c:
        changes=[{'id':62,'old_url':'http://old','new_url':'http://new','fetch_via_proxy':True}]
        switch(c,'http://local',changes)
        assert state['id']==62 and state['title']=='keep' and state['disabled']
        switch(c,'http://local',changes,rollback=True)
        assert state['feed_url']=='http://old' and state['fetch_via_proxy']


def test_corrupt_article_is_503_not_empty_feed():
    value=seed();value['entries'][0]['discovered']='not-a-date';v.save('nxp',value)
    assert client().get('/internal/vendor-feeds/nxp.xml').status_code==503
