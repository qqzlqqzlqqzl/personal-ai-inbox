"""Read-only complete own-account evidence; ambiguous or truncated lists fail closed."""
import csv
import hashlib
import io
import json
import re

PAGE_SIZE = 100
MAX_PAGES = 10
MAX_BYTES = 1024 * 1024
NEXT_PAGE_PREFIX = 'Next Page Token = '
VERSION_WARNING = re.compile(
    r"Warning: Looks like you're using an outdated `kaggle` version "
    r"\(installed: [A-Za-z0-9_.+!-]+\), please consider upgrading to the latest version "
    r"\([A-Za-z0-9_.+!-]+\)")


def _unique_object(pairs):
    row = {}
    for key, value in pairs:
        if key in row:
            raise ValueError('duplicate_kernel_field')
        row[key] = value
    return row


def parse_page(output):
    """Parse a complete page, retaining official continuation metadata.

    Kaggle prints its version warning and next-page token on stdout before CSV
    (and JSON). Only those exact, bounded prefixes are accepted. Arbitrary text
    is never skipped in search of a plausible header. Tokens stay in memory.
    """
    if not isinstance(output, str) or len(output.encode()) > MAX_BYTES:
        raise ValueError('invalid_kernel_list')
    # Do not normalize BOMs, ANSI escapes, or control characters into evidence.
    if any(ord(char) < 32 and char not in '\r\n\t' for char in output):
        raise ValueError('invalid_kernel_list')
    token = None
    first, separator, rest = output.partition('\n')
    if VERSION_WARNING.fullmatch(first.removesuffix('\r')):
        if not separator:
            raise ValueError('incomplete_kernel_list')
        output = rest
        first, separator, rest = output.partition('\n')
    if first.startswith(NEXT_PAGE_PREFIX):
        token = first.removesuffix('\r')[len(NEXT_PAGE_PREFIX):]
        if (not separator or not token or len(token) > 8192
                or any(char.isspace() or ord(char) < 32 for char in token)):
            raise ValueError('invalid_kernel_page_token')
        output = rest
    if output.strip() == 'Not found':
        if token is not None:
            raise ValueError('empty_page_has_continuation')
        return {'refs': [], 'next_page_token': None}
    if output.lstrip().startswith('['):
        rows = json.loads(output, object_pairs_hook=_unique_object)
        if not isinstance(rows, list):
            raise ValueError('invalid_kernel_list')
    else:
        # csv.writer always terminates the final row. An unterminated response
        # or header-only output may be truncated, not an empty official page.
        if not output.endswith('\n'):
            raise ValueError('incomplete_kernel_list')
        reader = csv.reader(io.StringIO(output, newline=''), strict=True)
        try:
            fields = next(reader)
            values = list(reader)
        except (StopIteration, csv.Error):
            raise ValueError('invalid_kernel_list') from None
        if (not fields or fields.count('ref') != 1 or len(fields) != len(set(fields))
                or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', field) for field in fields)
                or not values or any(len(value) != len(fields) for value in values)):
            raise ValueError('invalid_kernel_list')
        rows = [dict(zip(fields, value)) for value in values]
    if len(rows) > PAGE_SIZE:
        raise ValueError('unexpected_kernel_page_size')
    if not rows and token is not None:
        raise ValueError('empty_page_has_continuation')
    refs = []
    for row in rows:
        ref = row.get('ref') if isinstance(row, dict) else None
        if not isinstance(ref, str) or not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_-]+', ref):
            raise ValueError('invalid_kernel_ref')
        refs.append(ref.casefold())
    return {'refs': refs, 'next_page_token': token}


def parse_refs(output):
    return parse_page(output)['refs']


def prove_absent(client, owner, batch_id):
    """Require an empty terminal page AND positive evidence of the expected owner.

    An empty first page proves neither the authenticated identity nor absence.
    Always continue to an empty page, even when a provider silently caps page size.
    """
    try:
        from .quota_guard import query_client
    except ImportError:
        from quota_guard import query_client
    target = f'{owner}/{batch_id}'.casefold()
    seen = set()
    seen_tokens = set()
    for page in range(1, MAX_PAGES + 1):
        output = client(['kernels', 'list', '--mine', '--csv', '--page-size', str(PAGE_SIZE),
                         '--page', str(page), '--sort-by', 'dateCreated'], 20)
        parsed = parse_page(output)
        refs = parsed['refs']
        token = parsed['next_page_token']
        if token is not None:
            if token in seen_tokens:
                return None
            seen_tokens.add(token)
        if target in refs:
            return None
        if any(ref.split('/')[0] != owner.casefold() for ref in refs) or seen.intersection(refs) or len(refs) != len(set(refs)):
            return None
        if not refs:
            if not seen:
                return None
            # 0h is still an authenticated readable account; recovery needs no GPU.
            quota = query_client(client)
            if quota['state'] not in {'available', 'quota_reserved'}:
                return None
            return {'owner': owner, 'batch_id': batch_id, 'listed_count': len(seen),
                    'pages': page, 'listing_sha256': hashlib.sha256('\n'.join(sorted(seen)).encode()).hexdigest()}
        seen.update(refs)
    return None
