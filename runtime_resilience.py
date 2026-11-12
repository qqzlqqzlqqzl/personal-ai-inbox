"""Safe retry classification; never returns URLs, headers or exception messages."""
from email.utils import parsedate_to_datetime
import time


def source_retry(exc, failures, now=None):
    now = time.time() if now is None else now
    response = getattr(exc, 'response', None)
    status = getattr(response, 'status_code', None)
    category = 'transport'
    base = 1800
    if status in (401, 403):
        category, base = 'access_denied', 21600
    elif status in (404, 410):
        category, base = 'source_missing', 86400
    elif status == 429:
        category = 'rate_limited'
    elif isinstance(exc, ValueError):
        category, base = 'source_format', 21600
    elif status:
        category = 'upstream_http'
    delay = min(86400, base * 2 ** min(max(0, failures-1), 8))
    retry_after = getattr(response, 'headers', {}).get('retry-after')
    if retry_after:
        try:
            seconds = float(retry_after) if str(retry_after).isdigit() else parsedate_to_datetime(retry_after).timestamp() - now
            delay = max(delay, min(86400, max(0, seconds)))
        except (ValueError, TypeError, OverflowError):
            pass
    return {'category': category, 'delay': delay, 'http_status': status}
