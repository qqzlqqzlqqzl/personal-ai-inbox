"""Bounded read-only owner/status collector. Default is DRY_RUN.

Only --execute-reviewed may perform one IntrospectToken and one exact-ref
GetKernelSessionStatus request. Private stdin is supplied from an existing
ledger receipt by the operator. No claims, configuration or provider writes.
"""
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import hashlib
import importlib.util
import importlib.metadata
import json
import logging
import math
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import types
import uuid

SUPPORT_SHA256='8580f62cc92c56f9c4218732450887c1aacfab07a7fe82f0dbe94b5cfa3f6788'
SUPPORT_PATH=Path(__file__).with_name('transport_support.py')
_support_bytes=SUPPORT_PATH.read_bytes()
if hashlib.sha256(_support_bytes).hexdigest()!=SUPPORT_SHA256:
    raise SystemExit('support_source_changed')
# Compile the same verified bytes. -B prevents writing pyc, not reading one.
support=types.ModuleType('owner_status_support');support.__file__=str(SUPPORT_PATH)
exec(compile(_support_bytes,str(SUPPORT_PATH),'exec'),support.__dict__)
Refused=support.Refused
require=support.require

LANES=frozenset(('primary','secondary','third','fourth','fifth'))
OWNER=re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
REF=re.compile(r'[A-Za-z0-9_-]{1,128}/[A-Za-z0-9_-]{1,256}\Z')
STATES=frozenset(('QUEUED','RUNNING','COMPLETE','ERROR','CANCEL_REQUESTED','CANCEL_ACKNOWLEDGED','NEW_SCRIPT'))
ENDPOINTS={'identity':'https://api.kaggle.com/v1/security.OAuthService/IntrospectToken',
           'status':'https://api.kaggle.com/v1/kernels.KernelsApiService/GetKernelSessionStatus'}
MAX_RESPONSE=4*1024*1024
PROXY_ENV=('AI_NEWS_OUTBOUND_PROXY','HTTP_PROXY','http_proxy','HTTPS_PROXY','https_proxy',
           'ALL_PROXY','all_proxy','NO_PROXY','no_proxy','REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE')

def verified_sdk_sources():
    """Read and hash once, then retain those exact source bytes for execution."""
    raw=support.bounded_read(Path(__file__).with_name('SOURCE_PINS.json'),100000)
    require(hashlib.sha256(raw).hexdigest()==support.PINS_SHA256,'source_pin_manifest_changed')
    pins=support.parse_json(raw);distributions={};modules={}
    for name,version in pins['versions'].items():
        dist=importlib.metadata.distribution(name)
        require(dist.version==version,'package_version_changed');distributions[name]=dist
    for row in pins['files']:
        path=distributions[row['package']].locate_file(row['path'])
        data=support.bounded_read(path,2_000_000)
        require(len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256'],'installed_source_changed')
        if row['package']=='kagglesdk':
            parts=row['path'][:-3].split('/');package=parts[-1]=='__init__'
            name='.'.join(parts[:-1] if package else parts)
            require(name not in modules,'duplicate_sdk_module')
            modules[name]=(data,str(path),package)
    return modules

class VerifiedSDKLoader:
    """Serve only verified SDK source bytes; never load a .pyc or CLI module."""
    def __init__(self,modules):self.modules=modules
    def __enter__(self):
        require(not any(n=='kaggle' or n.startswith('kaggle.') or n=='kagglesdk' or n.startswith('kagglesdk.')
                        for n in sys.modules),'preloaded_kaggle_module_refused')
        sys.meta_path.insert(0,self);return self
    def __exit__(self,*args):sys.meta_path.remove(self)
    def find_spec(self,fullname,path=None,target=None):
        if fullname=='kaggle' or fullname.startswith('kaggle.'):
            raise Refused('cli_import_refused')
        if fullname=='kagglesdk' or fullname.startswith('kagglesdk.'):
            require(fullname in self.modules,'unreviewed_sdk_module_refused')
            _,filename,package=self.modules[fullname]
            spec=importlib.util.spec_from_loader(fullname,self,origin=filename,is_package=package)
            spec.has_location=True
            return spec
        return None
    def create_module(self,spec):return None
    def exec_module(self,module):
        data,filename,package=self.modules[module.__name__]
        module.__file__=filename
        if package:module.__path__=[str(Path(filename).parent)]
        exec(compile(data,filename,'exec'),module.__dict__)

def utc_now():
    return datetime.now(timezone.utc).isoformat()

def timestamp(value):
    return type(value) in (int,float) and math.isfinite(value) and 0<value<=time.time()+300

def binding_id(value):
    if not isinstance(value,str):return False
    try:parsed=uuid.UUID(value)
    except ValueError:return False
    return parsed.version==4 and str(parsed)==value

def parse_context(raw):
    require(len(raw)<=32768,'context_too_large')
    data=support.parse_json(raw)
    require(type(data)is dict and set(data)=={'lane','expected_owner','target'},'context_shape')
    require(data['lane'] in LANES if isinstance(data['lane'],str) else False,'lane_shape')
    owner=data['expected_owner'];target=data['target']
    require(isinstance(owner,str) and OWNER.fullmatch(owner),'owner_shape')
    require(type(target)is dict and set(target) in (
        {'binding_id','claimed_ref','ledger_observed_at'},
        {'binding_id','claimed_ref','ledger_observed_at','version_receipt'}),'target_shape')
    require(binding_id(target['binding_id']),'binding_id_shape')
    ref=target['claimed_ref']
    require(isinstance(ref,str) and REF.fullmatch(ref) and ref.split('/')[0]==owner,'exact_target_owner_mismatch')
    require(timestamp(target['ledger_observed_at']),'ledger_observation_time')
    if 'version_receipt' in target:
        receipt=target['version_receipt']
        require(type(receipt)is dict and set(receipt)=={'binding_id','version_label','verified_at'},'version_receipt_shape')
        require(receipt['binding_id']==target['binding_id'] and timestamp(receipt['verified_at'])
                and isinstance(receipt['version_label'],str)
                and re.fullmatch(r'[1-9][0-9]{0,8}',receipt['version_label']),'version_receipt_binding')
    return data

def token_snapshot(path, *, read=False):
    """One no-follow descriptor walk; bind parent chain and private leaf."""
    require(isinstance(path,str) and os.path.isabs(path),'explicit_token_file_required')
    path=Path(os.path.abspath(path));fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    leaf=None;parents=[]
    parent_identity=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode)
    leaf_identity=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    try:
        parents.append(parent_identity(os.fstat(fd)))
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=child;parents.append(parent_identity(os.fstat(fd)))
        leaf=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        before=os.fstat(leaf)
        require(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid()
                and before.st_mode & 0o077==0 and before.st_nlink==1
                and 0<before.st_size<=16384,'private_token_boundary')
        data=None
        if read:
            parts=[];remaining=16385
            while remaining:
                part=os.read(leaf,min(65536,remaining))
                if not part:break
                parts.append(part);remaining-=len(part)
            data=b''.join(parts)
            require(len(data)<=16384,'token_too_large')
        after=os.fstat(leaf)
        require(leaf_identity(before)==leaf_identity(after),'token_changed_during_read')
        return (tuple(parents),leaf_identity(after)),data
    finally:
        if leaf is not None:os.close(leaf)
        os.close(fd)

class TokenBinding:
    def __init__(self):
        self.path=os.environ.get('KAGGLE_API_TOKEN')
        self.identity,data=token_snapshot(self.path,read=True)
        self.token=data.decode('utf-8').strip()
        require(1<=len(self.token)<=16384 and all(33<=ord(c)<127 for c in self.token),'token_shape')
    def verify(self):
        require(os.environ.get('KAGGLE_API_TOKEN')==self.path,'token_path_changed')
        current,_=token_snapshot(self.path)
        require(current==self.identity,'token_binding_changed')

def identity_receipt(body, observed_epoch):
    # The caller checks exact owner separately; raw required fields only.
    if type(body)is not dict:return False,'identity_body_shape',False
    if type(body.get('active'))is not bool or body['active'] is not True:return False,'token_not_explicitly_active',False
    if not isinstance(body.get('username'),str) or not body['username']:return False,'identity_username_missing',False
    if 'exp' in body and (type(body['exp'])is not int or body['exp']<=observed_epoch):return False,'identity_expiry_invalid',True
    return True,'identity_fields_observed','exp' in body

def status_receipt(body):
    if type(body)is not dict or 'status' not in body:return 'UNRESOLVED','raw_status_missing'
    state=body['status']
    if type(state)is not str or state not in STATES:return 'UNRESOLVED','raw_status_unknown'
    return state,'raw_status_observed'

class Captured(Exception):
    """Stop before SDK response-model defaults; the official request was sent."""

class CaptureTransport:
    def __init__(self, token_binding, proxy, context):
        self.token_binding=token_binding;self.proxy=proxy;self.context=context
        self.owner_verified=False;self.active_phase=None;self.plan={};self.receipts={};self.raw={}
        self.counts={'identity':0,'status':0};self.started=set()
        self.proxy_environment={key:os.environ.get(key) for key in PROXY_ENV}
    def verify_binding(self):
        self.token_binding.verify()
        require({key:os.environ.get(key) for key in PROXY_ENV}==self.proxy_environment,'proxy_environment_changed')
        require(support.existing_proxy()==self.proxy,'proxy_binding_changed')
    def arm(self,phase):
        require(phase in ENDPOINTS and phase not in self.started and self.active_phase is None,'phase_reuse_refused')
        require(phase=='identity' or self.owner_verified is True,'owner_unverified')
        if phase=='identity':body={'token':self.token_binding.token}
        else:
            owner,slug=self.context['target']['claimed_ref'].split('/')
            body={'userName':owner,'kernelSlug':slug}
            if 'version_receipt' in self.context['target']:
                body['versionLabel']=self.context['target']['version_receipt']['version_label']
        self.plan[phase]=body;self.active_phase=phase;self.started.add(phase)
    def send(self,session,request,original_send,**kwargs):
        phase=self.active_phase
        require(phase in ENDPOINTS and self.counts[phase]==0,'extra_request_refused')
        require(phase=='identity' or self.owner_verified is True,'owner_unverified')
        self.verify_binding()
        require(request.method=='POST' and request.url==ENDPOINTS[phase],'endpoint_refused')
        require(request.headers.get('Authorization')=='Bearer '+self.token_binding.token,'token_header_changed')
        require(isinstance(request.body,(str,bytes)) and len(request.body)<=32768,'request_shape')
        require(support.parse_json(request.body)==self.plan[phase],'exact_request_changed')
        mapping=kwargs.get('proxies')
        proxies=support.bound_proxy_mapping({} if mapping is None else mapping,self.proxy)
        require(kwargs.get('verify',True)is True and not kwargs.get('cert'),'tls_configuration_requires_review')
        require(session.get_adapter(request.url).max_retries.total==0,'retry_configuration_refused')
        kwargs.update(timeout=(5,15),allow_redirects=False,stream=True,verify=True,proxies=proxies)
        self.counts[phase]+=1
        self.receipts[phase]={'request_started_at':utc_now(),'http_status':None,'response_observed_at':None,'reason':'transport_failed'}
        response=original_send(session,request,**kwargs)
        try:
            receipt=self.receipts[phase]
            require(type(response.status_code)is int and 100<=response.status_code<=599,'http_status_shape')
            receipt.update(http_status=response.status_code,response_observed_at=utc_now(),observed_epoch=time.time())
            if response.status_code!=200:
                receipt['reason']='http_non_success';self.verify_binding();raise Captured()
            require(response.headers.get('Content-Type','').split(';')[0].strip().lower()=='application/json','response_content_type')
            data=response.raw.read(MAX_RESPONSE+1,decode_content=True)
            require(len(data)<=MAX_RESPONSE,'response_too_large')
            parsed=support.parse_json(data)
            self.verify_binding()
            self.raw[phase]=parsed
            receipt.update(response_observed_at=utc_now(),observed_epoch=time.time(),reason='raw_response_captured')
            raise Captured()
        finally:response.close()
    def invoke(self,phase,callback):
        self.arm(phase)
        try:
            callback()
            raise Refused('sdk_send_not_captured')
        except Captured:
            require(self.counts[phase]==1,'capture_without_request')
        finally:self.active_phase=None

def base_result(context=None):
    result={'schema':'kaggle-owner-status-v2','status':'UNRESOLVED','owner_verified_at_observation':False,
        'status_observed':False,'exact_request_bound':False,'binding_verified_at_finish':False,
        'state':'UNRESOLVED','specific_submission_version_verified':False,'absence_proof':False,
        'complete_listing':False,'claim_transition_allowed':False,'submission_allowed':False,
        'request_counts':{'identity':0,'status':0},'maximum_business_requests':2}
    if context:
        target=context['target'];result.update(lane=context['lane'],target_binding_id=target['binding_id'],
            ledger_observed_at=target['ledger_observed_at'],version_request_bound='version_receipt' in target)
    return result

def finalize_binding(result,gate):
    try:
        gate.verify_binding();result['binding_verified_at_finish']=True
    except BaseException as error:
        result.update(status='UNRESOLVED',state='UNRESOLVED',status_observed=False,
            owner_verified_at_observation=False,exact_request_bound=False,binding_verified_at_finish=False,
            reason=error.args[0] if type(error)is Refused else 'final_binding_check_failed')
    return result

def collect(context, gate, identity_call, status_call):
    result=base_result(context);result['collection_started_at']=utc_now()
    try:
        gate.invoke('identity',identity_call)
        gate.verify_binding()
        receipt=gate.receipts['identity']
        if receipt['http_status']!=200:
            result['reason']='identity_http_unresolved';return result
        valid,reason,expiry_checked=identity_receipt(gate.raw.get('identity'),receipt['observed_epoch'])
        result['identity_expiry_checked']=expiry_checked
        if not valid:result['reason']=reason;return result
        if gate.raw['identity']['username']!=context['expected_owner']:
            result['reason']='identity_owner_mismatch';return result
        gate.owner_verified=True;result['owner_verified_at_observation']=True
        # Identity is established only for that observation. Expiry and file
        # binding are rechecked before the second request; no token refresh.
        expiry=gate.raw['identity'].get('exp')
        if expiry is not None and expiry<=time.time():
            result['reason']='identity_expired_before_status';return result
        gate.invoke('status',status_call)
        if gate.receipts['status']['http_status']!=200:
            result['reason']='status_http_unresolved';return result
        state,reason=status_receipt(gate.raw.get('status'))
        result.update(state=state,reason=reason,status='OBSERVED' if state!='UNRESOLVED' else 'UNRESOLVED',
            requires_semantic_review=state in {'CANCEL_REQUESTED','CANCEL_ACKNOWLEDGED','NEW_SCRIPT'},
            status_observed=state!='UNRESOLVED')
        return result
    except Refused as error:
        result['reason']=error.args[0];return result
    except BaseException:
        result['reason']='collection_failed';return result
    finally:
        result['request_counts']=dict(gate.counts)
        result['exact_request_bound']=gate.counts['status']==1 and gate.owner_verified is True
        result['observations']={phase:{k:v for k,v in row.items() if k!='observed_epoch'} for phase,row in gate.receipts.items()}
        result['collection_finished_at']=utc_now()
        finalize_binding(result,gate)

def execute(context):
    modules=verified_sdk_sources()
    token=TokenBinding();proxy=support.existing_proxy()
    with VerifiedSDKLoader(modules):
        return execute_verified(context,token,proxy)

def execute_verified(context,token,proxy):
    import requests
    gate=CaptureTransport(token,proxy,context);original=requests.sessions.Session.send
    def guarded(session,request,**kwargs):return gate.send(session,request,original,**kwargs)
    requests.sessions.Session.send=guarded
    try:
        from kagglesdk.kaggle_env import KaggleEnv
        from kagglesdk.kaggle_http_client import KaggleHttpClient
        from kagglesdk.security.services.oauth_service import OAuthClient
        from kagglesdk.security.types.oauth_service import IntrospectTokenRequest
        from kagglesdk.kernels.services.kernels_api_service import KernelsApiClient
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionStatusRequest
        identity=IntrospectTokenRequest();identity.token=token.token
        status=ApiGetKernelSessionStatusRequest()
        status.user_name,status.kernel_slug=context['target']['claimed_ref'].split('/')
        if 'version_receipt' in context['target']:status.version_label=context['target']['version_receipt']['version_label']
        with KaggleHttpClient(env=KaggleEnv.PROD,verbose=False,api_token=token.token) as client:
            result=collect(context,gate,lambda:OAuthClient(client).introspect_token(identity),
                lambda:KernelsApiClient(client).get_kernel_session_status(status))
        result=finalize_binding(result,gate)
        result['collection_finished_at']=utc_now()
        return result
    finally:requests.sessions.Session.send=original

def main():
    args=sys.argv[1:]
    if args not in (['--execute-reviewed'],['--check-input']):
        result=base_result();result.update(status='DRY_RUN',reason='explicit_reviewed_execution_required')
        print(json.dumps(result,sort_keys=True,separators=(',',':')));return 2
    prior_logging=logging.root.manager.disable;logging.disable(logging.CRITICAL)
    prior_alarm=signal.getsignal(signal.SIGALRM)
    def deadline(*args):raise Refused('deadline_exceeded')
    signal.signal(signal.SIGALRM,deadline);signal.alarm(55)
    result=base_result()
    try:
        with redirect_stdout(support.Quiet()),redirect_stderr(support.Quiet()):
            context=parse_context(sys.stdin.buffer.read(32769))
            result=base_result(context)
            if args==['--check-input']:result.update(status='DRY_RUN',reason='input_valid_no_credentials_read')
            else:result=execute(context)
    except Refused as error:result.update(status='REFUSED',reason=error.args[0])
    except BaseException:result.update(status='REFUSED',reason='collector_failed')
    finally:
        signal.alarm(0);signal.signal(signal.SIGALRM,prior_alarm);logging.disable(prior_logging)
    print(json.dumps(result,sort_keys=True,separators=(',',':')))
    return 0 if result['status'] in {'OBSERVED','DRY_RUN'} else 1

if __name__=='__main__':raise SystemExit(main())
