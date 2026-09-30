"""Read-only complete own-account evidence; ambiguous or truncated lists fail closed."""
import csv
import hashlib
import io
import json
import re

PAGE_SIZE = 100
MAX_PAGES = 10
MAX_BYTES = 1024 * 1024


def parse_refs(output):
    if not isinstance(output, str) or len(output.encode()) > MAX_BYTES:
        raise ValueError('invalid_kernel_list')
    if output.strip() == 'Not found':
        return []
    if output.lstrip().startswith('['):
        rows = json.loads(output)
        if not isinstance(rows, list):
            raise ValueError('invalid_kernel_list')
    else:
        reader = csv.DictReader(io.StringIO(output))
        if not reader.fieldnames or 'ref' not in reader.fieldnames:
            raise ValueError('invalid_kernel_list')
        rows = list(reader)
    if len(rows) > PAGE_SIZE:
        raise ValueError('unexpected_kernel_page_size')
    refs = []
    for row in rows:
        ref = row.get('ref') if isinstance(row, dict) else None
        if not isinstance(ref, str) or not re.fullmatch(r'[A-Za-z0-9_-]+/[A-Za-z0-9_-]+', ref):
            raise ValueError('invalid_kernel_ref')
        refs.append(ref.casefold())
    return refs


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
    for page in range(1, MAX_PAGES + 1):
        output = client(['kernels', 'list', '--mine', '--csv', '--page-size', str(PAGE_SIZE),
                         '--page', str(page), '--sort-by', 'dateCreated'], 20)
        refs = parse_refs(output)
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
