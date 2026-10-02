import json
from datetime import datetime,timezone
from pathlib import Path
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from agent_status_api import create_router
from agent_status_store import record_pull

def client():
    async def authorize(request,*,admin=False):
        assert admin is True
        if request.headers.get('x-test-role')=='user':raise HTTPException(403)
        if request.headers.get('x-test-role')!='admin':raise HTTPException(401)
    app=FastAPI();app.include_router(create_router(authorize));return TestClient(app)

def test_auth_checked_before_cache_read(tmp_path,monkeypatch):
    monkeypatch.setenv('AGENT_STATUS_STATE_PATH',str(tmp_path/'private.json'))
    c=client()
    assert c.get('/mf/v1/ai/agent-status').status_code==401
    assert c.get('/mf/v1/ai/agent-status',headers={'x-test-role':'user'}).status_code==403

def test_disabled_or_bad_cache_is_unknown_not_idle(tmp_path,monkeypatch):
    c=client();monkeypatch.delenv('AGENT_STATUS_STATE_PATH',raising=False)
    r=c.get('/mf/v1/ai/agent-status',headers={'x-test-role':'admin'})
    assert r.status_code==200 and r.json()['sample'] is None and r.json()['freshness']=='unknown'
    assert r.headers['cache-control']=='no-store'
    private=tmp_path/'private.json';private.write_text('{"prompt":"PRIVATE_MARKER"}')
    monkeypatch.setenv('AGENT_STATUS_STATE_PATH',str(private))
    r=c.get('/mf/v1/ai/agent-status',headers={'x-test-role':'admin'})
    assert r.json()['sample'] is None and 'PRIVATE_MARKER' not in r.text
    private.write_text(json.dumps({'sample':None,'last_successful_pull_at':None,'last_attempt_at':{'secret':'PRIVATE_MARKER'},'pull_status':'failed','error_code':None}))
    r=c.get('/mf/v1/ai/agent-status',headers={'x-test-role':'admin'})
    assert r.json()['sample'] is None and 'PRIVATE_MARKER' not in r.text

def test_valid_cache_keeps_observation_and_wait_count(tmp_path,monkeypatch):
    state=tmp_path/'state.json';observed='2026-10-02T04:34:08Z'
    sample={'schema_version':1,'sequence':2,'observed_at':observed,'tasks':[{'name':'模拟任务','state':'waiting','model':'gpt-6-astra'}],'statistics':{'total':1,'capacity':6,'active':1,'waiting':1,'completed':0,'failed':0,'unknown':0}}
    record_pull(state,json.dumps(sample).encode(),datetime.now(timezone.utc));monkeypatch.setenv('AGENT_STATUS_STATE_PATH',str(state))
    result=client().get('/mf/v1/ai/agent-status',headers={'x-test-role':'admin'}).json()
    assert result['sample']['observed_at']==observed and result['sample']['statistics']['active']==1
    assert result['sample']['statistics']['waiting']==1

def test_status_route_registered_before_generic_proxy():
    source=(Path(__file__).parents[1]/'src/api.py').read_text()
    assert source.index('app.include_router(create_agent_status_router(authorize))')<source.index('"/mf/{path:path}"')
