"""Persistent eligibility, total/page/detail agreement and source identity."""
import copy
import json

import httpx
import pytest
import pytest_asyncio

import api
import core
from content_quality import assess, public_for_row, serialized


def set_quality(eid, paid):
    with core.connect() as db:
        row=dict(db.execute('SELECT * FROM analyses WHERE entry_id=?',(eid,)).fetchone())
    body='<script type="application/ld+json">'+json.dumps({'@type':'NewsArticle','url':row['url'],'isAccessibleForFree':not paid})+'</script><article>Readable article.</article>'
    record=assess(url=row['url'],html_body=body,extraction_state='available',observed_at=100)
    assert core.record_content_quality(row,record,expected_quality=row['content_quality'])


@pytest_asyncio.fixture
async def quality_api(db,entry,model_result,monkeypatch):
    monkeypatch.setattr(api,'ROOT',db)
    monkeypatch.delenv('MINIFLUX_API_KEY',raising=False)
    entries={eid:{**copy.deepcopy(entry),'id':eid,'title':f'Article {eid}','url':f'https://example.org/article-{eid}'} for eid in range(1,5)}
    core.discover(entries.values())
    for eid in entries:
        core.update(eid,state='done',source_text='Original readable material',content_hash='body-v1',
                    score=10-eid,result=json.dumps(model_result))
    def upstream(request):
        path=request.url.path
        if path.endswith('/v1/me'):
            return httpx.Response(200,json={'id':1,'is_admin':True})
        if path.endswith('/entries/ids'):
            return httpx.Response(200,json={'entry_ids':list(entries),'total':len(entries)})
        if path.endswith('/v1/feeds'):
            return httpx.Response(200,json=[entry['feed']])
        if '/v1/entries/' in path:
            return httpx.Response(200,json=entries[int(path.rsplit('/',1)[1])])
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as upstream_client:
        monkeypatch.setattr(api.app.state,'client',upstream_client,raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://testserver',headers={'X-Auth-Token':'synthetic'}) as client:
            yield client


@pytest.mark.asyncio
async def test_null_false_true_correction_preserves_counts_pages_and_detail(quality_api):
    async def page(offset):
        response=await quality_api.get(f'/mf/v1/entries?ai_view=recommended&limit=1&offset={offset}&ai_min=6')
        assert response.status_code==200
        return response.json()
    before=await page(0)
    assert before['total']==4 and before['entries'][0]['id']==1
    assert before['entries'][0]['ai']['content_quality']['recommendation_eligible'] is None
    set_quality(1,True)
    pages=[await page(offset) for offset in range(3)]
    assert {page['total'] for page in pages}=={3}
    assert [page['entries'][0]['id'] for page in pages]==[2,3,4]
    detail=(await quality_api.get('/mf/v1/entries/1')).json()
    assert detail['ai']['state']=='done'
    assert detail['ai']['content_quality']['recommendation_eligible'] is False
    assert detail['ai']['content_quality']['access']=='unknown'
    assert 'publisher_nonfree_pending_review' in detail['ai']['content_quality']['reason_codes']
    assert detail['content']=='<p>Only a teaser.</p>'
    set_quality(1,False)
    after=await page(0)
    assert after['total']==4 and after['entries'][0]['id']==1
    assert after['entries'][0]['ai']['content_quality']['recommendation_eligible'] is True


@pytest.mark.asyncio
async def test_false_eligibility_does_not_remove_raw_article_or_notes(quality_api):
    set_quality(1,True)
    with core.connect() as db:
        db.execute('INSERT INTO entry_notes VALUES(1,1,?,?,?)',('Keep my note',1,2))
    response=await quality_api.get('/mf/v1/entries/1')
    assert response.status_code==200 and response.json()['id']==1
    with core.connect() as db:
        assert db.execute('SELECT note FROM entry_notes').fetchone()[0]=='Keep my note'


def test_receipt_cannot_cross_owner_url_or_source(db,entry):
    core.discover([entry])
    core.update(1,source_text='Original text',content_hash='body-v1')
    set_quality(1,True)
    with core.connect() as connection:
        original=dict(connection.execute('SELECT * FROM analyses WHERE entry_id=1').fetchone())
    assert public_for_row(original)['recommendation_eligible'] is False
    for key,value in [('user_id',2),('url','https://other.example/article'),('content_hash','body-v2'),('source_text','Changed body')]:
        assert public_for_row({**original,key:value})['recommendation_eligible'] is None
    fake=json.loads(original['content_quality'])
    fake['binding']['user_id']=True
    assert public_for_row({**original,'content_quality':json.dumps(fake)})['recommendation_eligible'] is None


def test_review_cas_rejects_wrong_identity_source_or_prior_receipt(db,entry):
    core.discover([entry])
    core.update(1,source_text='Original text',content_hash='body-v1')
    with core.connect() as connection:
        original=dict(connection.execute('SELECT * FROM analyses WHERE entry_id=1').fetchone())
    record=assess(url=entry['url'],text='Plain technical text.',extraction_state='available')
    for key,value in [('user_id',2),('url','https://other.example/article'),('content_hash','body-v2'),('source_text','Changed body')]:
        assert core.record_content_quality({**original,key:value},record) is False
    assert core.record_content_quality(original,record) is True
    assert core.record_content_quality(original,record,expected_quality=None) is False


def test_quality_write_rolls_back_when_admission_changes(db,entry):
    from work_admission import AdmissionStopped
    core.discover([entry])
    with core.connect() as connection:
        original=dict(connection.execute('SELECT * FROM analyses WHERE entry_id=1').fetchone())
    record=assess(url=entry['url'],text='Plain technical text.',extraction_state='available')
    calls=[]
    def guard():
        calls.append(True)
        if len(calls)==3:
            raise AdmissionStopped('stop before commit')
    with pytest.raises(AdmissionStopped):
        core.record_content_quality(original,record,admission=guard)
    with core.connect() as connection:
        assert dict(connection.execute('SELECT * FROM analyses WHERE entry_id=1').fetchone())==original
