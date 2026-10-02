import json,re,subprocess,time
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
"""Conservative retry timing and redacted provider failures.

Not-found is never a license to resubmit. Account/absence proof belongs to the
controller, which retains uncertain claims until repeated evidence agrees.
"""
import math


class ProviderError(RuntimeError):
    CODES = frozenset({'network', 'inaccessible', 'not_found', 'authentication', 'rate_limited', 'provider_unavailable','quota','capacity','rate_limit','auth','integrity','unknown','empty_output','protocol','local_state','upstream_validation'})

    def __init__(self, code):
        self.code = code if code in self.CODES else 'provider_unavailable'
        super().__init__(self.code)


def effective_retry_at(recovery):
    if not isinstance(recovery, dict):
        raise TypeError('invalid_recovery_record')
    value = recovery.get('retry_at', 0)
    if isinstance(value, bool):
        raise TypeError('invalid_retry_at')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError('invalid_retry_at') from None
    if not math.isfinite(value) or value < 0:
        raise ValueError('invalid_retry_at')
    return value


def classify_failure(text):
    """Inspect locally but return only a fixed code; never expose raw CLI output."""
    import re

    lower = text.lower()
    if re.search(r'\b401\b|unauthorized|unauthenticated|invalid credentials', lower):
        return ProviderError('authentication')
    if re.search(r'\b(?:403|404)\b|forbidden|not found|inaccessible', lower):
        return ProviderError('inaccessible')
    if re.search(r'\b429\b|too many requests|rate limit', lower):
        return ProviderError('rate_limited')
    if any(value in lower for value in ('timeout', 'timed out', 'connection', 'network', 'name resolution')):
        return ProviderError('network')
    return ProviderError('provider_unavailable')

def safe_summary(text,limit=1200):
    """Return a bounded diagnostic safe enough for private audit logs."""
    value=str(text or '')
    def scrub_url(match):
        try:
            parsed=urlsplit(match.group(0))
            host=parsed.hostname or ''
            return urlunsplit((parsed.scheme,host,parsed.path,'',''))
        except ValueError:
            return '[URL]'
    value=re.sub(r'https?://[^\s<>"\x27]+',scrub_url,value)
    value=re.sub(r'(?i)\bBearer\s+\S+','Bearer [REDACTED]',value)
    value=re.sub(r'(?i)\b(?:KGAT_|sk-|apify_api_)[\w-]+','[REDACTED]',value)
    value=re.sub(r'(?i)((?:api[_-]?key|token|password|secret|authorization|cookie)\s*[=:]\s*)[^\s,;]+',r'\1[REDACTED]',value)
    value=' '.join(value.split())
    return value[:limit] or None

def classify(text):
    text=text.lower()
    if re.search(r'(gpu|accelerator).*?(quota|hours|allowance).*?(exceed|exhaust|insufficient|limit|reached)|quota.*?(exceed|exhaust)|(?:exhausted|exceeded|insufficient|not enough).*?(?:gpu|accelerator).*?(?:quota|hours)',text):return 'quota'
    if re.search(r'no.*?(gpu|accelerator).*?available|accelerator.*?unavailable|too many.*?(session|kernel)',text):return 'capacity'
    if '429' in text or 'too many requests' in text:return 'rate_limit'
    if "permission 'kernels.get' was denied" in text or 'cannot access kernel' in text:return 'inaccessible'
    if re.search(r"\b(401|403)\b|unauthorized|forbidden|invalid.*token|permission.{0,120}(?:was )?denied|access denied",text):return 'auth'
    if re.search(r'\b404\b|kernel.*not found',text):return 'not_found'
    if re.search(r'timeout|timed out|connection|proxy|temporary failure|\b50[234]\b',text):return 'network'
    return 'unknown'

def exception_code(exc):
    if isinstance(exc,ProviderError):return exc.code
    if isinstance(exc,(subprocess.TimeoutExpired,TimeoutError,ConnectionError)):return 'network'
    if isinstance(exc,ValueError):return 'integrity'
    # HTTP exceptions are recognized by type without serializing their URLs.
    if type(exc).__module__.startswith(('httpx','httpcore')):return 'network'
    return 'unknown'

def backoff(code,failures):
    if code=='quota':return 6*3600
    if code=='rate_limited':code='rate_limit'
    if code in ('auth','authentication','inaccessible','integrity'):return 24*3600
    # Transient and unclassified failures must not silently park a lane for six
    # hours. The 11-minute watchdog will keep the campaign alive while bounded
    # exponential backoff protects Kaggle from tight retry loops.
    return min(3600,660*2**min(max(failures-1,0),3))

def record_failure(root,code,audit,owner,now=None):
    try:from .batch_control import atomic_json
    except ImportError:from batch_control import atomic_json
    path=Path(root)/'recovery.json'
    try:
        previous=json.loads(path.read_text()) if path.exists() else {}
    except (OSError,ValueError,RecursionError):
        previous={}
    if not isinstance(previous,dict):previous={}
    prior=previous.get('failures',0)
    if type(prior) is not int or prior<0:prior=0
    failures=prior+1 if previous.get('code')==code else 1
    now=time.time() if now is None else now
    value={'code':code,'failures':failures,'retry_at':now+backoff(code,failures),'at':now}
    audit.append('infrastructure_backoff',owner=owner,**value)
    atomic_json(path,value)
    return value
