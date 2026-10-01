"""Private loopback RSS bridge; no network fetches on RSS reads."""
import hashlib
import ipaddress
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response
import vendor_sources

router = APIRouter(prefix='/internal/vendor-feeds')


def loopback(request):
    try:
        local = ipaddress.ip_address(request.client.host).is_loopback
    except (ValueError, AttributeError):
        local = False
    # Reverse-proxied traffic must not be mistaken for a local Miniflux request.
    if not local or any(h in request.headers for h in ('x-forwarded-for', 'forwarded', 'x-real-ip')):
        raise HTTPException(403, 'Loopback requests only')


@router.get('/status')
def source_status(request: Request):
    loopback(request)
    return vendor_sources.status()


@router.get('/{key}.xml')
def feed(key: str, request: Request):
    loopback(request)
    try:
        content = vendor_sources.render(key)
        state = vendor_sources.load(key)
    except KeyError:
        raise HTTPException(404, 'Unknown source')
    except (ValueError, OSError):
        raise HTTPException(503, 'Source snapshot unavailable')
    etag = '"' + hashlib.sha256(content).hexdigest() + '"'
    headers = {'ETag': etag, 'Cache-Control': 'no-cache',
               'X-Source-State': 'stale-error' if state.get('last_error') else 'ok'}
    if request.headers.get('if-none-match') == etag:
        return Response(status_code=304, headers=headers)
    return Response(content, media_type='application/rss+xml', headers=headers)


@router.post('/{key}/refresh')
def source_refresh(key: str, request: Request, x_vendor_refresh: str = Header(default='')):
    loopback(request)
    from month_control import authenticate
    authenticate(x_vendor_refresh)
    if key not in vendor_sources.CONFIGS:
        raise HTTPException(404, 'Unknown source')
    report = vendor_sources.refresh(key, force=True)
    if report['state'] == 'error':
        from fastapi.responses import JSONResponse
        return JSONResponse(report, status_code=502)
    return report
