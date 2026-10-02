"""Independent PR95 counterexamples retained as corrected behavioral regressions.

Fixtures are synthetic; every provider, credential and HTTP boundary is mocked.
"""
import asyncio
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch
import pytest
from batch_control import Controller
from dispatch_policy import ConfigGuard,DispatchStopped,readonly_reconcile
from queue_dispatch import DispatchBlocked,claimed_entries
import test_fulltext_bridge
import cloud_bridge
import cloud_cycle
import qwen_exceptions as qe
from work_admission import http_client

@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr('time.time',lambda:1000)

def configuration(folder, root, enabled=True):
    value = {
        "schedule_enabled": enabled, "state_root": str(root), "peer_state_roots": [],
        "owner": "fixture", "kaggle_python": "never", "source": str(folder),
        "database": str(folder / "inbox.sqlite3"), "versions": str(folder / "versions.json"),
        "token_file": "unused", "model_dataset": "fixture/model",
        "runtime_sha256": "fixture", "runtime_source": "fixture/runtime",
        "qwen_exception_review": False,
    }
    path = folder / "config.json"
    path.write_text(json.dumps(value))
    return path, value

@pytest.mark.parametrize('stop_at',['miniflux','publisher','source_commit','card_enqueue'])
def test_real_bridge_stops_inside_extraction_after_first_request(stop_at):
    with tempfile.TemporaryDirectory(prefix="pr95-stop-extraction-") as temporary:
        folder = Path(temporary)
        root = folder / "own"
        Controller(root, "fixture", initialize=True)
        config_path, config = configuration(folder, root)
        Path(config["versions"]).write_text("{}")
        fixture = test_fulltext_bridge.FulltextBridgeTests()
        fixture.setUp()  # This fixture uses only an in-memory SQLite database.
        fixture.db.execute("""INSERT INTO analyses SELECT
            2,user_id,title,url,state,next_try,attempts,published_at,truncated,
            content_hash,source_text,content_source,source_chars,input_chars,
            image_count,extracted_at,updated_at,error FROM analyses WHERE entry_id=1""")
        fixture.db.commit()
        calls = []
        def stop():config_path.write_text(json.dumps({**config,'schedule_enabled':False}))
        def enqueue(entries,**kwargs):
            fixture.enqueued.append(entries)
            if stop_at=='card_enqueue':stop()
        fixture.cards.enqueue=enqueue
        original_connect=fixture.core.connect
        @contextlib.contextmanager
        def connect():
            with original_connect() as db:yield db
            if stop_at=='source_commit' and fixture.db.execute('SELECT source_text FROM analyses WHERE entry_id=2').fetchone()[0]=='Complete publisher article':stop()
        fixture.core.connect=connect

        async def mf_get(client, url):
            entry_id = int(url.rsplit("/", 1)[1])
            calls.append(["miniflux", entry_id, json.loads(config_path.read_text())["schedule_enabled"]])
            if len(calls)==1 and stop_at=='miniflux':stop()
            return {**fixture.entry,'id':entry_id}

        async def fetch(url,**kwargs):
            calls.append(["publisher", url, json.loads(config_path.read_text())["schedule_enabled"]])
            if stop_at=='publisher':stop()
            return fixture.body

        fixture.worker.mf_get = mf_get
        modules = {
            "initialize_secrets": SimpleNamespace(read_env=lambda *args: {}),
            "content_input": SimpleNamespace(content_text=lambda text: (text, 0), is_our_social_feed=lambda url: False),
            "product_source": SimpleNamespace(is_product_entry=lambda entry: False),
            "prepared_content": SimpleNamespace(apply=lambda entry,**kwargs: entry),
            "adafruit_source": SimpleNamespace(is_adafruit=lambda url: False, resolve=AsyncMock(),
                OriginalUnavailable=type("OriginalUnavailable", (Exception,), {})),
        }
        outer = None
        with patch.dict(sys.modules, modules), \
             patch.object(sys, "argv", ["cloud_bridge", "--config", str(config_path), "prepare"]), \
             patch("quota_guard.query_client", return_value={"allowed": True}), \
             patch.object(cloud_bridge, "validate_model_config"), \
             patch.object(cloud_bridge, "backup_before_import"), \
             patch.object(cloud_bridge, "resolve_entry_ids", return_value=[1, 2]), \
             patch.object(cloud_bridge, "load_inbox", return_value=(fixture.core, fixture.worker, fixture.cards)), \
             patch("fulltext_source.fetch", fetch):
            try:
                cloud_bridge.main()
            except DispatchStopped as error:
                outer = error.state
        rows = [dict(row) for row in fixture.db.execute(
            "SELECT entry_id,state,source_text FROM analyses ORDER BY entry_id")]
        result = {"outer_guard_result": outer, "calls_with_schedule_enabled": calls,
                  "rows_after_stop": rows, "cards_enqueued": len(fixture.enqueued)}
        fixture.tearDown()
        assert outer == "schedule_disabled"
        expected=[['miniflux',2,True]]
        if stop_at!='miniflux':expected.append(['publisher',fixture.entry['url'],True])
        if stop_at in {'source_commit','card_enqueue'}:expected.append(['miniflux',2,True])
        assert calls==expected
        assert rows[0]['source_text']=='Old RSS excerpt'
        assert rows[1]['source_text']==('Complete publisher article' if stop_at in {'source_commit','card_enqueue'} else 'Old RSS excerpt')
        assert result['cards_enqueued']==int(stop_at=='card_enqueue')
        assert claimed_entries([root]) == set()
        assert not list(root.glob("*/manifest.json"))

def test_real_bridge_stops_before_second_import_transaction():
    with tempfile.TemporaryDirectory(prefix="pr95-stop-import-") as temporary:
        folder = Path(temporary)
        root = folder / "own"
        database = folder / "inbox.sqlite3"
        control = Controller(root, "fixture", initialize=True)
        config_path, config = configuration(folder, root)
        with sqlite3.connect(database) as db:
            db.executescript("""CREATE TABLE analyses(entry_id INTEGER PRIMARY KEY,user_id INTEGER,
                content_hash TEXT,source_text TEXT,state TEXT,result TEXT,score REAL,technical_score REAL,
                business_score REAL,model TEXT,prompt_hash TEXT,tokens INTEGER,analyzed_at REAL,updated_at REAL,
                attempts INTEGER,next_try REAL,error TEXT);
                CREATE TABLE settings(name TEXT PRIMARY KEY,value TEXT);""")
            for entry_id in (1, 2):
                db.execute("INSERT INTO analyses(entry_id,user_id,content_hash,source_text,state) VALUES (?,2,'html','body','waiting_model')", (entry_id,))
        items = [{"id": "analysis-" + str(i), "kind": "analysis", "input_hash": "hash-" + str(i),
                  "source_refs": [{"entry_id": i, "user_id": 2, "content_hash": "html", "source_text": "body"}],
                  "messages": [{"role": "system", "content": "score"}]} for i in (1, 2)]
        manifest = {"session_timeout": 600, "runtime_source": "fixture/runtime",
                    "model": {"filename": "q.gguf", "model_revision": "abcdef123456789"}, "items": items}
        key = control.prepare(manifest, "MANIFEST = None\n")
        control._set(key, "downloaded", remote="COMPLETE")
        manifest = control.manifest(key)
        report = {"batch_id": key, "manifest_hash": manifest["manifest_hash"],
                  "valid": [item["id"] for item in items], "invalid": [],
                  "items": [{"id": item["id"], "input_hash": item["input_hash"], "valid": True,
                             "result": {"score": 7, "technical_score": 8, "business_score": 6}} for item in items]}
        real_connect = sqlite3.connect
        commits, begins = [], []

        class StopAfterFirstCommit(sqlite3.Connection):
            def execute(self, sql, *args):
                if sql == "BEGIN IMMEDIATE":
                    begins.append(json.loads(config_path.read_text())["schedule_enabled"])
                return super().execute(sql, *args)
            def __exit__(self, *args):
                result = super().__exit__(*args)
                commits.append(json.loads(config_path.read_text())["schedule_enabled"])
                if len(commits) == 1:
                    config_path.write_text(json.dumps({**config, "schedule_enabled": False}))

        def connect(path, *args, **kwargs):
            if "inbox.sqlite3" in str(path):
                kwargs["factory"] = StopAfterFirstCommit
            return real_connect(path, *args, **kwargs)

        async def upstream(*args,**kwargs):
            return {1, 2}

        outer = None
        with patch.dict(sys.modules, {"initialize_secrets": SimpleNamespace(read_env=lambda *args: {})}), \
             patch.object(sys, "argv", ["cloud_bridge", "--config", str(config_path), "advance", "--batch", key]), \
             patch.object(Controller, "download", return_value={"missing_ids": [], "results": [{"id": item["id"], "status": "ok"} for item in items]}), \
             patch.object(cloud_bridge, "validate", return_value=report), \
             patch.object(cloud_bridge, "verify_upstream", upstream), \
             patch.object(cloud_bridge, "backup_before_import"), \
             patch.object(sqlite3, "connect", connect):
            try:
                cloud_bridge.main()
            except DispatchStopped as error:
                outer = error.state
        with real_connect(database) as db:
            rows = db.execute("SELECT entry_id,state FROM analyses ORDER BY entry_id").fetchall()
        assert outer == "schedule_disabled" and begins == [True] and commits == [True]
        assert rows == [(1, "done"), (2, "waiting_model")]
        with real_connect(database) as db:
            assert db.execute("SELECT item_id FROM kaggle_imports").fetchall()==[("analysis-1",)]
        assert control.row(key)["state"]=="downloaded"
        assert claimed_entries([root])=={1,2}

@pytest.mark.parametrize('legacy',[False,True])
def test_manual_target_does_not_backfill_unrelated_pending_claims(legacy):
    with tempfile.TemporaryDirectory(prefix="pr95-manual-scope-") as temporary:
        folder = Path(temporary)
        root = folder / "own"
        control = Controller(root, "fixture", initialize=True)
        def create(entry, state):
            manifest = {"session_timeout": 600, "runtime_source": "fixture/runtime", "items": [
                {"id": "a", "messages": [{"role": "user", "content": "fixture"}],
                 "input_hash": "fixture", "source_refs": [{"entry_id": entry}]}]}
            key = control.prepare(manifest, "MANIFEST = None\n")
            control._set(key, state)
            return key
        target = create(1, "submit_unknown")
        unrelated = create(2, "prepared")
        with control.db() as db:
            db.execute("DELETE FROM batch_claims WHERE batch_id=?", (unrelated,))
            if legacy:db.execute("DROP TABLE batch_claims")
        config_path, config = configuration(folder, root, enabled=False)
        calls = []
        def claims():
            with sqlite3.connect(root / "batches.sqlite3") as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='batch_claims'").fetchone():return []
                return db.execute("SELECT batch_id,entry_id FROM batch_claims ORDER BY entry_id").fetchall()
        before = claims()
        def provider(self, args, timeout):
            calls.append(args)
            return "KernelWorkerStatus.RUNNING"
        with patch.dict(sys.modules, {"initialize_secrets": SimpleNamespace(read_env=lambda *args: {})}), \
             patch.object(sys, "argv", ["cloud_bridge", "--config", str(config_path), "recover", "--batch", target]), \
             patch.object(Controller, "_cli", provider), contextlib.redirect_stdout(io.StringIO()):
            cloud_bridge.main()
        after = claims()
        assert before == ([] if legacy else [(target,1)]) and after == [(target, 1)]
        assert control.row(unrelated)["state"]=="prepared"
        assert claimed_entries([root])=={1,2} # Legacy manifest still reserves B conservatively.
        assert calls == [["kernels", "status", "fixture/" + target]]

def synthetic_batch(root,entry=1,state='prepared'):
    c=Controller(root,'fixture',initialize=True)
    manifest={'session_timeout':600,'runtime_source':'fixture/runtime','items':[
        {'id':'a','messages':[{'role':'user','content':'fixture'}],
         'input_hash':'fixture','source_refs':[{'entry_id':entry}]}]}
    key=c.prepare(manifest,'MANIFEST = None\n');c._set(key,state)
    return c,key


@pytest.mark.parametrize('route',['cycle','bridge'])
def test_failure_handler_rechecks_stop_before_audit_or_backoff(tmp_path,monkeypatch,capsys,route):
    c,key=synthetic_batch(tmp_path/'own',1,'running')
    path,cfg=configuration(tmp_path,c.root)
    cfg.update(interval_hours=6,exception_audit_root=str(tmp_path/'audit'))
    path.write_text(json.dumps(cfg))
    recovery=c.root/'recovery.json'
    initial=b'{"code":"network","failures":17,"retry_at":0,"at":0}'
    recovery.write_bytes(initial)
    def stopped_ledger(roots):
        path.write_text(json.dumps({**cfg,'schedule_enabled':False}))
        raise DispatchBlocked('unreadable_ledger')
    monkeypatch.setattr('batch_control.claimed_entries',stopped_ledger)
    monkeypatch.setattr('time.time',lambda:1000)
    forbidden=Mock(side_effect=AssertionError('no audit, provider or credentials'))
    monkeypatch.setattr('exception_audit.Audit',forbidden)
    monkeypatch.setattr(Controller,'_cli',forbidden)
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=forbidden))
    args=['cloud_cycle','--config',str(path)] if route=='cycle' else ['cloud_bridge','--config',str(path),'advance','--batch',key]
    monkeypatch.setattr(sys,'argv',args)
    try:(cloud_cycle if route=='cycle' else cloud_bridge).main()
    except Exception as exc:print(json.dumps(cloud_bridge.handle_failure(exc)))
    assert json.loads(capsys.readouterr().out)['state']=='schedule_disabled'
    assert recovery.read_bytes()==initial
    assert not (tmp_path/'audit').exists()
    forbidden.assert_not_called()


@pytest.mark.parametrize('live_wal',[False,True])
def test_readonly_observer_reads_live_wal_and_documents_sqlite_sidecars(tmp_path,monkeypatch,live_wal):
    c,key=synthetic_batch(tmp_path/'own',1,'submit_unknown')
    database=c.root/'batches.sqlite3'
    writer=sqlite3.connect(database)
    writer.execute('PRAGMA journal_mode=WAL')
    if live_wal:
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("UPDATE batches SET state='running' WHERE id=?",(key,))
        writer.commit()
    else:writer.close()
    before=database.read_bytes()
    files={p.name for p in c.root.iterdir()}
    forbidden=Mock(side_effect=AssertionError('no Controller, provider or credentials'))
    monkeypatch.setattr(cloud_bridge,'Controller',forbidden)
    monkeypatch.setattr('batch_control.Controller',forbidden)
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=forbidden))
    report=readonly_reconcile({'state_root':str(c.root)})
    assert report['logical_readonly'] is True and report['sqlite_sidecars_may_change'] is True
    assert report['batches'][0]['state']==('running' if live_wal else 'submit_unknown')
    assert database.read_bytes()==before
    assert {p.name for p in c.root.iterdir()}-files <= {'batches.sqlite3-wal','batches.sqlite3-shm'}
    if live_wal:writer.close()
    forbidden.assert_not_called()


def test_exception_apply_stops_before_next_audit_or_transaction(tmp_path,monkeypatch):
    database=tmp_path/'inbox.sqlite3'
    root=tmp_path/'own'
    path,cfg=configuration(tmp_path,root)
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE analyses(entry_id INTEGER PRIMARY KEY,user_id INTEGER,title TEXT,url TEXT,content_hash TEXT,error TEXT,state TEXT,attempts INTEGER,source_text TEXT,published_at TEXT,next_try REAL,updated_at REAL)')
        for entry in (1,2):
            db.execute('INSERT INTO analyses VALUES (?,2,?,?,?,?,?,?,?,?,0,0)',(entry,'Title','https://go.dev/blog/test','hash','original_http_503','fetch_error',3,'excerpt','2026'))
    items=qe.prepare(database,[1,2],set())
    value={'batch_id':'batch','manifest_hash':'mh','items':items,'model':{'filename':'fixture'}}
    outputs=[{'id':item['id'],'batch_id':'batch','manifest_hash':'mh','input_hash':item['input_hash'],'status':'ok','content':json.dumps({'action':'retry_fetch','confidence':.9,'reason':'temporary failure'})} for item in items]
    events=[];begins=[];commits=[];real_connect=sqlite3.connect
    class StopAfterCommit(sqlite3.Connection):
        def execute(self,sql,*args):
            if sql=='BEGIN IMMEDIATE':begins.append(json.loads(path.read_text())['schedule_enabled'])
            return super().execute(sql,*args)
        def __exit__(self,*args):
            result=super().__exit__(*args)
            commits.append(True)
            path.write_text(json.dumps({**cfg,'schedule_enabled':False}))
    def connect(db,*args,**kwargs):
        kwargs['factory']=StopAfterCommit
        return real_connect(db,*args,**kwargs)
    monkeypatch.setattr(qe.sqlite3,'connect',connect)
    with pytest.raises(DispatchStopped,match='schedule_disabled'):
        qe.apply(database,value,outputs,SimpleNamespace(append=lambda event,**data:events.append((event,data))),admission=ConfigGuard(path,cfg))
    assert begins==[True] and commits==[True]
    assert [event for event,data in events]==['qwen_exception_decision']
    first=items[0]['source_refs'][0]['entry_id'];second=items[1]['source_refs'][0]['entry_id']
    with real_connect(database) as db:
        assert db.execute('SELECT entry_id FROM qwen_exception_reviews').fetchall()==[(first,)]
        assert db.execute('SELECT attempts FROM analyses WHERE entry_id=?',(first,)).fetchone()[0]==2
        assert db.execute('SELECT attempts FROM analyses WHERE entry_id=?',(second,)).fetchone()[0]==3


def test_http_redirect_does_not_start_next_request_after_stop(tmp_path,monkeypatch):
    import httpx
    path,cfg=configuration(tmp_path,tmp_path/'own')
    calls=[]
    def response(request):
        calls.append(str(request.url))
        path.write_text(json.dumps({**cfg,'schedule_enabled':False}))
        return httpx.Response(302,headers={'location':'https://fixture.invalid/next'})
    async def run():
        async with http_client(ConfigGuard(path,cfg),transport=httpx.MockTransport(response),follow_redirects=True) as client:
            await client.get('https://fixture.invalid/start')
    with pytest.raises(DispatchStopped,match='schedule_disabled'):asyncio.run(run())
    assert calls==['https://fixture.invalid/start']


def test_nested_reader_client_cannot_fallback_after_stop(tmp_path,monkeypatch):
    import httpx
    import fulltext_source
    path,cfg=configuration(tmp_path,tmp_path/'own');calls=[]
    real_client=httpx.AsyncClient
    def response(request):
        calls.append(str(request.url))
        path.write_text(json.dumps({**cfg,'schedule_enabled':False}))
        return httpx.Response(503)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(response),**kwargs))
    with pytest.raises(DispatchStopped,match='schedule_disabled'):
        asyncio.run(fulltext_source.fetch_reader('https://openai.com/article',admission=ConfigGuard(path,cfg)))
    assert len(calls)==1


def test_admitted_http_redirect_preserves_normal_response(tmp_path):
    import httpx
    path,cfg=configuration(tmp_path,tmp_path/'own');calls=[]
    def response(request):
        calls.append(str(request.url))
        return httpx.Response(302,headers={'location':'https://fixture.invalid/next'}) if len(calls)==1 else httpx.Response(200,text='complete')
    async def run():
        async with http_client(ConfigGuard(path,cfg),transport=httpx.MockTransport(response),follow_redirects=True) as client:
            return await client.get('https://fixture.invalid/start')
    assert asyncio.run(run()).text=='complete'
    assert calls==['https://fixture.invalid/start','https://fixture.invalid/next']


def test_browser_route_blocks_next_request_and_closes_on_stop(tmp_path,monkeypatch):
    import fulltext_source
    path,cfg=configuration(tmp_path,tmp_path/'own')
    continued=[];aborted=[];handler=None
    async def route(pattern,callback):
        nonlocal handler
        handler=callback
    async def proceed(**kwargs):
        assert kwargs=={'max_redirects':0,'max_retries':0}
        continued.append(True)
        path.write_text(json.dumps({**cfg,'schedule_enabled':False}))
        return SimpleNamespace(status=200)
    async def abort():aborted.append(True)
    async def goto(*args,**kwargs):
        await handler(SimpleNamespace(fetch=proceed,fulfill=AsyncMock(),abort=abort))
        await handler(SimpleNamespace(fetch=proceed,fulfill=AsyncMock(),abort=abort))
        raise RuntimeError('aborted navigation')
    page=SimpleNamespace(route=route,goto=goto,content=AsyncMock(side_effect=AssertionError('no extraction after stop')))
    browser=SimpleNamespace(new_page=AsyncMock(return_value=page),close=AsyncMock())
    playwright=SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
    @contextlib.asynccontextmanager
    async def launch():yield playwright
    monkeypatch.setitem(sys.modules,'playwright.async_api',SimpleNamespace(async_playwright=launch,TimeoutError=TimeoutError,Error=RuntimeError))
    monkeypatch.setattr(fulltext_source,'_browser_executable',lambda:'fixture-browser')
    with pytest.raises(DispatchStopped,match='schedule_disabled'):
        asyncio.run(fulltext_source._fetch_browser_locked('https://fixture.invalid/article','@browser:article','',False,admission=ConfigGuard(path,cfg)))
    assert continued==[True] and aborted==[True,True]
    browser.close.assert_awaited_once()
    assert browser.new_page.call_args.kwargs['service_workers']=='block'
    page.content.assert_not_awaited()


@pytest.mark.parametrize('script',['cloud_cycle.py','cloud_bridge.py'])
def test_stopped_direct_cli_without_pythonpath_has_no_state_or_provider_work(tmp_path,script):
    import os,subprocess
    path,cfg=configuration(tmp_path,tmp_path/'absent-ledger',enabled=False)
    env={key:value for key,value in os.environ.items() if key!='PYTHONPATH'}
    env['PYTHONDONTWRITEBYTECODE']='1'
    args=[sys.executable,str(Path(cloud_bridge.__file__).with_name(script)),'--config',str(path)]
    if script=='cloud_bridge.py':args.append('prepare')
    result=subprocess.run(args,cwd=tmp_path,env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode==0 and json.loads(result.stdout)['state']=='schedule_disabled'
    assert result.stderr=='' and not (tmp_path/'absent-ledger').exists()


@pytest.mark.parametrize('stop_kind',['disabled','pause','identity'])
def test_scheduler_stop_during_failed_ledger_read_preserves_prior_retry(tmp_path,monkeypatch,stop_kind):
    import lane_scheduler as scheduler
    from test_required_ledgers import topology
    roots,configs=topology(tmp_path,monkeypatch)
    folder=scheduler.ROOT/'state/kaggle-month-dispatch';folder.mkdir(parents=True)
    recovery=folder/'recovery.json';before=b'{"code":"network","failures":17,"retry_at":0,"at":0}'
    recovery.write_bytes(before)
    def failure(roots):
        if stop_kind=='disabled':
            for cfg in configs.values():cfg['schedule_enabled']=False
        elif stop_kind=='pause':
            scheduler.STAGE.mkdir(exist_ok=True);(scheduler.STAGE/'paused.json').write_text('{}')
        else:
            # Use independent config snapshots so identity change is detectable.
            configs['primary']['owner']='changed'
        raise DispatchBlocked('unreadable_ledger')
    if stop_kind=='identity':
        monkeypatch.setattr(scheduler,'schedule_snapshot',lambda:json.loads(json.dumps(configs)))
    monkeypatch.setattr(scheduler,'claimed_entries',failure)
    forbidden=Mock(side_effect=AssertionError('no provider, service or audit'))
    monkeypatch.setattr(scheduler,'query_config',forbidden)
    monkeypatch.setattr(scheduler,'Audit',forbidden)
    report=scheduler.tick(run=forbidden,starter=forbidden,now=1000)
    assert report['state']=={'disabled':'schedule_disabled','pause':'paused','identity':'configuration_changed'}[stop_kind]
    assert report['started']==[] and recovery.read_bytes()==before
    forbidden.assert_not_called()


def test_real_bridge_stop_during_claim_read_prevents_live_feed_request(tmp_path,monkeypatch):
    import live_scope,httpx
    root=tmp_path/'own';Controller(root,'fixture',initialize=True)
    path,cfg=configuration(tmp_path,root);cfg.update(queue_scope='all_enabled_feeds',scope_user_id=1)
    path.write_text(json.dumps(cfg));Path(cfg['versions']).write_text('{}')
    real=cloud_bridge.claimed_entries;calls=[]
    def claims(roots):
        result=real(roots);calls.append(True)
        # At the pre-scope seam after backup, not the earlier pre-quota checks.
        if len(calls)==3:path.write_text(json.dumps({**cfg,'schedule_enabled':False}))
        return result
    monkeypatch.setattr(cloud_bridge,'claimed_entries',claims)
    monkeypatch.setattr('quota_guard.query_client',lambda *a:{'allowed':True})
    monkeypatch.setattr(cloud_bridge,'validate_model_config',lambda *a:None)
    monkeypatch.setattr(cloud_bridge,'backup_before_import',lambda *a:None)
    monkeypatch.setattr(cloud_bridge,'resolve_entry_ids',live_scope.resolve_entry_ids)
    read_env=Mock(return_value={})
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=read_env))
    client=Mock(side_effect=AssertionError('no live feed HTTP client'))
    monkeypatch.setattr(httpx,'Client',client)
    monkeypatch.setattr(sys,'argv',['cloud_bridge','--config',str(path),'prepare'])
    with pytest.raises(DispatchStopped,match='schedule_disabled'):cloud_bridge.main()
    assert len(calls)==3 and read_env.call_count==1 # Only already-admitted outer bridge proxy read.
    client.assert_not_called()
    assert claimed_entries([root])==set() and not list(root.glob('*/manifest.json'))


@pytest.mark.parametrize('stop_on_first',[False,True])
def test_sync_http_client_checks_actual_redirect_hop(tmp_path,stop_on_first):
    import httpx
    from work_admission import sync_http_client
    path,cfg=configuration(tmp_path,tmp_path/'own');calls=[]
    def response(request):
        calls.append(str(request.url))
        if len(calls)==1:
            if stop_on_first:path.write_text(json.dumps({**cfg,'schedule_enabled':False}))
            return httpx.Response(307,headers={'location':'https://fixture.invalid/landing'})
        return httpx.Response(200,text='complete')
    def run():
        with sync_http_client(ConfigGuard(path,cfg),transport=httpx.MockTransport(response),follow_redirects=True) as client:
            return client.get('https://fixture.invalid/start')
    if stop_on_first:
        with pytest.raises(DispatchStopped,match='schedule_disabled'):run()
        assert calls==['https://fixture.invalid/start']
    else:
        assert run().text=='complete'
        assert calls==['https://fixture.invalid/start','https://fixture.invalid/landing']


@pytest.mark.parametrize('entrypoint',['due_entries','queue_summary'])
def test_scheduler_live_scope_stop_before_credentials_or_http(tmp_path,monkeypatch,entrypoint):
    import lane_scheduler as scheduler,live_scope,httpx
    path,cfg=configuration(tmp_path,tmp_path/'own');cfg.update(queue_scope='all_enabled_feeds',scope_user_id=1)
    path.write_text(json.dumps(cfg));guard=ConfigGuard(path,cfg)
    def claims(roots):
        path.write_text(json.dumps({**cfg,'schedule_enabled':False}));return set()
    monkeypatch.setattr(scheduler,'claimed_entries',claims)
    forbidden=Mock(side_effect=AssertionError('no credentials or HTTP after stop'))
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=forbidden))
    monkeypatch.setattr(httpx,'Client',forbidden)
    if entrypoint=='queue_summary':path.write_text(json.dumps({**cfg,'schedule_enabled':False}))
    with pytest.raises(DispatchStopped,match='schedule_disabled'):
        getattr(scheduler,entrypoint)(1000,cfg,admission=guard)
    forbidden.assert_not_called()


def test_live_scope_stop_after_credential_load_has_zero_http(tmp_path,monkeypatch):
    import live_scope,httpx
    path,cfg=configuration(tmp_path,tmp_path/'own')
    def read_env(name):
        path.write_text(json.dumps({**cfg,'schedule_enabled':False}))
        return {'MINIFLUX_API_KEY':'synthetic-unused-token'}
    monkeypatch.setitem(sys.modules,'initialize_secrets',SimpleNamespace(read_env=read_env))
    monkeypatch.setitem(sys.modules,'worker',SimpleNamespace(MF='https://fixture.invalid'))
    client=Mock(side_effect=AssertionError('no client after stop during credential load'))
    monkeypatch.setattr(httpx,'Client',client)
    with pytest.raises(DispatchStopped,match='schedule_disabled'):
        live_scope.enabled_feeds(cfg,admission=ConfigGuard(path,cfg))
    client.assert_not_called()


def test_live_scope_stop_at_sqlite_open_prevents_temp_write(tmp_path,monkeypatch):
    import live_scope
    path,cfg=configuration(tmp_path,tmp_path/'own');cfg.update(queue_scope='all_enabled_feeds',scope_user_id=1)
    path.write_text(json.dumps(cfg));db=sqlite3.connect(':memory:')
    monkeypatch.setattr(live_scope,'enabled_feeds',lambda *a,**kw:[{'id':7,'user_id':1}])
    def connect(*a,**kw):
        assert kw['uri'] is True and a[0].endswith('?mode=ro')
        path.write_text(json.dumps({**cfg,'schedule_enabled':False}));return db
    monkeypatch.setattr(live_scope.sqlite3,'connect',connect)
    with pytest.raises(DispatchStopped,match='schedule_disabled'):
        live_scope.resolve_entry_ids(cfg,admission=ConfigGuard(path,cfg))
    assert db.execute('SELECT count(*) FROM sqlite_temp_master').fetchone()[0]==0
    db.close()
