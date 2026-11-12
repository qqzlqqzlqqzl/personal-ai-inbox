"""Apply strict source transforms, preserving source differences and rollback copies."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

SOURCES = (Path('/home/ubuntu/ai-news/src'), Path('/home/ubuntu/website-release-20261004/reader-3349cf2-source/src'))


def vendor(text):
    if 'from runtime_resilience import source_retry' in text:
        return text
    replacements = [
        ('import httpx\n', 'import httpx\nfrom runtime_resilience import source_retry\n'),
        ('next_run_at=now + INTERVAL, last_error=None, last_added=len(pending))',
         'next_run_at=now + INTERVAL, last_error=None, last_added=len(pending), consecutive_failures=0)'),
        ("state.update(last_attempt_at=now, next_run_at=now + RETRY, last_error={'type': type(exc).__name__, 'at': now,\n                             'http_status': exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None})",
         "failures = min(100, int(state.get('consecutive_failures', 0)) + 1)\n                retry = source_retry(exc, failures, now)\n                state.update(last_attempt_at=now, next_run_at=now + retry['delay'], consecutive_failures=failures,\n                             last_error={'type': type(exc).__name__, 'at': now, **retry})"),
    ]
    for before, after in replacements:
        if text.count(before) != 1:
            raise ValueError('unexpected_vendor_source')
        text = text.replace(before, after)
    return text


def preview(text):
    if 'preview_error LIKE' in text:
        return text
    before = "(preview_checked_at IS NULL OR (preview_error IS NOT NULL AND preview_checked_at <= ?))"
    after = "(preview_checked_at IS NULL OR (preview_error IS NOT NULL AND preview_checked_at <= ? - CASE WHEN preview_error LIKE '% HTTP 401' OR preview_error LIKE '% HTTP 403' OR preview_error LIKE '% HTTP 404' OR preview_error LIKE '% HTTP 410' THEN 604800 ELSE 21600 END))"
    if text.count(before) != 1 or text.count('values = [now - RETRY_SECONDS]') != 1:
        raise ValueError('unexpected_preview_source')
    return text.replace(before, after).replace('values = [now - RETRY_SECONDS]', 'values = [now]')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    changes = {}
    helper = Path(__file__).with_name('runtime_resilience.py').read_bytes()
    for root in SOURCES:
        changes[root/'vendor_sources.py'] = vendor((root/'vendor_sources.py').read_text()).encode()
        changes[root/'runtime_resilience.py'] = helper
    target = SOURCES[1]/'preview_worker.py'
    changes[target] = preview(target.read_text()).encode()
    changes = {p: b for p,b in changes.items() if not p.exists() or p.read_bytes() != b}
    if not args.apply:
        print(json.dumps({'planned_paths': [str(p) for p in changes]}))
        return
    backup = Path('/home/ubuntu/cloud-maintenance/backups')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    manifest = []
    for i, (path, data) in enumerate(changes.items()):
        original = path.stat() if path.exists() else None
        saved = backup / str(i)
        if original:
            shutil.copy2(path, saved)
        mode = original.st_mode & 0o777 if original else 0o600
        fd, temporary = tempfile.mkstemp(prefix='.resilience-', dir=path.parent)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.chown(temporary, original.st_uid if original else 1000, original.st_gid if original else 1000)
        os.replace(temporary, path)
        manifest.append({'path': str(path), 'before': str(saved) if original else None,
                         'after_sha256': hashlib.sha256(data).hexdigest()})
    (backup/'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'modified': len(changes), 'rollback_manifest': str(backup/'manifest.json')}))


if __name__ == '__main__':
    main()
