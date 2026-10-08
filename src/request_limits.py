"""Actual request-body limit (including chunked bodies) and bounded auth failure throttle."""
from collections import OrderedDict, deque
import time
from starlette.responses import JSONResponse

class RequestLimits:
    def __init__(self, app, max_body=2*1024*1024):
        self.app=app; self.max_body=max_body; self.failures=OrderedDict()
    async def __call__(self, scope, receive, send):
        if scope['type']!='http':
            return await self.app(scope,receive,send)
        peer=(scope.get('client') or ('local',0))[0]; now=time.monotonic()
        limited=scope.get('path','').startswith('/mf/v1/')
        q=self.failures.get(peer,deque())
        while q and q[0]<now-300:q.popleft()
        if limited and len(q)>=30:
            return await JSONResponse({'error_message':'Too many authentication failures'},429,
                                      headers={'Retry-After':'300'})(scope,receive,send)
        body=None
        if scope.get('method') not in ('GET','HEAD','OPTIONS'):
            buffered=bytearray()
            while True:
                event=await receive()
                if event['type']=='http.disconnect':return
                chunk=event.get('body',b'')
                if len(buffered)+len(chunk)>self.max_body:
                    return await JSONResponse({'error_message':'Request too large'},413)(scope,receive,send)
                buffered.extend(chunk)
                if not event.get('more_body'):break
            # ASGI consumers need the bytes, not each transport chunk's metadata.
            # Retaining every chunk also made pop(0) replay quadratic.
            body=bytes(buffered)
            del buffered
        async def bounded_receive():
            nonlocal body
            if body is not None:
                current=body;body=None
                return {'type':'http.request','body':current,'more_body':False}
            return await receive()
        async def observe(event):
            if limited and event['type']=='http.response.start' and event['status']==401:
                q.append(time.monotonic());self.failures[peer]=q;self.failures.move_to_end(peer)
                while len(self.failures)>4096:self.failures.popitem(last=False)
            await send(event)
        await self.app(scope,bounded_receive,observe)
