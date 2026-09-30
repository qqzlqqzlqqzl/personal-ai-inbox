from unittest.mock import AsyncMock, patch

import httpx
import pytest

from adafruit_source import (
    OriginalUnavailable,
    checked_url,
    extract_original,
    resolve,
    see_more_link,
)
from prepared_content import apply, remember

ORIGINAL = 'https://ep-news.web.cern.ch/colibri-cern-vhdl-common-library-released-for-everyone-to-use/'
ADAFRUIT = 'https://blog.adafruit.com/2026/09/28/the-cern-vhdl-common-library-released-for-everyone-to-use/'
TITLE = 'The CERN VHDL Common Library released for everyone to use'
SUMMARY = '<p>' + 'CERN FPGA common library. ' * 12 + f'</p><p>See more at <a href="{ORIGINAL}">ep-news.web.cern.ch</a>. Via <a href="https://news.ycombinator.com/item?id=123">Hacker News</a>.</p>'


def page(url=ORIGINAL, title=TITLE, text=None):
    return f'<html><head><link rel="canonical" href="{url}"></head><body><h1>{title}</h1><div class="entry-content wp-block-post-content"><p>{text or "CERN VHDL common library implementation details. " * 45}</p><script>bad()</script><p onclick="bad()">Last paragraph.</p></div></body></html>'


def entry():
    return {'id':1,'user_id':1,'title':TITLE,'url':ADAFRUIT,'content':SUMMARY,
            'feed_id':1,'published_at':'2026-09-28T00:00:00Z'}


def test_only_attribution_anchor_not_via_or_multiple_targets():
    assert see_more_link(SUMMARY) == ORIGINAL
    assert see_more_link('<p>Via <a href="'+ORIGINAL+'">CERN</a></p>') is None
    assert see_more_link(SUMMARY.replace('See more at','Read this story at')) is None
    assert see_more_link(SUMMARY+ '<p>See more at <a href="https://medium.com/@x/other">Medium</a></p>') is None
    assert see_more_link('<p>See more at <a href="https://news.ycombinator.com/item?id=123">HN</a></p>') is None
    assert see_more_link('<a href="'+ORIGINAL+'">See more at CERN</a>') == ORIGINAL


@pytest.mark.parametrize('url',[
    'file:///tmp/article','javascript:alert(1)','http://127.0.0.1/article',
    'https://user@medium.com/story','https://:pw@medium.com/story',
    'https://medium.com:8443/story','https://medium.com.evil.example/story',
    'https://medium.com:bad/story','https://medium.com/\\evil','https://medium.com/\nfoo'])
def test_url_validation(url):
    with pytest.raises(OriginalUnavailable):
        checked_url(url, {'medium.com'})


@pytest.mark.asyncio
async def test_selects_longer_original_and_records_provenance():
    requests = []
    def respond(request):
        requests.append(str(request.url))
        assert not request.headers.get('authorization')
        return httpx.Response(200, text=page(), headers={'content-type':'text/html'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result=await resolve(entry(), client)
    assert requests == [ORIGINAL]
    assert result['receipt']['source']=='adafruit_linked_original'
    assert result['receipt']['target_url']==ORIGINAL
    assert result['receipt']['url']==ORIGINAL
    assert 'Last paragraph.' in result['source_text']
    assert 'bad()' not in result['html'] and 'onclick' not in result['html']


@pytest.mark.parametrize('body,reason',[
    (page(url='https://ep-news.web.cern.ch/'), 'identity'),
    (page(title='Login error page'), 'title'),
    (page(text='Short text.'), 'complete'),
    (page(text='Member-only story. '+ 'body '*500), 'access'),
    (page().replace('entry-content wp-block-post-content','other'), 'body'),
    (page().replace('</head>','<meta property="og:url" content="https://ep-news.web.cern.ch/other"></head>'), 'identity')])
@pytest.mark.asyncio
async def test_rejected_page_preserves_summary(body,reason):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,text=body,headers={'content-type':'text/html'}))) as client:
        result=await resolve(entry(),client)
    assert result['html']==SUMMARY
    assert result['receipt']['source']=='adafruit_summary'
    assert reason in result['receipt']['follow_status']


@pytest.mark.asyncio
async def test_http_failure_and_timeout_keep_summary():
    for response in [httpx.Response(403),httpx.Response(200,text='binary',headers={'content-type':'application/pdf'})]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request, response=response:response)) as client:
            assert (await resolve(entry(),client))['html']==SUMMARY
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(httpx.ReadTimeout('private error details')))) as client:
        result=await resolve(entry(),client)
    assert result['receipt']['follow_status']=='ReadTimeout'


@pytest.mark.parametrize('target',['http://127.0.0.1/','https://medium.com/story','https://user@ep-news.web.cern.ch/story'])
@pytest.mark.asyncio
async def test_redirect_target_rejected_before_request(target):
    requests=[]
    def respond(request):
        requests.append(str(request.url))
        return httpx.Response(302,headers={'location':target})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result=await resolve(entry(),client)
    assert requests==[ORIGINAL]
    assert result['html']==SUMMARY


@pytest.mark.asyncio
async def test_redirect_and_response_size_bounded():
    requests=[]
    def redirect(request):
        requests.append(str(request.url))
        return httpx.Response(302,headers={'location':ORIGINAL})
    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        result=await resolve(entry(),client)
    assert len(requests)==4
    assert result['receipt']['follow_status']=='original_redirect_limit'
    with patch('adafruit_source.MAX_BYTES',1000):
        small={**entry(),'content':'<p>'+('CERN '*25)+'</p><p>See more at <a href="'+ORIGINAL+'">CERN</a></p>'}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,text=page(),headers={'content-type':'text/html'}))) as client:
            result=await resolve(small,client)
        assert result['receipt']['follow_status']=='original_too_large'


def test_durable_reader_body_and_provenance_preserve_new_upstream(db):
    original=entry()
    body,_text=extract_original(page(),ORIGINAL,ORIGINAL,TITLE,SUMMARY)
    remember(original,body,'adafruit_linked_original',{'url':ORIGINAL,'source':'adafruit_linked_original'})
    result=apply(original)
    assert result['content']==body
    assert result['content_source_url']==ORIGINAL
    assert apply({**original,'content':SUMMARY+'<p>Publisher correction.</p>'})['content'].endswith('correction.</p>')
    changed_link=SUMMARY.replace(ORIGINAL,'https://ep-news.web.cern.ch/corrected-article/')
    assert apply({**original,'content':changed_link})['content']==changed_link
    assert apply({**original,'user_id':2}) == {**original,'user_id':2}


@pytest.mark.asyncio
async def test_worker_uses_linked_body_without_replacing_upstream_or_reanalyzing_library(db, monkeypatch):
    import core
    import worker
    original=entry()
    core.discover([original])
    with core.connect() as connection:
        row=connection.execute('SELECT * FROM analyses WHERE entry_id=1').fetchone()
    body,text=extract_original(page(),ORIGINAL,ORIGINAL,TITLE,SUMMARY)
    resolved={'html':body,'source_text':text,'receipt':{'url':ORIGINAL,'source':'adafruit_linked_original'}}
    monkeypatch.delenv('ARK_API_KEY',raising=False)
    mf=AsyncMock(return_value=original)
    with patch('worker.mf_get',mf), patch('adafruit_source.resolve',AsyncMock(return_value=resolved)), \
         patch('worker.discover_original_cover',AsyncMock(return_value=(None,None))):
        await worker.process_one(None,row,core.settings())
    assert mf.await_count==1
    with core.connect() as connection:
        result=dict(connection.execute('SELECT * FROM analyses WHERE entry_id=1').fetchone())
    assert result['state']=='waiting_model'
    assert result['content_source']=='adafruit_linked_original'
    assert result['source_text']==text
    assert apply(original)['content_source_url']==ORIGINAL


@pytest.mark.parametrize('concurrent',[False,True])
@pytest.mark.asyncio
async def test_bridge_uses_original_with_consistent_recovery_hash(db, monkeypatch, concurrent):
    import cloud_bridge

    import card_translation
    import core
    import worker

    original=entry()
    core.discover([original])
    body,text=extract_original(page(),ORIGINAL,ORIGINAL,TITLE,SUMMARY)
    async def resolved(_entry):
        if concurrent:
            core.update(1,state='done')
        return {'html':body,'source_text':text,'image_count':0,
                'receipt':{'url':ORIGINAL,'source':'adafruit_linked_original'}}
    with patch('cloud_bridge.load_inbox',return_value=(core,worker,card_translation)), \
         patch('worker.mf_get',AsyncMock(return_value=original)), \
         patch('adafruit_source.resolve',resolved):
        sample=await cloud_bridge.prepare_sample('.',1)
        if concurrent:
            assert sample['samples']==[]
            assert apply(original)==original
        else:
            row=sample['samples'][0]
            assert row['source_text']==text
            assert row['content_source']=='adafruit_linked_original'
            assert row['fulltext_receipt']['url']==ORIGINAL
            with core.connect() as connection:
                stored=connection.execute('SELECT content_hash FROM analyses WHERE entry_id=1').fetchone()[0]
            assert row['content_hash']==stored
            manifest={'items':[{'source_refs':[{'entry_id':1,'upstream_hash':row['upstream_hash']}]}]}
            assert await cloud_bridge.verify_upstream('.',manifest)=={1}
