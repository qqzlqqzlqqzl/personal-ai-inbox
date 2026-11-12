import json,re,subprocess,time
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
"""Conservative retry timing and redacted provider failures.

Not-found is never a license to resubmit. Account/absence proof belongs to the
controller, which retains uncertain claims until repeated evidence agrees.
"""
import math

MAX_RETRY_SECONDS = 3600
ACCOUNT_ERRORS = frozenset({'authentication','auth','quota','capacity','rate_limit','rate_limited'})


class ProviderError(RuntimeError):
    CODES = frozenset({'network', 'inaccessible', 'not_found', 'authentication', 'rate_limited', 'provider_unavailable','quota','capacity','rate_limit','auth','integrity','unknown','empty_output','protocol','local_state','upstream_validation'})

    def __init__(self, code, *, operation='unknown'):
        self.code = code if code in self.CODES else 'provider_unavailable'
        self.operation = operation if operation in {'kernels.status','kernels.output','kernels.push','kernels.list','quota'} else 'unknown'
        self.scope = 'account' if self.code in ACCOUNT_ERRORS or self.operation == 'quota' else 'task'
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
    anchor = recovery.get('at')
    if anchor is not None:
        if type(anchor) not in (int,float) or not math.isfinite(anchor) or anchor < 0:
            raise ValueError('invalid_recovery_at')
        value = min(value, anchor + MAX_RETRY_SECONDS)
    return value


def classify_failure(text, *, operation='unknown'):
    """Inspect locally but return only a fixed code; never expose raw CLI output."""
    import re

    # URL paths/query strings are not error evidence (e.g. example.invalid?token).
    lower = re.sub(r'https?://\S+','[url]',str(text or '')[:65536].lower())
    statuses = set(re.findall(r'(?:^|\n)(?:[\w.]*httperror:\s*)?([45]\d\d)\s+(?:client error|server error|not found|forbidden|unauthorized|too many requests|service unavailable)',lower))
    status = next(iter(statuses)) if len(statuses)==1 else None
    def error(code):return ProviderError(code,operation=operation)
    if len(statuses)>1:
        return error('protocol')
    if status=='401' or re.search(r'unauthorized|unauthenticated|invalid credentials|invalid\s+(?:api\s+)?token', lower):
        return error('authentication')
    if status=='429' or re.search(r'too many requests|rate limit', lower):
        return error('rate_limited')
    if re.search(r'(gpu|accelerator).*?(quota|hours|allowance).*?(exceed|exhaust|insufficient|limit|reached)|quota.*?(exceed|exhaust)',lower):
        return error('quota')
    if re.search(r'no.*?(gpu|accelerator).*?available|accelerator.*?unavailable|too many.*?(session|kernel)',lower):
        return error('capacity')
    if status=='404' or re.search(r'kernel.*not found|could not find kernel',lower):
        return error('not_found')
    if status=='403' or re.search(r'forbidden|inaccessible|cannot access kernel|kernels\.get.*denied',lower):
        return error('auth' if operation in {'kernels.push','quota'} else 'inaccessible')
    if any(value in lower for value in ('timeout', 'timed out', 'connection', 'network', 'name resolution')):
        return error('network')
    return error('provider_unavailable')

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
    if code in {'auth','authentication','quota','capacity','integrity'}:
        return MAX_RETRY_SECONDS
    # Task observations are retried, never submitted again, with a bounded delay.
    return min(MAX_RETRY_SECONDS,660*2**min(max(failures-1,0),3))

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
    scope='account' if code in ACCOUNT_ERRORS else 'lane_infrastructure'
    # A legacy task's 111 errors are not 111 failures of a new account operation.
    failures=prior+1 if previous.get('code')==code and previous.get('scope')==scope else 1
    now=time.time() if now is None else now
    value={'code':code,'failures':failures,'retry_at':now+backoff(code,failures),'at':now,
           'scope':scope,'policy_version':2}
    audit.append('infrastructure_backoff',owner=owner,previous_failures=prior,**value)
    atomic_json(path,value)
    return value
