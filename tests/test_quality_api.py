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
    metadata_calls=[]
    body_calls=[]
    metadata_control={'transform':lambda rows:rows, 'status':200, 'on_body':lambda eid:None}
    def upstream(request):
        path=request.url.path
        if path.endswith('/v1/me'):
            return httpx.Response(200,json={'id':1,'is_admin':True})
        if path.endswith('/entries/ids'):
            return httpx.Response(200,json={'entry_ids':list(entries),'total':len(entries)})
        if path.endswith('/v1/feeds'):
            return httpx.Response(200,json=[entry['feed']])
        if path.endswith('/entries/metadata'):
            assert request.method=='POST'
            ids=json.loads(request.content)['entry_ids']
            metadata_calls.append(ids)
            rows=[{key:entries[eid][key] for key in ('id','user_id','feed_id','title','url','published_at')} for eid in ids if eid in entries]
            return httpx.Response(metadata_control['status'],json={'entries':metadata_control['transform'](rows)},headers={'X-Reader-Entry-Metadata':'1'})
        if '/v1/entries/' in path:
            eid=int(path.rsplit('/',1)[1])
            body_calls.append(eid)
            metadata_control['on_body'](eid)
            return httpx.Response(200,json=entries[eid])
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as upstream_client:
        monkeypatch.setattr(api.app.state,'client',upstream_client,raising=False)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://testserver',headers={'X-Auth-Token':'synthetic'}) as client:
            client.synthetic_entries=entries
            client.metadata_calls=metadata_calls
            client.body_calls=body_calls
            client.metadata_control=metadata_control
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


@pytest.mark.asyncio
async def test_changed_current_url_is_unknown_in_detail_total_and_page(quality_api):
    set_quality(1,True)
    quality_api.synthetic_entries[1]['url']='https://example.org/current-different-article'
    detail=(await quality_api.get('/mf/v1/entries/1')).json()
    assert detail['ai']['content_quality']['recommendation_eligible'] is None
    page=(await quality_api.get('/mf/v1/entries?ai_view=recommended&limit=1&ai_min=6')).json()
    assert page['total']==4 and page['entries'][0]['id']==1
    assert page['entries'][0]['url']=='https://example.org/current-different-article'
    assert page['entries'][0]['ai']['content_quality']['recommendation_eligible'] is None


@pytest.mark.asyncio
async def test_legacy_done_without_captured_body_keeps_selection_without_fake_fulltext_coverage(quality_api):
    core.update(1,source_text=None,content_hash=None,source_chars=0,input_chars=0,content_quality=None)
    page=(await quality_api.get('/mf/v1/entries?ai_view=recommended&limit=1&ai_min=6')).json()
    assert page['total']==4 and page['entries'][0]['id']==1
    ai=page['entries'][0]['ai']
    assert ai['state']=='done' and ai['content_quality']['recommendation_eligible'] is None
    assert ai['source_chars']==0 and ai['input_chars']==0
    coverage=core.status_summary(1)['coverage']
    assert coverage['ai_done']==4 and coverage['source_text_ready']==0


@pytest.mark.asyncio
async def test_sixty_five_exclusions_use_one_metadata_snapshot_and_only_page_bodies(quality_api,model_result):
    template=quality_api.synthetic_entries[1]
    additions={eid:{**copy.deepcopy(template),'id':eid,'title':f'Synthetic {eid}','url':f'https://example.org/article-{eid}'} for eid in range(10,75)}
    quality_api.synthetic_entries.update(additions)
    core.discover(additions.values())
    for eid in additions:
        core.update(eid,state='done',source_text='Captured original text',content_hash='synthetic-source',score=9,result=json.dumps(model_result))
        set_quality(eid,True)
    pages=[]
    for offset in (0,2):
        response=await quality_api.get(f'/mf/v1/entries?ai_view=recommended&ai_min=6&limit=2&offset={offset}')
        assert response.status_code==200
        pages.append(response.json())
    assert [page['total'] for page in pages]==[4,4]
    assert [entry['id'] for page in pages for entry in page['entries']]==[1,2,3,4]
    assert len(quality_api.metadata_calls)==2 and all(len(ids)==65 for ids in quality_api.metadata_calls)
    assert quality_api.body_calls==[1,2,3,4]


@pytest.mark.asyncio
@pytest.mark.parametrize('transform',[
    lambda rows:[{key:value for key,value in rows[0].items() if key!='url'}],
    lambda rows:[dict(rows[0],url=None)],
    lambda rows:[dict(rows[0],url=123)],
    lambda rows:[dict(rows[0],url='')],
    lambda rows:[dict(rows[0],user_id=2)],
])
async def test_old_dto_bad_url_and_cross_user_fail_without_body_fallback(quality_api,transform):
    set_quality(1,True)
    quality_api.metadata_control['transform']=transform
    response=await quality_api.get('/mf/v1/entries?ai_view=recommended&ai_min=6&limit=2')
    assert response.status_code==503
    assert 'URL' in response.json()['error_message']
    assert quality_api.body_calls==[]


@pytest.mark.asyncio
async def test_unstable_metadata_url_never_returns_false_recommendation(quality_api):
    set_quality(1,True)
    quality_api.metadata_control['transform']=lambda rows:[dict(rows[0],url='https://example.org/contradictory-url')]
    response=await quality_api.get('/mf/v1/entries?ai_view=recommended&ai_min=6&limit=1')
    assert response.status_code==503
    assert len(quality_api.metadata_calls)==2 and quality_api.body_calls==[1,1]


@pytest.mark.asyncio
async def test_one_url_churn_refreshes_snapshot_and_count_once(quality_api):
    set_quality(1,True)
    old_url=quality_api.synthetic_entries[1]['url']
    quality_api.synthetic_entries[1]['url']='https://example.org/current-b'
    quality_api.metadata_control['on_body']=lambda eid:quality_api.synthetic_entries[1].update(url=old_url)
    response=await quality_api.get('/mf/v1/entries?ai_view=recommended&ai_min=6&limit=1')
    assert response.status_code==200
    assert response.json()['total']==3 and response.json()['entries'][0]['id']==2
    assert len(quality_api.metadata_calls)==2 and quality_api.body_calls==[1,2]


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
