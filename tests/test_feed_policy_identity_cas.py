"""Actual SQLite races, synthetic DTOs only; no provider or real Miniflux."""
from unittest.mock import AsyncMock
from contextlib import contextmanager
import sqlite3
import pytest
import core,worker,preview_worker

FEED='https://www.kicktraq.com/categories/technology/latest.rss'
MARKER='rss_summary_only:kicktraq-rss-preview-only-v1'

def saved():
    with core.connect() as db:return dict(db.execute('SELECT * FROM analyses WHERE entry_id=1').fetchone())

# The six scenarios and safety assertion supplied by the main operator.
@pytest.mark.parametrize('change',['user_id','id','url'])
@pytest.mark.asyncio
async def test_main_worker_dto_identity_negative(db,entry,monkeypatch,change):
    core.discover([entry]);before=saved()
    dto={**entry,'feed':{**entry['feed'],'feed_url':FEED}}
    dto[change]='https://example.org/changed' if change=='url' else 2
    monkeypatch.setattr(worker,'mf_get',AsyncMock(return_value=dto))
    await worker.process_one(None,before,core.settings())
    after=saved()
    assert after['error']!=MARKER
    assert after==before

@pytest.mark.parametrize('field',['state','content_hash','source_text'])
@pytest.mark.asyncio
async def test_main_worker_winner_during_await_negative(db,entry,monkeypatch,field):
    core.discover([entry]);before=saved();winner=[]
    dto={**entry,'feed':{**entry['feed'],'feed_url':FEED}}
    async def read(*args,**kwargs):
        core.update(1,**{field:'done' if field=='state' else 'concurrent winner'})
        winner.append(saved());return dto
    monkeypatch.setattr(worker,'mf_get',AsyncMock(side_effect=read))
    await worker.process_one(None,before,core.settings())
    after=saved()
    assert after['error']!=MARKER
    assert after==winner[0]

class Response:
    def __init__(self,entry):self.entry=entry
    def raise_for_status(self):pass
    def json(self):return self.entry

@pytest.mark.parametrize('scheme',['https','http'])
@pytest.mark.parametrize('change',['user_id','id','url'])
@pytest.mark.asyncio
async def test_preview_policy_dto_identity_negative(db,entry,monkeypatch,change,scheme):
    core.discover([entry]);core.update(1,cover_url='https://example.org/winner.jpg',cover_source='winner')
    before=saved();dto={**entry,'feed':{**entry['feed'],'feed_url':FEED.replace('https:',scheme+':')}}
    dto[change]='https://example.org/changed' if change=='url' else 2
    client=type('Client',(),{'get':AsyncMock(return_value=Response(dto))})()
    await preview_worker.prepare_one(client,{'entry_id':1})
    assert saved()==before

@pytest.mark.parametrize('scheme',['https','http'])
@pytest.mark.parametrize('field',['state','content_hash','source_text','cover_url','preview_error'])
@pytest.mark.asyncio
async def test_preview_policy_winner_during_await_negative(db,entry,monkeypatch,field,scheme):
    core.discover([entry]);winner=[]
    dto={**entry,'feed':{**entry['feed'],'feed_url':FEED.replace('https:',scheme+':')}}
    async def read(*args,**kwargs):
        core.update(1,**{field:'done' if field=='state' else 'concurrent winner'})
        winner.append(saved());return Response(dto)
    client=type('Client',(),{'get':AsyncMock(side_effect=read)})()
    await preview_worker.prepare_one(client,{'entry_id':1})
    assert saved()==winner[0]

@pytest.mark.parametrize('field,value',[('id',True),('user_id',True),('feed_id',True),
                                      ('title','Changed title'),('feed_id',2),('feed.id',2)])
@pytest.mark.parametrize('kind',['worker','preview'])
@pytest.mark.asyncio
async def test_policy_source_tuple_and_types_are_not_coerced(db,entry,monkeypatch,field,value,kind):
    core.discover([entry]);before=saved();dto={**entry,'feed':{**entry['feed'],'feed_url':FEED}}
    if field=='feed.id':dto['feed']['id']=value
    else:dto[field]=value
    if kind=='worker':
        monkeypatch.setattr(worker,'mf_get',AsyncMock(return_value=dto))
        await worker.process_one(None,before,core.settings())
    else:
        client=type('Client',(),{'get':AsyncMock(return_value=Response(dto))})()
        await preview_worker.prepare_one(client,{'entry_id':1})
    assert saved()==before

@pytest.mark.parametrize('kind',['worker','preview'])
@pytest.mark.asyncio
async def test_policy_row_deleted_during_await_is_not_recreated(db,entry,monkeypatch,kind):
    core.discover([entry]);before=saved();dto={**entry,'feed':{**entry['feed'],'feed_url':FEED}}
    async def read(*args,**kwargs):
        with core.connect() as conn:conn.execute('DELETE FROM analyses WHERE entry_id=1')
        return dto if kind=='worker' else Response(dto)
    if kind=='worker':
        monkeypatch.setattr(worker,'mf_get',AsyncMock(side_effect=read));await worker.process_one(None,before,core.settings())
    else:await preview_worker.prepare_one(type('Client',(),{'get':AsyncMock(side_effect=read)})(),{'entry_id':1})
    with core.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM analyses').fetchone()[0]==0

@pytest.mark.parametrize('kind',['worker','preview'])
@pytest.mark.asyncio
async def test_policy_transaction_abort_does_not_fall_back_to_unbound_error_write(db,entry,monkeypatch,kind):
    core.discover([entry]);before=saved();dto={**entry,'feed':{**entry['feed'],'feed_url':FEED}}
    with core.connect() as conn:
        predicate="NEW.error='"+MARKER+"'" if kind=='worker' else 'NEW.preview_checked_at IS NOT NULL'
        conn.execute("CREATE TRIGGER reject_policy BEFORE UPDATE ON analyses WHEN "+predicate+" BEGIN SELECT RAISE(ABORT,'synthetic transaction failure'); END")
    if kind=='worker':
        monkeypatch.setattr(worker,'mf_get',AsyncMock(return_value=dto))
        with pytest.raises(sqlite3.IntegrityError):await worker.process_one(None,before,core.settings())
    else:
        client=type('Client',(),{'get':AsyncMock(return_value=Response(dto))})()
        result=await preview_worker.prepare_one(client,{'entry_id':1});assert result['result']=='failed'
    assert saved()==before
    with core.connect() as conn:assert conn.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0

@pytest.mark.parametrize('kind',['worker','preview'])
@pytest.mark.asyncio
async def test_actual_second_connection_winner_between_validation_and_policy_update(db,entry,monkeypatch,kind):
    core.discover([entry]);before=saved();dto={**entry,'feed':{**entry['feed'],'feed_url':FEED}}
    original=core.connect;winner=[]
    class Connection:
        def __init__(self,connection):self.connection=connection
        def execute(self,sql,args=()):
            if sql.startswith('UPDATE analyses SET') and not winner:
                winner.append(True)
                with original() as other:other.execute("UPDATE analyses SET source_text='independent SQL winner' WHERE entry_id=1")
            return self.connection.execute(sql,args)
    @contextmanager
    def connected():
        with original() as conn:yield Connection(conn)
    if kind=='worker':
        monkeypatch.setattr(worker,'connect',connected)
        monkeypatch.setattr(worker,'mf_get',AsyncMock(return_value=dto));await worker.process_one(None,before,core.settings())
    else:
        monkeypatch.setattr(core,'connect',connected)
        await preview_worker.prepare_one(type('Client',(),{'get':AsyncMock(return_value=Response(dto))})(),{'entry_id':1})
    assert winner==[True];assert saved()=={**before,'source_text':'independent SQL winner'}

@pytest.mark.asyncio
async def test_replayed_worker_snapshot_is_noop_and_done_is_preserved(db,entry,monkeypatch):
    core.discover([entry]);before=saved();dto={**entry,'feed':{**entry['feed'],'feed_url':FEED}}
    monkeypatch.setattr(worker,'mf_get',AsyncMock(return_value=dto))
    await worker.process_one(None,before,core.settings());after=saved();assert after['error']==MARKER
    await worker.process_one(None,before,core.settings());assert saved()==after
    core.update(1,state='done',score=8.1,error=None);done=saved()
    await worker.process_one(None,done,core.settings());assert saved()==done
