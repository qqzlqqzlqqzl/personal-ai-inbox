"""Selection parity and failure isolation for the opt-in metadata transport."""
import asyncio
import copy
import json
import threading
from urllib.parse import urlencode

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from starlette.requests import Request

import api
import core
import notes_metadata as nm


class Upstream:
    def __init__(self, n=200):
        self.entries = {i: dict(id=i, user_id=1, feed_id=1, title=f'Article {i}',
             url=f'https://example.test/article/{i}',
             published_at='2026-09-28T00:00:00Z', changed_at='2026-09-28T00:00:00Z',
             status='unread', starred=False, content='<p>body</p>',
             feed=dict(id=1, title='Feed', category=dict(id=10))) for i in range(1,n+1)}
        self.feeds = [dict(id=1, user_id=1, hide_globally=False, category=dict(id=10, user_id=1, hide_globally=False)),
                      dict(id=2, user_id=1, hide_globally=False, category=dict(id=20, user_id=1, hide_globally=False))]
        self.calls = []
        self.fail_bodies = {}
        self.metadata_status = 200
        self.metadata_header = '1'
        self.transform = lambda rows: rows
        self.on_body = lambda eid: None
        self.metadata_requests = []
        self.active = self.peak = 0

    async def __call__(self, req):
        path = req.url.path
        self.calls.append((req.method,path))
        if path.endswith('/me'):
            return httpx.Response(200,json=dict(id=1,is_admin=True))
        if path.endswith('/entries/ids'):
            p=req.url.params
            ids=[i for i,e in self.entries.items() if e['status']==p.get('status')
                 and (p.get('starred') is None or e['starred']==(p['starred']=='true'))]
            offset,limit=int(p.get('offset',0)),int(p.get('limit',10000))
            return httpx.Response(200,json=dict(entry_ids=ids[offset:offset+limit],total=len(ids)))
        if path.endswith('/feeds'):
            return httpx.Response(200,json=self.feeds)
        if path.endswith('/entries/metadata'):
            assert req.method=='POST'
            assert req.headers.get('X-Auth-Token')=='user-one'
            ids=json.loads(req.content)['entry_ids']
            self.metadata_requests.append(ids)
            rows=[{k:self.entries[i].get(k) for k in nm.FIELDS} for i in reversed(ids)
                  if i in self.entries and self.entries[i]['status'] in ('read','unread')
                  and self.entries[i]['user_id']==1]
            return httpx.Response(self.metadata_status,json=dict(entries=self.transform(rows)),
                                  headers={'X-Reader-Entry-Metadata':self.metadata_header})
        eid=int(path.rsplit('/',1)[-1])
        self.active+=1
        self.peak=max(self.peak,self.active)
        try:
            await asyncio.sleep(0)
            self.on_body(eid)
            status=self.fail_bodies.get(eid,200 if eid in self.entries else 404)
            return httpx.Response(status,json=self.entries.get(eid,{}))
        finally:
            self.active-=1

    @property
    def bodies(self):
        return [int(path.rsplit('/',1)[-1]) for method,path in self.calls
                if method=='GET' and path.rsplit('/',1)[-1].isdigit()]


@pytest_asyncio.fixture
async def notes(db, monkeypatch):
    monkeypatch.setenv('READER_NOTES_METADATA','1')
    monkeypatch.delenv('MINIFLUX_API_KEY',raising=False)
    upstream=Upstream()
    with core.connect() as c:
        c.executemany('INSERT INTO entry_notes(user_id,entry_id,note,created_at,updated_at) VALUES(1,?,?,0,?)',
                      [(i,f'note {i}',float(i)) for i in upstream.entries])
    monkeypatch.setattr(api,'enrich_reader_entries',lambda entries, uid: entries)
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
        monkeypatch.setattr(api.app.state,'client',client,raising=False)
        yield upstream


async def listing(**params):
    feed_id=params.pop('feed_id',None)
    category_id=params.pop('category_id',None)
    request=Request({'type':'http','method':'GET','path':'/mf/v1/entries',
                     'query_string':urlencode(dict(ai_view='notes',ai_min=0,**params)).encode(),
                     'headers':[(b'x-auth-token',b'user-one')]})
    return await api.ai_entries(request,1,feed_id=feed_id,category_id=category_id)


@pytest.mark.asyncio
async def test_200_candidates_24_matches_only_page_bodies(notes):
    for i,e in notes.entries.items():
        if i<=24:e['title']='target'
        else:notes.fail_bodies[i]=500
    result=await listing(search='target',limit=24)
    assert result['total']==24
    assert [e['id'] for e in result['entries']]==list(range(24,0,-1))
    assert set(notes.bodies)==set(range(1,25))
    assert len(notes.calls)==28
    assert len(notes.metadata_requests)==1 and len(notes.metadata_requests[0])==200
    assert 1 < notes.peak <= 8
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM analyses').fetchone()[0]==0


@pytest.mark.asyncio
async def test_same_scope_offpage_and_offscope_500_not_contacted(notes):
    notes.entries[1]['feed_id']=2
    notes.fail_bodies={i:500 for i in range(1,177)}
    result=await listing(feed_id=1,limit=24)
    assert result['total']==199
    assert notes.bodies==list(range(200,176,-1))
    notes.calls.clear()
    result=await listing(offset=10000)
    assert result==dict(total=200,entries=[])
    assert notes.bodies==[]


@pytest.mark.asyncio
async def test_selected_500_explicit_and_recovers(notes):
    notes.fail_bodies[200]=500
    with pytest.raises(HTTPException) as err:await listing(limit=24)
    assert err.value.status_code==503
    assert len(notes.metadata_requests)==1
    notes.fail_bodies.clear()
    assert (await listing(limit=24))['total']==200


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[401,403])
async def test_selected_auth_loss_is_failure_not_deletion(notes,status):
    notes.fail_bodies[200]=status
    with pytest.raises(HTTPException) as err:await listing(limit=1)
    assert err.value.status_code==503
    assert len(notes.metadata_requests)==1


@pytest.mark.asyncio
async def test_disappeared_selected_row_refreshes_total_and_refills(notes):
    def remove(eid):
        if eid==200:notes.entries.pop(200,None)
    notes.on_body=remove
    result=await listing(limit=2)
    assert result['total']==199
    assert [e['id'] for e in result['entries']]==[199,198]
    assert notes.bodies==[200,199,199,198]
    assert len(notes.metadata_requests)==2


@pytest.mark.asyncio
async def test_repeated_churn_fails_retryably_with_bounded_bodies(notes):
    notes.on_body=lambda eid:notes.entries[eid].update(title=notes.entries[eid]['title']+' changed')
    with pytest.raises(HTTPException) as err:await listing(limit=24)
    assert err.value.status_code==503 and err.value.headers['Retry-After']=='1'
    assert len(notes.bodies)==48
    assert len(notes.metadata_requests)==2


@pytest.mark.asyncio
async def test_title_change_without_changed_at_is_fresh(notes):
    first=await listing(search='Article 200')
    assert first['total']==1
    before=notes.entries[200]['changed_at']
    notes.entries[200]['title']='Straße %_ 特殊标题'
    notes.calls.clear()
    assert (await listing(search='STRASSE %_'))['total']==1
    notes.calls.clear()
    assert (await listing(search='Article 200'))['total']==0
    assert notes.bodies==[] and notes.entries[200]['changed_at']==before


@pytest.mark.asyncio
async def test_body_title_race_reselects_instead_of_stale_total(notes):
    def change(eid):
        if eid==200:notes.entries[eid]['title']='different'
    notes.on_body=change
    result=await listing(search='Article 200',limit=1)
    assert result==dict(total=0,entries=[])
    assert notes.bodies==[200]
    assert len(notes.metadata_requests)==2


@pytest.mark.asyncio
async def test_moving_feed_and_hidden_category_reflected(notes):
    notes.entries[200]['feed_id']=2
    notes.entries[200]['feed']=dict(id=2,category=dict(id=20))
    assert (await listing(category_id=20))['total']==1
    notes.feeds[1]['category']['hide_globally']=True
    notes.entries[200]['feed']['category']['hide_globally']=True
    assert (await listing(category_id=20,globally_visible='true'))['total']==0
    assert (await listing(category_id=20))['total']==1


@pytest.mark.asyncio
async def test_note_only_unicode_literal_truncation_and_empty_sql_rule(notes):
    with core.connect() as c:
        c.execute("UPDATE entry_notes SET note='Straße %_ 手记' WHERE entry_id=199")
        c.execute("UPDATE entry_notes SET note='   ' WHERE entry_id=200")
        c.execute("UPDATE entry_notes SET note=? WHERE entry_id=198",('x'*200,))
        c.execute("INSERT INTO entry_notes VALUES(2,200,'private secret',0,99999)")
    assert [e['id'] for e in (await listing(search='STRASSE %_'))['entries']]==[199]
    assert (await listing(search='private secret'))['total']==0
    assert (await listing(search='x'*200+'ignored'))['total']==1
    assert (await listing())['total']==199


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',[
    lambda rows:[dict(rows[0],user_id=2)],
    lambda rows:[dict(rows[0],id=999999)],
    lambda rows:[rows[0],rows[0]],
    lambda rows:[dict(rows[0],content='forbidden')],
    lambda rows:[dict(rows[0],id=True)],
    lambda rows:[dict(rows[0],title=None)],
    lambda rows:[{key:value for key,value in rows[0].items() if key!='url'}],
    lambda rows:[dict(rows[0],url=None)],
    lambda rows:[dict(rows[0],url='')],
])
async def test_untrusted_metadata_fails_closed(notes,mutation):
    notes.transform=mutation
    with pytest.raises(HTTPException) as err:await listing()
    assert err.value.status_code==503 and notes.bodies==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[400,401,403,404,413,500,503])
async def test_provider_errors_never_fallback_or_zero_success(notes,status):
    notes.metadata_status=status
    with pytest.raises(HTTPException) as err:await listing()
    assert err.value.status_code==503 and notes.bodies==[]


@pytest.mark.asyncio
async def test_capability_mismatch_fails_closed(notes):
    notes.metadata_header='2'
    with pytest.raises(HTTPException) as err:await listing()
    assert err.value.status_code==503 and notes.bodies==[]


@pytest.mark.asyncio
async def test_more_than_1000_candidates_one_snapshot_and_explicit_limit(notes):
    original=copy.deepcopy(notes.entries[1])
    for i in range(201,10002):notes.entries[i]={**original,'id':i,'title':f'Article {i}'}
    with core.connect() as c:
        c.executemany('INSERT INTO entry_notes VALUES(1,?,?,0,?)',[(i,'legacy',i) for i in range(201,10001)])
    result=await listing(limit=1)
    assert result['total']==10000 and len(notes.metadata_requests)==1
    assert len(notes.metadata_requests[0])==10000 and notes.bodies==[10000]
    with core.connect() as c:c.execute('INSERT INTO entry_notes VALUES(1,10001,\'legacy\',0,10001)')
    notes.calls.clear()
    with pytest.raises(HTTPException) as err:await listing(limit=1)
    assert err.value.status_code==503 and '10000' in err.value.detail
    assert notes.bodies==[]
    assert not any(path.endswith('/metadata') for _,path in notes.calls)


@pytest.mark.asyncio
async def test_metadata_omission_preserves_local_note(notes):
    notes.transform=lambda rows:[r for r in rows if r['id']!=200]
    result=await listing(limit=1)
    assert result['total']==199 and result['entries'][0]['id']==199
    with core.connect() as c:assert c.execute('SELECT note FROM entry_notes WHERE entry_id=200').fetchone()[0]=='note 200'


@pytest.mark.asyncio
async def test_selection_cpu_work_uses_bounded_worker(notes,monkeypatch):
    main=threading.get_ident()
    for name in ['request_body','decode_metadata','decode_feeds','select_page','body_matches']:
        original=getattr(nm,name)
        def checked(*args,_fn=original,**kwargs):
            assert threading.get_ident()!=main
            return _fn(*args,**kwargs)
        monkeypatch.setattr(nm,name,checked)
    assert (await listing(limit=2))['total']==200


@pytest.mark.asyncio
async def test_stable_selection_matches_legacy_matrix(notes,monkeypatch):
    # Differential oracle is the unchanged release function, including its literal
    # search and timestamp fallback. At least 274 full selections, not mocked SQL.
    notes.entries={i:e for i,e in notes.entries.items() if i<=8}
    for i,e in notes.entries.items():
        e['title']=['Straße %_','Σίσυφος','标题', 'other'][i%4]
        e['status']='read' if i%2 else 'unread'
        e['starred']=bool(i%3)
        e['published_at']=['2026-09-28T00:00:00Z','2026-09-28T00:00:00.500Z',
                           '2026-09-28T08:00:00+08:00','invalid'][i%4]
        if i>4:e['feed_id']=2;e['feed']=dict(id=2,category=dict(id=20))
    with core.connect() as c:
        c.execute('UPDATE entry_notes SET updated_at=100')
        c.execute("UPDATE entry_notes SET note='note Straße %_' WHERE entry_id=3")
    stamp=1790553600
    cases=[{}]
    for direction in ['asc','desc']:
      for sort in ['time','note_updated']:
       for term in ['', 'strasse', '%_', 'ΣΊΣΥΦΟΣ', '标题', 'note', 'missing']:
        for scope in [{},{'feed_id':1},{'category_id':20}]:
         for page in [{'limit':2},{'limit':2,'offset':2},{'limit':0,'offset':-1}]:
          cases.append(dict(direction=direction,ai_sort=sort,search=term,**scope,**page))
    cases += [dict(**{key:stamp},status=st) for key in ['after','before','published_after','published_before']
              for st in ['read','unread']]
    cases += [dict(starred=s,globally_visible=g,limit=1000,offset=off)
              for s in ['true','false'] for g in ['true','false'] for off in [0,1,999,1000]]
    assert len(cases)>=274
    for params in cases:
        monkeypatch.setenv('READER_NOTES_METADATA','0')
        old=await listing(**params)
        monkeypatch.setenv('READER_NOTES_METADATA','1')
        new=await listing(**params)
        assert new==old, params


def test_maximum_request_size_and_id_validation():
    ids=[2**63-1-i for i in range(10000)]
    _,body=nm.request_body([dict(entry_id=i) for i in ids],set(ids))
    assert len(body)<256<<10
    for invalid in [True,0,-1,2**63]:
        with pytest.raises(ValueError):nm.request_body([dict(entry_id=invalid)],{invalid})


@pytest.mark.asyncio
async def test_cold_legacy_notes_real_decoration_and_warm_zero_dml(notes,monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    import processing_status
    # Compare both real decorations at the same observation time.
    monkeypatch.setattr(processing_status,'time',SimpleNamespace(time=lambda:1791209000.0))
    # Resolve the real function from its unchanged definition rather than the
    # fixture's selection-only identity decorator.
    import ast
    source=ast.parse(open(api.__file__).read())
    fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='enrich_reader_entries')
    namespace=dict(vars(api))
    exec(compile(ast.Module(body=[fn],type_ignores=[]),api.__file__,'exec'),namespace)
    monkeypatch.setattr(api,'enrich_reader_entries',namespace['enrich_reader_entries'])
    core.save_settings({'enabled':False,'translation_enabled':False})
    first=await listing(limit=24)
    assert len(first['entries'])==24
    assert all(e['ai']['has_note'] and e['ai']['state']=='pending' for e in first['entries'])
    with core.connect() as c:
        assert c.execute('SELECT count(*) FROM analyses').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM card_translations').fetchone()[0]==24
    original=core.connect
    statements=[]
    @contextmanager
    def traced():
        with original() as c:
            c.set_trace_callback(statements.append)
            yield c
    monkeypatch.setattr(core,'connect',traced)
    monkeypatch.setattr(api,'connect',traced)
    second=await listing(limit=24)
    assert second==first
    assert not [s for s in statements if s.lstrip().split()[0].upper() in
                {'INSERT','UPDATE','DELETE','REPLACE','BEGIN','COMMIT','ROLLBACK'}]


@pytest.mark.asyncio
async def test_http_retry_header_is_preserved(notes):
    notes.on_body=lambda eid:notes.entries[eid].update(title=notes.entries[eid]['title']+'!')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://test',
                                  headers={'X-Auth-Token':'user-one'}) as client:
        response=await client.get('/mf/v1/entries',params={'ai_view':'notes','limit':1})
    assert response.status_code==503 and response.headers['Retry-After']=='1'


@pytest.mark.asyncio
async def test_default_switch_retains_known_good_path(notes,monkeypatch):
    monkeypatch.delenv('READER_NOTES_METADATA')
    assert (await listing(limit=1))['total']==200
    assert len(notes.bodies)==200 and notes.metadata_requests==[]
    monkeypatch.setenv('READER_NOTES_METADATA','true')
    with pytest.raises(HTTPException) as err:await listing(limit=1)
    assert err.value.status_code==503


@pytest.mark.asyncio
@pytest.mark.parametrize('envelope',[{'total':200},{'entry_ids':[],'total':200},
    {'entry_ids':[1],'total':0},{'entry_ids':[True],'total':1},{'entry_ids':[1,1],'total':2}])
async def test_invalid_visibility_never_fabricates_empty_success(notes,monkeypatch,envelope):
    original=api.app.state.client.get
    async def get(url,**kwargs):
        if url.endswith('/entries/ids'):
            return httpx.Response(200,json=envelope,request=httpx.Request('GET',url))
        return await original(url,**kwargs)
    monkeypatch.setattr(api.app.state.client,'get',get)
    with pytest.raises(HTTPException) as err:await listing(limit=1)
    assert err.value.status_code==503 and notes.bodies==[] and notes.metadata_requests==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',[
    lambda feed:feed.pop('category'),
    lambda feed:feed.update(category=[]),
    lambda feed:feed['category'].pop('id'),
    lambda feed:feed.pop('hide_globally'),
    lambda feed:feed['category'].pop('hide_globally'),
    lambda feed:feed.update(user_id=True),
    lambda feed:feed['category'].update(user_id=2),
])
async def test_incomplete_feed_visibility_fails_closed(notes,mutation):
    mutation(notes.feeds[0])
    with pytest.raises(HTTPException) as err:await listing(category_id=10)
    assert err.value.status_code==503 and notes.bodies==[]
