#!/usr/bin/env python3
"""Real-process Reader -> observing proxy -> pinned Miniflux -> disposable PG.

Hosted-only, synthetic-only acceptance; never accepts an arbitrary database URL.
The proxy forwards normal traffic, recording only paths/statuses and synthetic IDs.
Failure cases explicitly inject transport faults; successful metadata is never mocked.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

import httpx

CODE = '5349898006132cebec894a129fd7c6982db39d25'
BINARY_SHA = '20d6c314a0c8030be4ae02254838948b9262935422e6cb074e3f4eb7dca7a1c4'
IMAGE = 'postgres@sha256:d74eeac9a635390a49bc21bd49fccd973de707e2a53a76ac49b552b8712ec46f'
DSN = 'postgres://issue73:issue73-disposable-only@127.0.0.1:55473/issue73_metadata_test?sslmode=disable'
ADMIN = ('pair_admin', 'synthetic-pair-password')
SECOND = ('pair_second', 'synthetic-second-password')
READER = 'http://127.0.0.1:8092'
MINIFLUX = 'http://127.0.0.1:8093/mf'
FIELDS = {'id', 'user_id', 'feed_id', 'title', 'published_at', 'url', 'changed_at'}


def require(value, message):
    if not value:
        raise AssertionError(message)


def legacy_url_omission_fault(status, headers, data):
    """Negative-only old-protocol simulation, applied to a real nonempty reply.

    Preserve the real capability header and status. This does not run an old
    binary and must never be described as a successful mocked metadata reply.
    """
    capability = [value for key, value in headers.items() if key.lower() == 'x-reader-entry-metadata']
    require(status == 200 and capability == ['1'], 'legacy fault requires actual successful capability 1')
    payload = json.loads(data)
    require(isinstance(payload, dict) and set(payload) == {'entries'} and
            isinstance(payload['entries'], list) and payload['entries'], 'legacy fault requires nonempty actual entries')
    entries = payload['entries']
    require(all(isinstance(entry, dict) and set(entry) == FIELDS and
                isinstance(entry['url'], str) and entry['url'].strip() for entry in entries),
            'legacy fault requires complete actual seven-field URL/change DTOs')
    # Construct another envelope without mutating any real source data or headers.
    fault = {'entries': [{key: value for key, value in entry.items() if key not in {'url', 'changed_at'}} for entry in entries]}
    return json.dumps(fault, separators=(',', ':')).encode(), {
        'kind': 'old-five-field-protocol-injection', 'actual_old_binary': False,
        'upstream_status': status, 'upstream_capability': capability[0],
        'upstream_fields': sorted(FIELDS), 'returned_fields': sorted(FIELDS - {'url', 'changed_at'}),
        'entry_ids': [entry['id'] for entry in entries]}


def command(*args, **kw):
    return subprocess.check_output(args, text=True, **kw).strip()


def verify_runtime_source(root, baseline=CODE):
    """Read actual import-tree bytes; do not trust Git's cached stat/index view."""
    source = root / 'src'
    require(source.is_dir() and not source.is_symlink(), 'invalid runtime source directory')
    tags = command('git','ls-files','-v','-z','--','src',cwd=root)
    require(all(row.startswith('H ') for row in tags.split('\0') if row), 'masked runtime index entry')
    entries = command('git','ls-tree','-r','-z',baseline,'--','src',cwd=root)
    expected = set()
    for entry in filter(None,entries.split('\0')):
        header,name=entry.split('\t',1)
        mode,kind,sha=header.split()
        require(kind=='blob' and mode in {'100644','100755'}, 'unsupported runtime source object')
        path=root/name
        require(path.is_file() and not path.is_symlink(), 'runtime file type mismatch')
        require(not any(p.is_symlink() for p in path.parents if p!=root), 'runtime parent symlink')
        require(bool(path.stat().st_mode & 0o111)==(mode=='100755'), 'runtime executable mode mismatch')
        data=path.read_bytes()
        require(hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()==sha, 'runtime bytes mismatch')
        expected.add(path.relative_to(source).as_posix())
    actual=set()
    for path in source.rglob('*'):
        require(not path.is_symlink(), 'unexpected runtime symlink')
        if not path.is_dir():
            require(path.is_file(), 'unexpected runtime file type')
            actual.add(path.relative_to(source).as_posix())
    require(expected and actual==expected, 'unexpected or missing runtime files/imports')


def verify_container_binding(info, container):
    """Bind the inspected empty fixture to the exact TCP endpoint used by Miniflux."""
    require(info.get('Id','').startswith(container), 'container identity mismatch')
    require(info.get('Config',{}).get('Image')==IMAGE, 'not the pinned disposable PostgreSQL service')
    require(info.get('State',{}).get('Running') is True, 'fixture container is not running')
    ports=info.get('NetworkSettings',{}).get('Ports',{}).get('5432/tcp') or []
    require(any(p.get('HostPort')=='55473' and p.get('HostIp') in {'127.0.0.1','0.0.0.0'} for p in ports),
            'fixture container does not own IPv4 loopback port 55473 for PostgreSQL 5432')


def require_process_identity(proc, expected_sha):
    require(proc.poll() is None, 'candidate Miniflux process exited')
    require(hashlib.sha256(Path(f'/proc/{proc.pid}/exe').read_bytes()).hexdigest()==expected_sha,
            'running Miniflux executable identity mismatch')


class Wire:
    def __init__(self, sql):
        self.sql = sql
        self.lock = threading.Lock()
        self.reset('startup')

    def reset(self, case, *, body_faults=None, metadata_fault=None, delete=None):
        with self.lock:
            self.case, self.calls = case, []
            self.body_faults = body_faults or {}
            self.metadata_fault, self.delete = metadata_fault, delete

    def snapshot(self):
        with self.lock:
            return list(self.calls)

    @property
    def bodies(self):
        return [x['entry_id'] for x in self.snapshot() if 'entry_id' in x]

    @property
    def metadata(self):
        return [x for x in self.snapshot() if x['path'] == '/mf/v1/entries/metadata']


def proxy_handler(wire):
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.relay()

        def do_POST(self):
            self.relay()

        def relay(self):
            path = urlsplit(self.path).path
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            match = re.fullmatch(r'/mf/v1/entries/(\d+)', path)
            row = {'method': self.command, 'path': path, 'forwarded': False}
            if match:
                row['entry_id'] = int(match[1])
            if path == '/mf/v1/entries/metadata':
                row['candidate_ids'] = json.loads(body)['entry_ids']
            with wire.lock:
                fault = wire.body_faults.get(row.get('entry_id'))
                metadata_fault = wire.metadata_fault if path.endswith('/entries/metadata') else None
                remove = wire.delete == row.get('entry_id') and wire.delete is not None
                if remove:
                    wire.delete = None
            if remove:
                wire.sql(f"DELETE FROM entries WHERE id={row['entry_id']};")
                row['fixture_deleted_before_forward'] = True
            headers = {}
            if fault or metadata_fault == '500':
                status = fault or 500
                data = b'{"error_message":"synthetic transport fault"}'
                row['injected_fault'] = True
            else:
                forward_path = self.path
                if metadata_fault == 'missing-route':
                    forward_path = '/mf/v1/pair-nonexistent-route'
                conn = http.client.HTTPConnection('127.0.0.1', 8093, timeout=20)
                try:
                    forwarded = {k: v for k, v in self.headers.items()
                                 if k.lower() not in {'host', 'connection', 'accept-encoding'}}
                    conn.request(self.command, forward_path, body=body, headers=forwarded)
                    response = conn.getresponse()
                    status, data = response.status, response.read()
                    headers = dict(response.getheaders())
                    row['forwarded'] = True
                    row['upstream_path'] = urlsplit(forward_path).path
                finally:
                    conn.close()
            if metadata_fault == 'strip-capability':
                headers = {k: v for k, v in headers.items() if k.lower() != 'x-reader-entry-metadata'}
                row['stripped_capability'] = True
            if metadata_fault == 'legacy-five-fields':
                data, row['legacy_dto_fault'] = legacy_url_omission_fault(status, headers, data)
            row['status'] = status
            with wire.lock:
                wire.calls.append(row)
            self.send_response(status)
            for key, value in headers.items():
                if key.lower() not in {'connection', 'transfer-encoding', 'content-length', 'content-encoding'}:
                    self.send_header(key, value)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return Proxy


@contextlib.contextmanager
def process(args, env, log):
    with log.open('w') as output:
        proc = subprocess.Popen(args, env=env, cwd=env['AI_NEWS_ROOT'], stdout=output, stderr=subprocess.STDOUT)
        try:
            yield proc
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def wait_http(client, url, proc):
    for _ in range(100):
        require(proc.poll() is None, 'fixture process exited; inspect fixture-only log')
        try:
            if client.get(url).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise AssertionError('fixture process readiness timeout')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--postgres-container', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    require(os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('ISSUE73_DISPOSABLE_POSTGRES') == '1',
            'requires hosted disposable fixture markers')
    require(re.fullmatch(r'[a-f0-9]{12,64}', args.postgres_container), 'invalid service container ID')
    inspected=json.loads(command('docker','inspect',args.postgres_container))
    require(isinstance(inspected,list) and len(inspected)==1,'unexpected container inspection')
    verify_container_binding(inspected[0],args.postgres_container)
    require(hashlib.sha256(args.binary.read_bytes()).hexdigest() == BINARY_SHA, 'unexpected candidate binary')
    verify_runtime_source(root)
    for port in (8092,8093):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',port)) # Reject pre-existing listeners before any fixture mutation.
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    def sql(query):
        return command('docker', 'exec', '-i', args.postgres_container, 'psql', '-X', '-A', '-t',
                       '-v', 'ON_ERROR_STOP=1', '-U', 'issue73', '-d', 'issue73_metadata_test', input=query)
    require(sql("SELECT count(*) FROM pg_tables WHERE schemaname='public';") == '0',
            'refusing an already populated public schema')
    results = []
    wire = Wire(sql)
    proxy = ThreadingHTTPServer(('127.0.0.1', 8091), proxy_handler(wire))
    proxy.daemon_threads = True
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='issue73-pair-', dir=os.environ['RUNNER_TEMP']) as tmp:
            fixture = Path(tmp)
            env = {'PATH': os.environ['PATH'], 'HOME': str(fixture), 'LANG': 'C.UTF-8',
                   'AI_NEWS_ROOT': str(fixture), 'PYTHONPATH': str(root / 'src'),
                   'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'}
            init = ('import core; core.init_db(); core.init_usage(); core.migrate(); '
                    'core.save_settings({"enabled":False,"translation_enabled":False})')
            subprocess.run([sys.executable, '-c', init], check=True, env=env, cwd=fixture)
            mf_env = {**env, 'DATABASE_URL': DSN, 'RUN_MIGRATIONS': '1', 'CREATE_ADMIN': '1',
                      'ADMIN_USERNAME': ADMIN[0], 'ADMIN_PASSWORD': ADMIN[1],
                      'BASE_URL': MINIFLUX, 'LISTEN_ADDR': '127.0.0.1:8093',
                      'DISABLE_SCHEDULER_SERVICE': '1', 'LOG_LEVEL': 'warning'}
            with httpx.Client(timeout=30, trust_env=False) as client, process(
                    [str(args.binary.resolve())], mf_env, evidence / 'miniflux.log') as mf:
                wait_http(client, MINIFLUX + '/healthcheck', mf)
                require_process_identity(mf,BINARY_SHA)
                version = client.get(MINIFLUX + '/v1/version', auth=ADMIN)
                require(version.status_code == 200 and version.json().get('version') == '2.3.3',
                        'actual Miniflux must pass Reader supported-release version gate')
                results.append({'case': 'stable-release-version', 'passed': True,
                                'http_status': version.status_code, 'version': version.json()['version']})
                owner = client.get(MINIFLUX + '/v1/me', auth=ADMIN).json()['id']
                response = client.post(MINIFLUX + '/v1/users', auth=ADMIN,
                                       json={'username': SECOND[0], 'password': SECOND[1]})
                require(response.status_code == 201, 'synthetic second-user creation')
                second = response.json()['id']
                require((owner, second) == (1, 2), 'fresh synthetic user identities expected')
                sql("""INSERT INTO feeds(id,user_id,category_id,title,feed_url,site_url)
                    SELECT u.id,u.id,c.id,'Synthetic feed','https://example.invalid/feed/'||u.id,
                           'https://example.invalid' FROM users u JOIN categories c ON c.user_id=u.id;
                    INSERT INTO entries(id,user_id,feed_id,hash,published_at,changed_at,title,url,author,content,status)
                    SELECT n,1,1,'pair-'||n,'2026-09-28T00:00:00Z','2026-09-29T12:00:00Z',
                           CASE WHEN n<=24 THEN 'target '||n ELSE 'offpage '||n END,
                           'https://example.invalid/entry/'||n,'','<p>synthetic body '||n||'</p>','unread'
                    FROM generate_series(1,200) AS n;
                    INSERT INTO entries(id,user_id,feed_id,hash,published_at,changed_at,title,url,author,content,status)
                    VALUES(1001,2,2,'pair-1001','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z',
                           'second user target','https://example.invalid/1001','','<p>second private body</p>','unread');""")
                database = fixture / 'state/analysis.sqlite3'
                with sqlite3.connect(database) as db:
                    db.executemany('INSERT INTO entry_notes VALUES(1,?,?,0,?)',
                                   [(i, f'synthetic note {i}', i) for i in range(1,201)])
                    db.execute("INSERT INTO entry_notes VALUES(1,1001,'foreign trap',0,99999)")
                    db.execute("INSERT INTO entry_notes VALUES(2,1001,'second private note',0,99999)")
                    db.execute("INSERT INTO entry_notes VALUES(2,1,'foreign trap',0,99999)")
                def record(name, response, expected=200, **facts):
                    require(mf.poll() is None and reader.poll() is None,'paired child process exited')
                    require(response.status_code == expected, f'{name}: expected {expected}, got {response.status_code}')
                    results.append({'case': name, 'passed': True, 'http_status': response.status_code,
                                    **facts, 'upstream_calls': wire.snapshot()})
                    print('PASS paired:', name, flush=True)
                def listing(auth=ADMIN, **params):
                    return client.get(READER+'/mf/v1/entries', auth=auth,
                                      params={'ai_view':'notes','ai_min':0,'search':'target','limit':24,**params})
                for stage,mode in enumerate((None, '0', '1', '0')):
                    reader_env = dict(env)
                    if mode is not None:
                        reader_env['READER_NOTES_METADATA'] = mode
                    with process([sys.executable,'-m','uvicorn','api:app','--host','127.0.0.1',
                                  '--port','8092','--log-level','warning'], reader_env,
                                 evidence / f'reader-{stage}-{mode or "unset"}.log') as reader:
                        wait_http(client, READER+'/', reader)
                        wire.reset('legacy' if mode != '1' else 'enabled',
                                   body_faults={i:500 for i in range(25,201)} if mode=='1' else None)
                        response = listing()
                        require(response.status_code == 200, f'notes listing mode {mode}: {response.text[:200]}')
                        data = response.json()
                        require(data['total']==24 and [e['id'] for e in data['entries']]==list(range(24,0,-1)),
                                'legacy/metadata selection equivalence')
                        expected_bodies = 24 if mode=='1' else 200
                        require(len(wire.bodies)==expected_bodies and len(wire.metadata)==(1 if mode=='1' else 0),
                                'effective flag/body count')
                        require(len(wire.snapshot())==(29 if mode=='1' else 204), 'HTTP count includes /me authentication')
                        require(all(x['forwarded'] for x in wire.snapshot()), 'successful cases must hit actual Miniflux')
                        record('rollback_1_to_0' if stage==3 else f'flag_{mode or "unset"}',response,body_calls=expected_bodies,
                               total_http_calls=len(wire.snapshot()),effective_flag=mode)
                        if mode!='1':
                            continue
                        require(wire.metadata[0]['candidate_ids']==list(range(1,201)), 'foreign note candidate filtered')
                        wire.reset('capability-direct')
                        direct=client.post(MINIFLUX+'/v1/entries/metadata',auth=ADMIN,json={'entry_ids':[]})
                        require(direct.headers.get('X-Reader-Entry-Metadata')=='1' and direct.json()=={'entries':[]},
                                'direct authenticated capability')
                        record('direct_capability',direct)
                        wire.reset('metadata-url-direct')
                        direct=client.post(MINIFLUX+'/v1/entries/metadata',auth=ADMIN,json={'entry_ids':[1,1001]})
                        require(direct.status_code==200 and direct.headers.get('X-Reader-Entry-Metadata')=='1',
                                'real nonempty metadata must retain capability 1')
                        native=direct.json()
                        require(set(native)=={'entries'} and len(native['entries'])==1 and
                                set(native['entries'][0])==FIELDS and native['entries'][0]['id']==1 and
                                native['entries'][0]['user_id']==owner and
                                native['entries'][0]['url']=='https://example.invalid/entry/1',
                                'real native metadata must return current URL with exact DTO and owner scope')
                        require(isinstance(native['entries'][0]['changed_at'], str) and
                                datetime.fromisoformat(native['entries'][0]['changed_at'].replace('Z', '+00:00')) ==
                                datetime(2026, 9, 29, 12, tzinfo=timezone.utc),
                                'real changed_at must retain the distinct stored instant and timezone')
                        record('direct_metadata_current_url',direct,actual_native_response=True,
                               fields=sorted(native['entries'][0]),synthetic_url=native['entries'][0]['url'])
                        wire.reset('capability-reader-proxy')
                        proxied=client.post(READER+'/mf/v1/entries/metadata',auth=ADMIN,json={'entry_ids':[1,1001]})
                        require('X-Reader-Entry-Metadata' not in proxied.headers and proxied.headers.get('cache-control')=='no-store',
                                'Reader proxy intentionally strips capability but preserves cache policy')
                        require([x['id'] for x in proxied.json()['entries']]==[1] and
                                set(proxied.json()['entries'][0])==FIELDS,'proxy DTO/auth ownership')
                        record('reader_proxy_header_distinction',proxied,capability_header_retained=False)
                        for size in (256<<10,(256<<10)+1):
                            wire.reset('body-admission')
                            body=b'{"entry_ids":[]}';body+=b' '*(size-len(body))
                            response=client.post(READER+'/mf/v1/entries/metadata',auth=ADMIN,content=body,
                                                 headers={'Content-Type':'application/json'})
                            record(f'proxied_body_{size}',response,200 if size==256<<10 else 400,
                                   forwarded_to_real_miniflux=all(x['forwarded'] for x in wire.snapshot()))
                        wire.reset('second-user')
                        response=listing(auth=SECOND)
                        require(response.json()['total']==1 and [x['id'] for x in response.json()['entries']]==[1001],
                                'second user notes ownership')
                        record('second_user_scope',response)
                        wire.reset('foreign-body')
                        record('foreign_body_404',client.get(READER+'/mf/v1/entries/1001',auth=ADMIN),404)
                        wire.reset('admin-boundary')
                        record('reader_admin_403',client.get(READER+'/mf/v1/ai/settings',auth=SECOND),403)
                        wire.reset('invalid-auth')
                        record('invalid_auth_401',listing(auth=(ADMIN[0],'incorrect-synthetic-password')),401)
                        for fault in ('strip-capability','missing-route','500','legacy-five-fields'):
                            wire.reset('metadata-fault-'+fault,metadata_fault=fault)
                            response=listing(limit=1)
                            require(not wire.bodies and len(wire.metadata)==1,'metadata fault must not fall back to bodies')
                            if fault=='legacy-five-fields':
                                proof=wire.metadata[0]
                                require(proof['forwarded'] and proof['status']==200 and
                                        proof['legacy_dto_fault']['upstream_capability']=='1' and
                                        proof['legacy_dto_fault']['entry_ids'],
                                        'old DTO negative must come from a nonempty actual capability-1 response')
                                require(isinstance(response.json(),dict) and 'total' not in response.json(),
                                        'old DTO failure must not publish a zero or partial total')
                            record('metadata_'+fault,response,503,synthetic_proxy_fault=fault)
                        for status in (403,500):
                            wire.reset('selected-body-fault',body_faults={24:status})
                            response=listing(limit=1)
                            require(wire.bodies==[24] and len(wire.metadata)==1,'selected auth/server errors are not deletion')
                            record(f'selected_body_{status}',response,503,synthetic_proxy_fault=status)
                        wire.reset('real-selected-delete',delete=24)
                        response=listing(limit=3)
                        require(response.json()['total']==23 and [x['id'] for x in response.json()['entries']]==[23,22,21],
                                'real deletion must reselect with fresh total')
                        require(len(wire.metadata)==2 and len(wire.bodies)==6 and
                                any(x.get('entry_id')==24 and x['status']==404 and x['forwarded'] for x in wire.snapshot()),
                                'real selected404 bounded reselection')
                        record('real_selected_404_reselect',response,metadata_snapshots=2,body_calls=6)
                        # Restore only the deleted synthetic entry for the actual 1 -> 0 restart check.
                        sql("""INSERT INTO entries(id,user_id,feed_id,hash,published_at,changed_at,title,url,author,content,status)
                            VALUES(24,1,1,'pair-24','2026-09-28T00:00:00Z','2026-09-28T00:00:00Z',
                                   'target 24','https://example.invalid/entry/24','','<p>synthetic body 24</p>','unread');""")
                with sqlite3.connect(database) as db:
                    require(db.execute('SELECT count(*) FROM analyses').fetchone()[0]==0,'legacy notes need no analysis rows')
                    require(db.execute('SELECT count(*) FROM entry_notes').fetchone()[0]==203,'no note deletion')
                    prefs=json.loads(db.execute("SELECT value FROM settings WHERE name='preferences'").fetchone()[0])
                    require(not prefs['enabled'] and not prefs['translation_enabled'],'paid workers remain disabled')
                require_process_identity(mf,BINARY_SHA)
                verify_runtime_source(root)
                report={'passed':True,'runtime_source_commit':CODE,'candidate_binary_sha256':BINARY_SHA,
                        'actual_miniflux_process':True,'actual_reader_process':True,'actual_postgresql':True,
                        'external_model_credentials':False,'production_acceptance':False,
                        'runtime_admission':{'actual_bytes_verified':True,'index_flags_verified':True,
                                             'extra_imports_rejected':True,'miniflux_proc_exe_verified':True,
                                             'postgres_container_port_binding_verified':True},
                        'limitations':['Synthetic loopback HTTP proxy, not production Nginx/TLS or real cardinality.',
                                       '403/500 response and missing-header cases explicitly inject transport faults.',
                                       'Missing URL case removes only url from actual nonempty capability-1 replies; it simulates the old protocol, not an old binary process.',
                                       'Only selected404 deletes synthetic PG fixture data; notes remain intact.'],
                        'cases':results}
                (evidence/'paired-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
                print(json.dumps({'passed':True,'cases':len(results),'source':CODE,'binary_sha256':BINARY_SHA}))
    finally:
        if not (evidence/'paired-result.json').exists():
            (evidence/'paired-result.json').write_text(json.dumps(
                {'passed':False,'completed_cases':results,'last_proxy_calls':wire.snapshot()},indent=2)+'\n')
        proxy.shutdown()
        proxy.server_close()
        proxy_thread.join(timeout=5)


if __name__ == '__main__':
    main()
