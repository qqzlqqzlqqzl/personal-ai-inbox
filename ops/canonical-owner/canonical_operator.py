"""One named canonical-only read attempt around the unchanged e87 collector.

Default is DRY_RUN. Inputs and evidence stay private on the operator's host.
This does not grant logical-lane continuity, CAS, claim or submission admission.
"""
import hashlib
import ast
import json
import math
import os
from pathlib import Path
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import types
from datetime import datetime,timezone
from contextlib import redirect_stdout,redirect_stderr
import logging

HERE=Path(__file__).resolve().parent
COLLECTOR=HERE/'collector/owner_status_once.py'
PINS={'owner_status_once.py':'e4322052908125b185976aab5a49f866da2c671659e9dd06162ed320dff09bd1',
      'transport_support.py':'8580f62cc92c56f9c4218732450887c1aacfab07a7fe82f0dbe94b5cfa3f6788',
      'SOURCE_PINS.json':'35a6e17708ee082b39f14dbb37d120a11d29d39d68ff281e5264ddddb619c714'}
VERIFIED={_name:(COLLECTOR.parent/_name).read_bytes() for _name in PINS}
for _name,_sha in PINS.items():
    if hashlib.sha256(VERIFIED[_name]).hexdigest()!=_sha:
        raise SystemExit('collector_source_changed')
c=types.ModuleType('canonical_bound_collector');c.__file__=str(COLLECTOR)
exec(compile(VERIFIED['owner_status_once.py'],str(COLLECTOR),'exec'),c.__dict__)
Refused=c.Refused
require=c.require
MAX_ROWS=100000
MAX_INPUT=131072
FIELDS=('id','state','remote_status','updated','error','manifest_hash')
PUBLIC_CODES={n.value for data in (VERIFIED['owner_status_once.py'],VERIFIED['transport_support.py'])
    for n in ast.walk(ast.parse(data)) if isinstance(n,ast.Constant) and isinstance(n.value,str)
    and c.re.fullmatch(r'[a-z][a-z0-9_]{0,79}',n.value)}
BOOTSTRAP="""import hashlib,sys
path,expected=sys.argv[1:3]
with open(path,'rb') as stream: data=stream.read(100000)
if hashlib.sha256(data).hexdigest()!=expected: raise SystemExit(70)
sys.argv=[path,'--execute-reviewed']
exec(compile(data,path,'exec'),{'__name__':'__main__','__file__':path})
"""

def now():return datetime.now(timezone.utc).isoformat()
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()
def digest(value):return hashlib.sha256(encoded(value)).hexdigest()

def metadata(value, *, directory=False):
    result={'dev':value.st_dev,'ino':value.st_ino,'uid':value.st_uid,'gid':value.st_gid,'mode':value.st_mode}
    if not directory:result.update(size=value.st_size,nlink=value.st_nlink,mtime_ns=value.st_mtime_ns,ctime_ns=value.st_ctime_ns)
    return result

def trace_path(logical, expected=None):
    """Resolve every encountered link, including links inside link targets."""
    require(isinstance(logical,str) and logical.startswith('/') and len(logical)<=4096
            and not any(ord(x)<32 for x in logical),'absolute_path_required')
    require(all(p not in {'.','..'} for p in logical.split('/')),'logical_dot_component_refused')
    pending=[p for p in logical.split('/')[1:] if p];resolved=[];links=[];directories=[];steps=0;hops=0
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY);final=None
    try:
        directories.append({'path':'/','identity':metadata(os.fstat(fd),directory=True)})
        while pending:
            part=pending.pop(0);steps+=1
            require(steps<=256,'path_resolution_budget')
            if part in ('','.'):continue
            if part=='..':
                resolved=resolved[:-1]
                os.close(fd);fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
                for component in resolved:
                    child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=child
                continue
            before=os.stat(part,dir_fd=fd,follow_symlinks=False)
            physical='/'+('/'.join([*resolved,part]))
            if stat.S_ISLNK(before.st_mode):
                hops+=1;require(hops<=40,'symlink_loop_or_budget')
                target=os.readlink(part,dir_fd=fd)
                after=os.stat(part,dir_fd=fd,follow_symlinks=False)
                require(metadata(before)==metadata(after),'alias_changed_during_resolution')
                require(len(target)<=4096 and not any(ord(x)<32 for x in target),'link_target_shape')
                links.append({'path':physical,'identity':metadata(after),'target':target})
                if target.startswith('/'):
                    resolved=[];os.close(fd);fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
                pending=[p for p in target.split('/') if p and p!='.']+pending
                continue
            if pending:
                require(stat.S_ISDIR(before.st_mode),'non_directory_component')
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                require(metadata(before,directory=True)==metadata(os.fstat(child),directory=True),'directory_changed')
                os.close(fd);fd=child;resolved.append(part)
                directories.append({'path':physical,'identity':metadata(before,directory=True)})
            else:
                require(stat.S_ISREG(before.st_mode) or stat.S_ISDIR(before.st_mode),'unsupported_leaf_type')
                flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK
                if stat.S_ISDIR(before.st_mode):flags|=os.O_DIRECTORY
                leaf=os.open(part,flags,dir_fd=fd)
                try:
                    after=os.fstat(leaf)
                    is_dir=stat.S_ISDIR(after.st_mode)
                    require(metadata(before,directory=is_dir)==metadata(after,directory=is_dir),'leaf_changed')
                    final={'path':physical,'identity':metadata(after,directory=is_dir),'kind':'directory' if is_dir else 'file'}
                finally:os.close(leaf)
                resolved.append(part)
        if final is None and resolved:
            final={'path':'/'+('/'.join(resolved)),'identity':metadata(os.fstat(fd),directory=True),'kind':'directory'}
        require(final is not None,'root_leaf_refused')
        require(expected is None or final['kind']==expected,'unexpected_leaf_type')
        canonical='/'+('/'.join(resolved))
        require(str(Path(logical).resolve(strict=True))==canonical,'resolution_disagreement')
        return {'logical':logical,'canonical':canonical,'aliases':links,'directories':directories,'leaf':final}
    finally:os.close(fd)

def read_file(logical,cap):
    before=trace_path(logical,'file')
    data=c.support.bounded_read(before['canonical'],cap)
    require(trace_path(logical,'file')==before,'file_binding_changed')
    return data,before

def parse_spec(raw):
    require(len(raw)<=MAX_INPUT,'input_too_large')
    spec=c.support.parse_json(raw)
    require(type(spec)is dict and set(spec)=={'attempt_id','evidence_root','expected_root_count','sole_executor_no_alias_changes','lanes'},'input_shape')
    require(c.binding_id(spec['attempt_id']),'attempt_id_shape')
    require(spec['sole_executor_no_alias_changes'] is True,'sole_executor_declaration_required')
    require(type(spec['expected_root_count'])is int and 1<=spec['expected_root_count']<=32,'root_count_shape')
    require(type(spec['lanes'])is list and 1<=len(spec['lanes'])<=5,'lane_budget')
    seen=set();bindings=set()
    for lane in spec['lanes']:
        require(type(lane)is dict and set(lane)=={'config_path','context','ledger_before'},'lane_shape')
        context=c.parse_context(encoded(lane['context']));name=context['lane'];target=context['target']
        require(name not in seen and target['binding_id'] not in bindings,'duplicate_lane_or_binding')
        seen.add(name);bindings.add(target['binding_id'])
        row=lane['ledger_before']
        require(type(row)is dict and set(row)==set(FIELDS),'ledger_before_shape')
        require(row['id']==target['claimed_ref'].split('/')[1] and row['state'] in {'submitting','submit_unknown'}
                and row['remote_status'] is None,'original_unknown_target_required')
        require(isinstance(row['manifest_hash'],str) and c.re.fullmatch(r'[0-9a-f]{64}',row['manifest_hash'])
                and c.timestamp(row['updated']) and (row['error'] is None or isinstance(row['error'],str)),'ledger_before_fields')
    return spec

def ledger_snapshot(root_trace):
    logical=root_trace['logical'].rstrip('/')+'/batches.sqlite3'
    path=trace_path(logical,'file');db_path=Path(path['canonical'])
    require(path['leaf']['identity']['nlink']==1,'hardlinked_ledger_refused')
    con=sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True,timeout=3)
    con.row_factory=sqlite3.Row
    try:
        con.execute('PRAGMA query_only=ON');con.execute('BEGIN')
        columns={row['name'] for row in con.execute('PRAGMA table_info(batches)')}
        require(columns==set(FIELDS),'batches_schema_mismatch')
        columns={row['name'] for row in con.execute('PRAGMA table_info(batch_claims)')}
        require(columns=={'batch_id','entry_id'},'claims_schema_missing_or_mismatch')
        batches=[dict(row) for row in con.execute('SELECT * FROM batches ORDER BY id LIMIT ?',(MAX_ROWS+1,))]
        claims=[dict(row) for row in con.execute('SELECT * FROM batch_claims ORDER BY batch_id,entry_id LIMIT ?',(MAX_ROWS+1,))]
        require(len(batches)<=MAX_ROWS and len(claims)<=MAX_ROWS,'ledger_snapshot_budget')
        ids={row['id'] for row in batches}
        require(all(isinstance(row['batch_id'],str) and row['batch_id'] in ids and type(row['entry_id'])is int and row['entry_id']>0 for row in claims),'invalid_claim_rows')
    finally:con.close()
    require(trace_path(logical,'file')==path and trace_path(root_trace['logical'],'directory')==root_trace,'ledger_binding_changed')
    return {'root':root_trace,'database':path,'batches_count':len(batches),'claims_count':len(claims),
            'batches_sha256':digest(batches),'claims_sha256':digest(claims)},batches,claims

def bind_interpreter(configured):
    """Allow a resolved venv parent while retaining the complete config alias graph.

    Resolving the executable leaf would select the system Python and lose the
    virtual environment. Compare both bin directories and both venv prefixes;
    matching the shared executable alone is insufficient.
    """
    try:
        binding = {
            'configured': trace_path(configured, 'file'),
            'running': trace_path(sys.executable, 'file'),
            'configured_bin': trace_path(str(Path(configured).parent), 'directory'),
            'running_bin': trace_path(str(Path(sys.executable).parent), 'directory'),
            'configured_prefix': trace_path(str(Path(configured).parent.parent), 'directory'),
            'running_prefix': trace_path(sys.prefix, 'directory'),
            'running_exec_prefix': trace_path(sys.exec_prefix, 'directory'),
        }
    except (OSError, TypeError, ValueError):
        raise Refused('interpreter_binding_mismatch') from None
    for left, right in (
        ('configured', 'running'),
        ('configured_bin', 'running_bin'),
        ('configured_prefix', 'running_prefix'),
        ('configured_prefix', 'running_exec_prefix'),
    ):
        require(binding[left]['canonical'] == binding[right]['canonical']
                and binding[left]['leaf'] == binding[right]['leaf'],
                'interpreter_binding_mismatch')
    return binding


def gather_snapshot(spec):
    configs=[];root_traces={};targets=[]
    for lane in spec['lanes']:
        context=lane['context'];raw,path=read_file(lane['config_path'],262144);cfg=c.support.parse_json(raw)
        require(type(cfg)is dict,'config_shape')
        require(cfg.get('owner')==context['expected_owner'],'config_owner_mismatch')
        interpreter_binding=bind_interpreter(cfg.get('kaggle_python'))
        root=trace_path(cfg.get('state_root'),'directory');token=trace_path(cfg.get('token_file'),'file')
        # Same private-leaf/no-follow rules as the unchanged collector. No bytes
        # are read during this metadata-only preflight.
        c.token_snapshot(token['canonical'],read=False)
        peers=cfg.get('peer_state_roots',[])
        require(type(peers)is list,'peer_roots_shape')
        declared=[cfg['state_root'],*peers];resolved=[]
        for name in declared:
            item=trace_path(name,'directory');root_traces[name]=item;resolved.append(item['canonical'])
        if Path(root['canonical']).name in {'kaggle-month-'+key for key in c.LANES}:
            expected={str(Path(root['canonical']).parent/('kaggle-month-'+key)) for key in c.LANES}
            require(expected<=set(resolved),'incomplete_campaign_roots')
        configs.append({'lane':context['lane'],'path':path,'content_sha256':hashlib.sha256(raw).hexdigest(),
                        'interpreter_binding':interpreter_binding})
        targets.append({'lane':context['lane'],'binding_id':context['target']['binding_id'],'root':root,'token':token,
                        'context_sha256':digest(context),'ledger_before':lane['ledger_before']})
    canonical_roots={trace['canonical'] for trace in root_traces.values()}
    require(len(canonical_roots)==spec['expected_root_count'],'complete_root_count_mismatch')
    ledgers={};rows_by_root={};claims_by_root={};db_inodes=set()
    for trace in root_traces.values():
        if trace['canonical'] in ledgers:continue
        receipt,rows,claims=ledger_snapshot(trace)
        identity=receipt['database']['leaf']['identity'];inode=(identity['dev'],identity['ino'])
        require(inode not in db_inodes,'duplicate_physical_ledger');db_inodes.add(inode)
        ledgers[trace['canonical']]=receipt;rows_by_root[trace['canonical']]=rows;claims_by_root[trace['canonical']]=claims
    for target in targets:
        rows=rows_by_root[target['root']['canonical']]
        actual=next((row for row in rows if row['id']==target['ledger_before']['id']),None)
        require(actual==target['ledger_before'],'target_snapshot_changed')
        require(any(row['batch_id']==actual['id'] for row in claims_by_root[target['root']['canonical']]),'target_claims_missing')
    proxy=c.support.existing_proxy()
    from urllib.request import getproxies
    c.support.bound_proxy_mapping(getproxies(),proxy)
    require(not os.environ.get('REQUESTS_CA_BUNDLE') and not os.environ.get('CURL_CA_BUNDLE'),'tls_configuration_requires_review')
    proxy_env={key:os.environ.get(key) for key in c.PROXY_ENV}
    runtime={}
    for name,expected in PINS.items():
        data,trace=read_file(str(COLLECTOR.parent/name),100000)
        require(hashlib.sha256(data).hexdigest()==expected,'collector_source_changed')
        runtime[name]={'sha256':expected,'path':trace}
    snapshot={'configs':configs,'roots':root_traces,'ledgers':ledgers,'targets':targets,
              'interpreter':trace_path(sys.executable,'file'),
              'proxy_environment_binding':digest({'attempt_id':spec['attempt_id'],'values':proxy_env}),
              'collector_source':runtime}
    # Rewalk the complete alias graphs after all reads. Directory identities
    # exclude incidental mtime/ctime; every alias and regular leaf includes both.
    traces=[row['path'] for row in configs]+list(root_traces.values())+[row['token'] for row in targets]
    traces += [trace for row in configs for trace in row['interpreter_binding'].values()]
    for trace in traces:require(trace_path(trace['logical'],trace['leaf']['kind'])==trace,'preflight_path_drift')
    return snapshot

def sdk_preflight():
    """Actual SDK init with a synthetic token, with all networking denied."""
    names=['getaddrinfo','gethostbyname','gethostbyname_ex','create_connection']
    old={name:getattr(socket,name) for name in names};old_connect=socket.socket.connect;old_connect_ex=socket.socket.connect_ex
    def denied(*args,**kwargs):raise Refused('preflight_network_attempt_refused')
    for name in names:setattr(socket,name,denied)
    socket.socket.connect=denied;socket.socket.connect_ex=denied
    original_send=None
    try:
        import requests
        original_send=requests.sessions.Session.send;requests.sessions.Session.send=denied
        modules=c.verified_sdk_sources()
        with c.VerifiedSDKLoader(modules):
            from kagglesdk.kaggle_env import KaggleEnv
            from kagglesdk.kaggle_http_client import KaggleHttpClient
            from kagglesdk.security.types.oauth_service import IntrospectTokenRequest
            from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionStatusRequest
            identity=IntrospectTokenRequest();identity.token='SYNTHETIC_PREFLIGHT_ONLY'
            status=ApiGetKernelSessionStatusRequest();status.user_name='synthetic';status.kernel_slug='synthetic'
            with KaggleHttpClient(env=KaggleEnv.PROD,verbose=False,api_token='SYNTHETIC_PREFLIGHT_ONLY'):pass
    finally:
        if original_send is not None:requests.sessions.Session.send=original_send
        for name,value in old.items():setattr(socket,name,value)
        socket.socket.connect=old_connect;socket.socket.connect_ex=old_connect_ex

class EvidenceDirectory:
    """Bind evidence root and attempt dirfds; no pathname-parent writes."""
    def __init__(self,spec,create=False):
        self.root_fd=None;self.attempt_fd=None;self.attempt=spec['attempt_id']
        try:
            self.root_trace=trace_path(spec['evidence_root'],'directory')
            self._private(self.root_trace['leaf']['identity'])
            self.root_fd=self._open_directory(self.root_trace['canonical'])
            require(metadata(os.fstat(self.root_fd),directory=True)==self.root_trace['leaf']['identity'],'evidence_root_open_changed')
            self._verify_root()
            if create:
                os.mkdir(self.attempt,mode=0o700,dir_fd=self.root_fd);os.fsync(self.root_fd)
            before=os.stat(self.attempt,dir_fd=self.root_fd,follow_symlinks=False)
            require(stat.S_ISDIR(before.st_mode),'evidence_attempt_not_directory')
            self._private(metadata(before,directory=True))
            self.attempt_fd=os.open(self.attempt,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=self.root_fd)
            self.attempt_identity=metadata(os.fstat(self.attempt_fd),directory=True)
            require(self.attempt_identity==metadata(before,directory=True),'evidence_attempt_open_changed')
            self.verify()
        except BaseException:
            self.close();raise
    @staticmethod
    def _private(identity):
        require(identity['uid']==os.getuid() and identity['mode']&0o077==0,'private_evidence_directory_required')
    @staticmethod
    def _open_directory(canonical):
        fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        try:
            for component in Path(canonical).parts[1:]:
                child=os.open(component,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=child
            return fd
        except BaseException:os.close(fd);raise
    def _verify_root(self):
        require(trace_path(self.root_trace['logical'],'directory')==self.root_trace,'evidence_root_path_changed')
        require(metadata(os.fstat(self.root_fd),directory=True)==self.root_trace['leaf']['identity'],'evidence_root_fd_changed')
    def verify(self):
        self._verify_root()
        current=os.stat(self.attempt,dir_fd=self.root_fd,follow_symlinks=False)
        require(stat.S_ISDIR(current.st_mode) and metadata(current,directory=True)==self.attempt_identity,'evidence_attempt_path_changed')
        require(metadata(os.fstat(self.attempt_fd),directory=True)==self.attempt_identity,'evidence_attempt_fd_changed')
    def binding(self):
        self.verify()
        return {'root':self.root_trace,'attempt_id':self.attempt,'attempt_identity':self.attempt_identity}
    def close(self):
        if self.attempt_fd is not None:os.close(self.attempt_fd);self.attempt_fd=None
        if self.root_fd is not None:os.close(self.root_fd);self.root_fd=None
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
    def _name(self,name):
        require(isinstance(name,str) and c.re.fullmatch(r'[a-z0-9.-]{1,80}',name) and name not in {'.','..'},'evidence_leaf_name')
    def write(self,name,value):
        self._name(name);data=encoded(value)+b'\n';require(len(data)<=16*1024*1024,'private_evidence_size')
        self.verify()
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.attempt_fd)
        try:
            with os.fdopen(fd,'wb',closefd=False) as stream:stream.write(data);stream.flush();os.fsync(fd)
            os.fsync(self.attempt_fd)
            identity=metadata(os.fstat(fd))
            require(metadata(os.stat(name,dir_fd=self.attempt_fd,follow_symlinks=False))==identity,'evidence_file_replaced')
            self.verify()
        finally:os.close(fd)
    def read(self,name,cap):
        self._name(name);self.verify()
        fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=self.attempt_fd)
        try:
            before=os.fstat(fd)
            require(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and before.st_mode&0o077==0
                    and before.st_nlink==1 and 0<=before.st_size<=cap,'private_evidence_file_required')
            data=b''
            while len(data)<=cap:
                chunk=os.read(fd,min(65536,cap+1-len(data)))
                if not chunk:break
                data+=chunk
            require(len(data)<=cap,'private_evidence_size')
            require(metadata(os.fstat(fd))==metadata(before)
                    and metadata(os.stat(name,dir_fd=self.attempt_fd,follow_symlinks=False))==metadata(before),'evidence_file_replaced')
            self.verify();return data
        finally:os.close(fd)

def base_receipt(spec=None):
    value={'schema':'canonical-owner-status-operator-v1','status':'DRY_RUN','provider_requests':0,
        'canonical_only':True,'logical_lane_continuity_verified':False,'aba_excluded':False,
        'cas_admission':False,'claim_transition_allowed':False,'submission_allowed':False,'absence_proof':False}
    if spec:value.update(attempt_id=spec['attempt_id'],lane_count=len(spec['lanes']),maximum_business_attempts=2*len(spec['lanes']),
                        sole_executor_asserted=True)
    return value

def preflight(spec,result=None):
    result=base_receipt(spec) if result is None else result
    result['phase']='open_attempt'
    with EvidenceDirectory(spec,create=True) as evidence:
        return _preflight(spec,result,evidence)

def _preflight(spec,result,evidence):
    result['phase']='snapshot';snapshot=gather_snapshot(spec)
    result['phase']='offline_sdk'
    sdk_preflight()
    result['phase']='compare_after_preflight'
    require(gather_snapshot(spec)==snapshot,'binding_drift_during_preflight')
    evidence.write('preflight.json',{'schema':'canonical-preflight-v1','created_at':now(),'input_sha256':digest(spec),
        'evidence_binding':evidence.binding(),
        'snapshot':snapshot,'snapshot_sha256':digest(snapshot),'sdk_offline_init':'passed','real_token_contents_read':False,'provider_requests':0})
    result.update(status='PREFLIGHT_OK',phase='preflight_complete',required_root_count=spec['expected_root_count'])
    evidence.write('preflight-summary.json',result);return result

def validated_child(raw,context):
    require(len(raw)<=65536,'collector_output_size')
    result=c.support.parse_json(raw)
    allowed={'schema','status','owner_verified_at_observation','status_observed','exact_request_bound','binding_verified_at_finish',
        'state','specific_submission_version_verified','absence_proof','complete_listing','claim_transition_allowed','submission_allowed',
        'request_counts','maximum_business_requests','lane','target_binding_id','ledger_observed_at','version_request_bound',
        'collection_started_at','collection_finished_at','observations','reason','identity_expiry_checked','requires_semantic_review'}
    require(type(result)is dict and set(result)<=allowed and result.get('schema')=='kaggle-owner-status-v2','collector_receipt_schema')
    require(result.get('lane')==context['lane'] and result.get('target_binding_id')==context['target']['binding_id']
            and result.get('ledger_observed_at')==context['target']['ledger_observed_at'],'collector_target_binding')
    counts=result.get('request_counts')
    require(type(counts)is dict and set(counts)=={'identity','status'} and all(type(v)is int and 0<=v<=1 for v in counts.values()),'collector_request_counts')
    require(type(result.get('maximum_business_requests'))is int and result['maximum_business_requests']==2,'collector_budget_shape')
    require(all(result.get(k)is False for k in ['specific_submission_version_verified','absence_proof','complete_listing','claim_transition_allowed','submission_allowed']),'collector_admission_refused')
    require(result.get('state') in c.STATES|{'UNRESOLVED'},'collector_state_shape')
    require(result.get('status') in {'OBSERVED','UNRESOLVED','REFUSED','DRY_RUN'},'collector_status_shape')
    if 'reason' in result:require(result['reason'] in PUBLIC_CODES,'collector_reason_shape')
    for key in ['owner_verified_at_observation','status_observed','exact_request_bound','binding_verified_at_finish',
                'version_request_bound','identity_expiry_checked','requires_semantic_review']:
        if key in result:require(type(result[key])is bool,'collector_boolean_shape')
    def iso(value):
        require(isinstance(value,str) and len(value)<=40,'collector_time_shape')
        parsed=datetime.fromisoformat(value)
        require(parsed.tzinfo is not None,'collector_time_zone')
        return parsed
    for key in ['collection_started_at','collection_finished_at']:
        if key in result:iso(result[key])
    observations=result.get('observations',{})
    require(type(observations)is dict and set(observations)<={'identity','status'},'collector_observations_shape')
    for item in observations.values():
        require(type(item)is dict and set(item)=={'request_started_at','response_observed_at','http_status','reason'},'collector_phase_shape')
        iso(item['request_started_at'])
        if item['response_observed_at'] is not None:iso(item['response_observed_at'])
        require(item['http_status'] is None or type(item['http_status'])is int and 100<=item['http_status']<=599,'collector_http_shape')
        require(item['reason'] in PUBLIC_CODES,'collector_phase_reason_shape')
    if result.get('status_observed')is True:
        require(set(observations)=={'identity','status'} and counts=={'identity':1,'status':1}
                and all(item['http_status']==200 and item['response_observed_at'] is not None for item in observations.values()),'collector_observation_evidence_missing')
        times=[datetime.fromtimestamp(context['target']['ledger_observed_at'],timezone.utc),
               iso(result['collection_started_at']),iso(observations['identity']['request_started_at']),
               iso(observations['identity']['response_observed_at']),iso(observations['status']['request_started_at']),
               iso(observations['status']['response_observed_at']),iso(result['collection_finished_at'])]
        require(times==sorted(times),'collector_observation_time_order')
    return result

def execute(spec,result=None):
    result=base_receipt(spec) if result is None else result
    result['phase']='load_preflight'
    with EvidenceDirectory(spec) as evidence:
        return _execute(spec,result,evidence)

def _execute(spec,result,evidence):
    raw=evidence.read('preflight.json',16*1024*1024);plan=c.support.parse_json(raw)
    require(plan.get('schema')=='canonical-preflight-v1' and plan.get('input_sha256')==digest(spec)
            and plan.get('sdk_offline_init')=='passed','preflight_binding_required')
    require(plan.get('evidence_binding')==evidence.binding(),'preflight_evidence_directory_changed')
    snapshot=plan['snapshot'];require(digest(snapshot)==plan['snapshot_sha256'],'preflight_snapshot_corrupt')
    # O_EXCL consumes this named attempt before any dependency or RPC work.
    evidence.write('execution.marker',{'at':now(),'input_sha256':digest(spec),'reserved_business_attempts':2*len(spec['lanes']),
        'no_retry':True,'collector_sha256':PINS['owner_status_once.py']})
    result.update(provider_requests=None,reserved_business_attempts=2*len(spec['lanes']),business_attempt_upper_bound=0,lanes=[])
    result['phase']='compare_before_execution'
    require(gather_snapshot(spec)==snapshot,'pre_execute_binding_changed')
    result['phase']='offline_sdk'
    sdk_preflight()
    require(gather_snapshot(spec)==snapshot,'pre_execute_dependency_drift')
    result.update(status='OBSERVATIONS_COMPLETE')
    for lane in spec['lanes']:
        result['phase']='compare_before_lane'
        context=lane['context'];name=context['lane'];target=next(t for t in snapshot['targets'] if t['lane']==name)
        require(gather_snapshot(spec)==snapshot,'binding_changed_before_lane')
        evidence.write('lane-'+name+'.started',{'at':now(),'target_binding_id':context['target']['binding_id'],'reserved_business_attempts':2})
        env=dict(os.environ);env['KAGGLE_API_TOKEN']=target['token']['canonical']
        result['business_attempt_upper_bound']+=2
        result['phase']='collector_child'
        child=None;reason='collector_process_failed'
        try:
            process=subprocess.run([sys.executable,'-I','-B','-c',BOOTSTRAP,str(COLLECTOR),PINS['owner_status_once.py']],
                input=encoded(context),stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=75)
            child=validated_child(process.stdout,context)
            reason='observed' if process.returncode==0 else 'collector_unresolved'
        except BaseException:reason='collector_process_or_receipt_failed'
        outer=False
        try:outer=gather_snapshot(spec)==snapshot
        except BaseException:pass
        accepted=bool(outer and child is not None and reason=='observed' and child.get('status')=='OBSERVED'
            and child.get('binding_verified_at_finish')is True and child.get('owner_verified_at_observation')is True
            and child.get('status_observed')is True and child.get('exact_request_bound')is True)
        record={'lane':name,'target_binding_id':context['target']['binding_id'],'outer_binding_verified':outer,
            'provisional_canonical_observation':accepted,'requires_final_attempt_record':True,
            'logical_lane_continuity_verified':False,'cas_admission':False,
            'reason':reason if outer else 'outer_binding_changed','collector_receipt':child,'finished_at':now()}
        evidence.write('lane-'+name+'.receipt.json',record)
        result['lanes'].append({'lane':name,'target_binding_id':context['target']['binding_id'],
            'status':'OBSERVED_CANONICAL_ONLY' if accepted else 'UNRESOLVED','outer_binding_verified':outer,
            'state':child['state'] if accepted else 'UNRESOLVED','reason':record['reason'],
            'request_counts':child['request_counts'] if child is not None else None})
        if not accepted:result['status']='STOPPED_UNRESOLVED';break
    result['phase']='final_binding_check'
    final=False
    try:final=gather_snapshot(spec)==snapshot
    except BaseException:pass
    result['final_outer_binding_verified']=final
    if not final:
        result['status']='STOPPED_UNRESOLVED';result['reason']='final_outer_binding_changed'
        for lane in result['lanes']:
            lane.update(status='UNRESOLVED',outer_binding_verified=False,state='UNRESOLVED',reason='final_outer_binding_changed')
    result['phase']='complete'
    result['finished_at']=now();evidence.write('result.json',result);return result

def main():
    args=sys.argv[1:]
    if args not in (['--preflight'],['--execute-reviewed']):
        print(json.dumps(base_receipt(),sort_keys=True));return 2
    result=base_receipt();previous=logging.root.manager.disable;logging.disable(logging.CRITICAL)
    old_alarm=signal.getsignal(signal.SIGALRM)
    def deadline(*args):raise Refused('operator_deadline_exceeded')
    signal.signal(signal.SIGALRM,deadline);signal.alarm(480)
    try:
        with redirect_stdout(c.support.Quiet()),redirect_stderr(c.support.Quiet()):
            result['phase']='input';spec=parse_spec(sys.stdin.buffer.read(MAX_INPUT+1))
            result=base_receipt(spec)
            if args==['--preflight']:preflight(spec,result)
            else:execute(spec,result)
    except Refused as error:result.update(status='REFUSED',reason=error.args[0])
    except BaseException as error:
        name=type(error).__name__
        result.update(status='REFUSED',reason='operator_failed',exception_class=name if name in {
            'FileExistsError','FileNotFoundError','NotADirectoryError','PermissionError','OperationalError',
            'ImportError','ModuleNotFoundError','TimeoutExpired','ValueError','TypeError','OSError'} else 'other')
    finally:signal.alarm(0);signal.signal(signal.SIGALRM,old_alarm);logging.disable(previous)
    if result['status']=='REFUSED':
        result['final_outer_binding_verified']=False
        for lane in result.get('lanes',[]):
            lane.update(status='UNRESOLVED',outer_binding_verified=False,state='UNRESOLVED',reason='attempt_refused')
    print(json.dumps(result,sort_keys=True,separators=(',',':')))
    return 0 if result['status'] in {'PREFLIGHT_OK','OBSERVATIONS_COMPLETE'} else 1

if __name__=='__main__':raise SystemExit(main())
