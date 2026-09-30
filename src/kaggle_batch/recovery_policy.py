"""Conservative retry timing and redacted provider failures.

Not-found is never a license to resubmit. Account/absence proof belongs to the
controller, which retains uncertain claims until repeated evidence agrees.
"""
import math


class ProviderError(RuntimeError):
    CODES = frozenset({'network', 'inaccessible', 'not_found', 'authentication', 'rate_limited', 'provider_unavailable'})

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
