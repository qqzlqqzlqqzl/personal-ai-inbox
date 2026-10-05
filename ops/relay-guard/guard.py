#!/usr/bin/env python3
"""Loopback-only text inference boundary. Python stdlib; no tools or prompt logging."""
from __future__ import annotations
import collections, hashlib, http.client, json, math, os, pathlib, secrets, socket
import threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
ROOT=pathlib.Path(__file__).resolve().parents[1]
MAX_INPUT=524288; MAX_OUTPUT=2097152
class Invalid(Exception): pass

def clean_request(obj, enabled):
    allowed={'model','messages','temperature','top_p','max_tokens','max_completion_tokens','response_format','stream','n','stop'}
    if not isinstance(obj,dict) or set(obj)-allowed: raise Invalid('unsupported_field')
    if obj.get('stream',False) is not False: raise Invalid('stream_not_enabled_in_v1')
    if obj.get('n',1)!=1 or isinstance(obj.get('n',1),bool): raise Invalid('n_must_be_one')
    model=obj.get('model')
    if model not in ('public-fast','public-smart'): raise Invalid('model_not_allowed')
    if model not in enabled: raise Invalid('no_configured_provider')
    msgs=obj.get('messages')
    if not isinstance(msgs,list) or not 1<=len(msgs)<=64: raise Invalid('invalid_messages')
    for m in msgs:
        if not isinstance(m,dict) or set(m)!={'role','content'} or m['role'] not in ('system','user','assistant') or not isinstance(m['content'],str): raise Invalid('text_messages_only')
    if 'max_tokens' in obj and 'max_completion_tokens' in obj: raise Invalid('duplicate_token_limits')
    for k in ('max_tokens','max_completion_tokens'):
        if k in obj and (type(obj[k]) is not int or not 1<=obj[k]<=8192): raise Invalid('invalid_output_limit')
    if 'max_tokens' not in obj and 'max_completion_tokens' not in obj: obj['max_tokens']=2048
    for k,upper in (('temperature',2),('top_p',1)):
        if k in obj and (isinstance(obj[k],bool) or not isinstance(obj[k],(int,float)) or not math.isfinite(obj[k]) or not 0<=obj[k]<=upper): raise Invalid('invalid_sampling_parameter')
    if 'response_format' in obj:
        rf=obj['response_format']
        if not isinstance(rf,dict) or set(rf)!={'type'} or rf['type'] not in ('text','json_object'): raise Invalid('unsupported_response_format')
    if 'stop' in obj:
        stops=[obj['stop']] if isinstance(obj['stop'],str) else obj['stop']
        if not isinstance(stops,list) or not 1<=len(stops)<=4 or any(not isinstance(s,str) or not 1<=len(s)<=128 for s in stops): raise Invalid('invalid_stop')
    return obj

def clean_response(obj,model):
    if not isinstance(obj,dict): raise Invalid('upstream_invalid_contract')
    cs=obj.get('choices')
    if not isinstance(cs,list) or len(cs)!=1 or not isinstance(cs[0],dict): raise Invalid('upstream_invalid_contract')
    c=cs[0]; m=c.get('message')
    if not isinstance(m,dict) or m.get('role')!='assistant' or not isinstance(m.get('content'),str): raise Invalid('upstream_invalid_contract')
    if any(k in m for k in ('tool_calls','function_call','audio')) or c.get('finish_reason') not in ('stop','length','content_filter'): raise Invalid('upstream_unsafe_contract')
    result={'id':'chatcmpl-public-'+uuid.uuid4().hex,'object':'chat.completion','created':int(time.time()),'model':model,
            'choices':[{'index':0,'message':{'role':'assistant','content':m['content']},'finish_reason':c['finish_reason']}]}
    u=obj.get('usage',{})
    if isinstance(u,dict):
        clean={k:u[k] for k in ('prompt_tokens','completion_tokens','total_tokens') if type(u.get(k)) is int and 0<=u[k]<=10**8}
        if clean: result['usage']=clean
    return result

class Server(ThreadingHTTPServer):
    daemon_threads=True; request_queue_size=8
    def __init__(self,*args,**kwargs):
        self.work=threading.BoundedSemaphore(2); self.connections=threading.BoundedSemaphore(8)
        self.rates=collections.defaultdict(collections.deque); self.lock=threading.Lock()
        super().__init__(*args,**kwargs)
    def process_request(self,request,address):
        if not self.connections.acquire(False): self.shutdown_request(request); return
        try: super().process_request(request,address)
        except Exception: self.connections.release(); raise
    def process_request_thread(self,request,address):
        try: super().process_request_thread(request,address)
        finally: self.connections.release()
    def handle_error(self,*args): print(json.dumps({'event':'connection_error'}),flush=True)

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.0'; server_version='PublicPool/1.0'; sys_version=''
    def setup(self): super().setup(); self.connection.settimeout(15)
    def log_message(self,*args): pass
    def answer(self,status,obj):
        raw=json.dumps(obj,ensure_ascii=False,allow_nan=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff'); self.end_headers()
        try:self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError):pass
    def error(self,status,code): self.answer(status,{'error':{'message':code,'type':'public_pool_error','code':code}})
    def auth(self):
        vals=self.headers.get_all('Authorization',[])
        if len(vals)!=1 or not vals[0].startswith('Bearer ') or len(vals[0])>256: self.error(401,'authentication_required');return None
        digest=hashlib.sha256(vals[0][7:].encode()).hexdigest()
        try: clients=json.loads((ROOT/'secrets/guard-clients.json').read_text())
        except (OSError,ValueError):self.error(503,'credentials_unavailable');return None
        found=next((v for k,v in clients.items() if secrets.compare_digest(k,digest)),None)
        backend_key=found['backend_key'] if found else vals[0][7:]
        conn=None
        try:
            conn=http.client.HTTPConnection('127.0.0.1',18767,timeout=5)
            conn.request('GET','/v1/models',headers={'Authorization':'Bearer '+backend_key})
            res=conn.getresponse();raw=res.read(262145)
            if res.status in (401,403):self.error(401,'invalid_api_key');return None
            if res.status!=200 or len(raw)>262144:self.error(503,'model_catalog_unavailable');return None
            data=json.loads(raw)
            if not isinstance(data,dict) or not isinstance(data.get('data'),list):raise ValueError()
            models=sorted({m['id'] for m in data['data'] if isinstance(m,dict) and m.get('id') in ('public-fast','public-smart')})
            found={'name':found['name'] if found else 'newapi-token','backend_key':backend_key,'models':models}
        except (OSError,ValueError,http.client.HTTPException):
            self.error(503,'model_catalog_unavailable');return None
        finally:
            if conn:conn.close()
        with self.server.lock:
            q=self.server.rates[digest]; now=time.monotonic()
            while q and now-q[0]>60:q.popleft()
            if len(q)>=30:self.error(429,'request_rate_limit');return None
            q.append(now)
        return found
    def cfg(self):return json.loads((ROOT/'config/guard.json').read_text())
    def do_GET(self):
        if self.path not in ('/healthz','/v1/models'):self.error(404,'endpoint_not_allowed');return
        if self.path=='/healthz':self.answer(200,{'status':'running','service':'public-ai-guard','version':'1.0'});return
        client=self.auth()
        if client is None:return
        models=client['models']
        self.answer(200,{'object':'list','data':[{'id':m,'object':'model','created':0,'owned_by':'public-untrusted'} for m in models if m in ('public-fast','public-smart')]})
    def do_POST(self):
        started=time.monotonic()
        if self.path!='/v1/chat/completions':self.error(404,'endpoint_not_allowed');return
        client=self.auth()
        if client is None:return
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Encoding'):self.error(400,'encoded_request_not_allowed');return
        lengths=self.headers.get_all('Content-Length',[])
        if len(lengths)!=1 or not lengths[0].isdigit():self.error(411,'content_length_required');return
        size=int(lengths[0])
        if not 1<=size<=MAX_INPUT:self.error(413,'request_too_large');return
        if self.headers.get('Content-Type','').split(';')[0].strip().lower()!='application/json':self.error(415,'json_required');return
        try:
            raw=self.rfile.read(size)
            if len(raw)!=size:raise Invalid('incomplete_body')
            obj=json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            obj=clean_request(obj,client['models'])
        except Invalid as e:self.error(503 if str(e)=='no_configured_provider' else 400,str(e));return
        except (ValueError,UnicodeError,RecursionError,socket.timeout):self.error(400,'invalid_json');return
        if not self.server.work.acquire(False):self.error(429,'concurrency_limit');return
        status=502; conn=None
        try:
            conn=http.client.HTTPConnection('127.0.0.1',18767,timeout=60)
            conn.request('POST','/v1/chat/completions',json.dumps(obj,allow_nan=False).encode(),{'Content-Type':'application/json','Authorization':'Bearer '+client['backend_key']})
            res=conn.getresponse(); payload=res.read(MAX_OUTPUT+1)
            if len(payload)>MAX_OUTPUT:raise Invalid('upstream_response_too_large')
            if res.status!=200:
                status=503 if res.status in (401,402,403,429,503) else 502
                self.error(status,'no_usable_upstream');return
            if 'json' not in res.getheader('Content-Type','').lower():raise Invalid('upstream_non_json')
            reply=clean_response(json.loads(payload),obj['model'])
            if obj.get('response_format',{}).get('type')=='json_object':
                if not isinstance(json.loads(reply['choices'][0]['message']['content']),dict):raise Invalid('upstream_invalid_json_object')
            status=200;self.answer(status,reply)
        except Invalid as e:self.error(502,str(e))
        except (OSError,ValueError,KeyError,http.client.HTTPException,RecursionError):self.error(502,'upstream_failed')
        finally:
            if conn:conn.close()
            self.server.work.release()
            print(json.dumps({'event':'inference','client':client.get('name','unknown'),'status':status,'latency_ms':round((time.monotonic()-started)*1000),'input_bytes':size}),flush=True)

if __name__=='__main__':
    os.umask(0o077)
    Server(('127.0.0.1',18765),Handler).serve_forever(poll_interval=0.5)
