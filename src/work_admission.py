"""Optional admission at a new request or transaction boundary.

One admitted request/transaction may finish. The next operation rechecks current
policy. Default callers keep their existing behavior.
"""
from contextlib import asynccontextmanager,contextmanager

class AdmissionStopped(Exception):
    pass

def check(admission):
    if admission is not None:admission()

def options(admission):
    return {} if admission is None else {'admission':admission}

async def async_call(admission, function, /, *args, **kwargs):
    check(admission)
    result=await function(*args, **kwargs)
    check(admission)
    return result

class _AdmittedClient:
    def __init__(self,client,admission):
        self.client,self.admission=client,admission
    def __getattr__(self,name):
        value=getattr(self.client,name)
        if name not in {'get','post','put','patch','delete','head','options','request','send'}:
            return value
        async def request(*args,**kwargs):
            return await async_call(self.admission,value,*args,**kwargs)
        return request
    @asynccontextmanager
    async def stream(self,*args,**kwargs):
        check(self.admission)
        async with self.client.stream(*args,**kwargs) as response:
            check(self.admission)
            yield response
    async def __aenter__(self):
        check(self.admission)
        await self.client.__aenter__()
        return self
    async def __aexit__(self,*args):
        return await self.client.__aexit__(*args)

def http_client(admission=None,**kwargs):
    import httpx
    check(admission)
    if admission is None:return httpx.AsyncClient(**kwargs)
    hooks={key:list(value) for key,value in kwargs.pop('event_hooks',{}).items()}
    async def before_request(request):
        check(admission)
    hooks.setdefault('request',[]).insert(0,before_request)
    # HTTPX hooks run on every redirect hop as well as the first request.
    client=httpx.AsyncClient(event_hooks=hooks,**kwargs)
    return _AdmittedClient(client,admission)


class _AdmittedSyncClient:
    def __init__(self,client,admission):
        self.client,self.admission=client,admission
    def __getattr__(self,name):
        value=getattr(self.client,name)
        if name not in {'get','post','put','patch','delete','head','options','request','send'}:return value
        def request(*args,**kwargs):
            check(self.admission)
            result=value(*args,**kwargs)
            check(self.admission)
            return result
        return request
    @contextmanager
    def stream(self,*args,**kwargs):
        check(self.admission)
        with self.client.stream(*args,**kwargs) as response:
            check(self.admission)
            yield response
    def __enter__(self):
        check(self.admission)
        self.client.__enter__()
        return self
    def __exit__(self,*args):return self.client.__exit__(*args)

def sync_http_client(admission=None,**kwargs):
    import httpx
    check(admission)
    if admission is None:return httpx.Client(**kwargs)
    hooks={key:list(value) for key,value in kwargs.pop('event_hooks',{}).items()}
    def before_request(request):check(admission)
    hooks.setdefault('request',[]).insert(0,before_request)
    return _AdmittedSyncClient(httpx.Client(event_hooks=hooks,**kwargs),admission)
