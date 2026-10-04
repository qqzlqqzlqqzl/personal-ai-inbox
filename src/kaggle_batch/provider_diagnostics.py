"""Positive-allowlist telemetry for already-attempted provider CLI calls.

This module does not decide retry, absence, claims or GPU admission. Raw output
is inspected only in memory and is never returned, logged or attached to errors.
"""
import json
import re
import subprocess

OPERATIONS = frozenset({'kernels.status', 'kernels.list', 'kernels.push',
                        'kernels.output', 'quota', 'unknown'})
ERROR_CLASSES = frozenset({'cli_nonzero', 'TimeoutExpired', 'FileNotFoundError',
                           'PermissionError', 'OSError', 'protocol_error', 'unknown'})
CATEGORIES = frozenset({'authentication_rejected', 'http_forbidden', 'http_not_found',
                        'rate_limited', 'http_5xx', 'timeout', 'connection_failure',
                        'local_permission', 'local_process_failure',
                        'ambiguous_http_status', 'unclassified'})
# requests' HTTPError rendering, as exercised by synthetic wrapper fixtures.
# Bare numbers, generic JSON, Reason-only messages and arbitrary prose are not
# evidence of a numeric HTTP response. Unknown CLI renderings remain unknown.
HTTP_ERROR = re.compile(
    r'^(?:(?:requests\.exceptions\.)?HTTPError: )?'
    r'([45][0-9]{2}) (Client|Server) Error: [^\r\n]* for url: https?://\S+$',
    re.MULTILINE)
MAX_INSPECT_BYTES = 65536


def operation(args):
    if not isinstance(args, (list, tuple)) or not args:
        return 'unknown'
    if args[0] == 'quota':
        return 'quota'
    if len(args) > 1 and args[0] == 'kernels' and args[1] in ('status', 'list', 'push', 'output'):
        return 'kernels.' + args[1]
    return 'unknown'


def project(value):
    """Re-project at each retention/export boundary; never accept free text."""
    value = value if isinstance(value, dict) else {}
    status = value.get('http_status')
    status = status if type(status) is int and 100 <= status <= 599 else None
    def enum(key, allowed, default):
        item = value.get(key)
        return item if isinstance(item, str) and item in allowed else default
    result = {'http_status': status,
              'operation': enum('operation', OPERATIONS, 'unknown'),
              'error_class': enum('error_class', ERROR_CLASSES, 'unknown'),
              'category': enum('category', CATEGORIES, 'unclassified')}
    assert len(json.dumps(result, separators=(',', ':')).encode('utf-8')) <= 256
    return result


def failure(args, *, output='', error=None):
    status, category, error_class = None, 'unclassified', 'cli_nonzero'
    if error is not None:
        if isinstance(error, subprocess.TimeoutExpired):
            error_class, category = 'TimeoutExpired', 'timeout'
        elif isinstance(error, FileNotFoundError):
            error_class, category = 'FileNotFoundError', 'local_process_failure'
        elif isinstance(error, PermissionError):
            error_class, category = 'PermissionError', 'local_permission'
        elif isinstance(error, OSError):
            error_class, category = 'OSError', 'local_process_failure'
        else:
            error_class = 'unknown'
    elif isinstance(output, str) and len(output.encode('utf-8', errors='replace')) <= MAX_INSPECT_BYTES:
        matches = list(HTTP_ERROR.finditer(output))
        statuses = {int(match[1]) for match in matches
                    if (match[1][0], match[2]) in {('4', 'Client'), ('5', 'Server')}}
        if any((match[1][0], match[2]) not in {('4', 'Client'), ('5', 'Server')}
               for match in matches):
            statuses = set()  # A contradictory wrapper cannot prove a status.
        if len(statuses) > 1:
            category = 'ambiguous_http_status'
        elif statuses:
            status = statuses.pop()
            category = {401: 'authentication_rejected', 403: 'http_forbidden',
                        404: 'http_not_found', 429: 'rate_limited'}.get(status, 'unclassified')
            if status >= 500:
                category = 'http_5xx'
    return project({'http_status': status, 'operation': operation(args),
                    'error_class': error_class, 'category': category})


def private_observer(root, *, authorize=None):
    """Lazy existing private audit sink; caller isolates all persistence errors."""
    def observe(diagnostic):
        if authorize is not None:
            authorize()
        try:
            from .exception_audit import Audit
        except ImportError:
            from exception_audit import Audit
        Audit(root).append('provider_call_failure', diagnostic=project(diagnostic))
        return True
    return observe


def safe_event(record):
    """Explicit export projection; never copy legacy owner/batch/free-text fields."""
    import math
    if not isinstance(record, dict) or record.get('event') != 'provider_call_failure':
        return None
    at = record.get('at')
    try:
        valid_time = type(at) in (int, float) and math.isfinite(at) and at >= 0
    except OverflowError:
        valid_time = False
    if not valid_time:
        return None
    result = {'event': 'provider_call_failure', 'at': at,
              'diagnostic': project(record.get('diagnostic'))}
    # The existing bridge has no verified lane ordinal; do not infer one from
    # an owner, kernel or path. A caller with a verified fixed label may include it.
    if record.get('lane') in ('primary', 'secondary', 'third', 'fourth', 'fifth'):
        result['lane'] = record['lane']
    return result
