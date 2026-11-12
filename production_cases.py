"""Operator-selected real incidents; fixed recipes, fresh verification and row CAS.

The model receives typed facts and case hashes, never code, URLs or credentials.
This is a bounded campaign, not permission for general code execution.
"""
import asyncio
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HOME = Path('/home/ubuntu/ai-news/.private/recovery-agent/production-campaign')
SOURCE = Path('/home/ubuntu/website-release-20261004/reader-3349cf2-source/src')
RSS = Path('/home/ubuntu/ai-news/upstream/rsshub/dist/anthropic-engineering-BXEk-g2f.mjs')
PYTHON = '/home/ubuntu/ai-news/runtime/venv/bin/python'
CASE_IDS = {58, 3395, 3630, 2054, 6382, 2671, 3208, 3709, 11475, 11422}
KINDS = {'rsshub_cards', 'mime_image', 'referer_image', 'malformed_meta', 'blocked'}


def delivered_image(status, body):
    if status != 200 or not body or len(body) > 8*1024*1024:
        return False
    from PIL import Image
    try:
        with Image.open(io.BytesIO(body)) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP', 'GIF'} or not 0 < image.width*image.height <= 32_000_000:
                return False
            image.verify()
        with Image.open(io.BytesIO(body)) as image:
            image.load()
        return True
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


def verify_cover_delivery(cover):
    import httpx
    from reader_cover_proxy import media_proxy_key
    from stabilize_media import signed_url
    url = 'http://127.0.0.1:8092'+signed_url(cover, media_proxy_key())+'?reader_width=960'
    with httpx.Client(timeout=15, trust_env=False) as client:
        with client.stream('GET', url, headers={'Accept': 'image/webp,image/*'}) as response:
            body = bytearray()
            for chunk in response.iter_bytes():
                if len(body)+len(chunk) > 8*1024*1024:
                    return {'status': response.status_code, 'verified': False}
                body.extend(chunk)
            return {'status': response.status_code, 'verified': delivered_image(response.status_code, bytes(body))}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, limit=65536):
    if path.is_symlink():
        raise ValueError('symlink')
    with path.open('rb') as stream:
        raw = stream.read(limit+1)
    if len(raw) > limit:
        raise ValueError('size')
    return raw


def atomic(path, value):
    raw = json.dumps(value, ensure_ascii=True, allow_nan=False).encode()
    pending = path.with_suffix('.pending')
    if path.is_symlink() or pending.is_symlink():
        raise ValueError('symlink')
    with pending.open('wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    pending.chmod(0o600); pending.replace(path)


def campaign(now):
    from ops_review import ops_read
    if HOME.is_symlink():
        raise ValueError('campaign_path')
    marker = HOME/'active.json'
    if not marker.exists():
        return None
    data = ops_read(marker)
    created, deadline = data.get('created_at'), data.get('deadline')
    if (data.get('armed') is not True or type(created) is not int or type(deadline) is not int
            or not created <= now <= deadline or not 0 < deadline-created <= 7200):
        return None
    if len(data.get('cases', [])) != 10 or {int(i) for i in data['cases']} != CASE_IDS:
        raise ValueError('campaign_cases')
    return data


def spec_for(target, expected, now):
    if target not in {'case_'+str(i) for i in CASE_IDS}:
        raise ValueError('case_target')
    data = campaign(now)
    if not data:
        raise ValueError('campaign_expired')
    ident = target[5:]
    raw = read(HOME/(target+'.json'))
    if sha(raw) != expected or data['cases'][ident] != expected:
        raise ValueError('case_hash')
    spec = json.loads(raw)
    if spec.get('id') != int(ident) or spec.get('kind') not in KINDS:
        raise ValueError('case_spec')
    return spec


def case_incidents(now):
    from ops_protocol import protocol_hash, check_incident
    data = campaign(now)
    if not data:
        return []
    incidents = []
    for ident, expected in data['cases'].items():
        target = 'case_'+ident
        if (HOME/(target+'.outcome.json')).exists():
            continue
        spec = spec_for(target, expected, now)
        action = 'inspect_production_case' if spec['kind'] == 'blocked' else 'repair_production_case'
        item = {'id': action+':'+target, 'target': target, 'code': spec['code'],
                'allowed_actions': [action], 'precondition': {'state_sha256': expected, 'facts': spec['facts']}}
        item['fingerprint'] = protocol_hash({'case_sha256': expected, 'target': target})
        incidents.append(check_incident(item))
    return incidents


def patch_rss(raw):
    text = raw.decode()
    changes = {
        'l(`a[class$="cardLink"]`)': 'l(`a[class$="cardLink"],article[data-listing-row] h3 a[href^="/research/"]`)',
        't.find(`h2, h3`).text()': '(t.find(`h2, h3`).text()||t.text().trim())',
        't.find(`div[class$="date"]`).text()': '(t.find(`div[class$="date"]`).text()||t.closest(`article`).find(`time`).text())',
    }
    for before, after in changes.items():
        if text.count(before) != 1:
            raise ValueError('rss_recipe_baseline')
        text = text.replace(before, after)
    return text.encode()


COVER_HELPER = '''def _verified_raster_prefix(raw):
    """Recognize raster signatures only; never treat HTML or SVG as binary images."""
    return (len(raw) >= 24 and raw.startswith(b"\\x89PNG\\r\\n\\x1a\\n") and raw[12:16] == b"IHDR"
            or len(raw) >= 10 and raw[:6] in (b"GIF87a", b"GIF89a")
            or len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
            or len(raw) >= 4 and raw[:3] == b"\\xff\\xd8\\xff")


'''


def patch_cover(raw):
    text = raw.decode()
    changes = {
        'src = str(src or "").strip()': 'src = str(src or "").strip().lstrip("\\ufffc ").strip()',
        'if image.status_code == 200 and image.headers.get("content-type", "").lower().startswith("image/"):\n                            return cover, source':
        '''if image.status_code == 200:
                            mime = image.headers.get("content-type", "").lower().split(";", 1)[0].strip()
                            if mime.startswith("image/"):
                                return cover, source
                            if mime in ("", "application/octet-stream", "binary/octet-stream"):
                                prefix = b""
                                async for chunk in image.aiter_bytes():
                                    prefix += chunk[:max(0, 32-len(prefix))]
                                    if len(prefix) >= 32:
                                        break
                                if _verified_raster_prefix(prefix):
                                    return cover, source''',
    }
    for before, after in changes.items():
        if text.count(before) != 1:
            raise ValueError('cover_recipe_baseline')
        text = text.replace(before, after)
    text = text.replace('async def _discover_original_cover(', COVER_HELPER+'async def _discover_original_cover(', 1)
    compile(text, 'content_input.py', 'exec')
    return text.encode()


def service(unit, operation):
    if unit not in {'ai-news-web.service', 'ai-news-rsshub.service'} or operation != 'restart':
        raise ValueError('service_scope')
    env = {**os.environ, 'XDG_RUNTIME_DIR': '/run/user/1000', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1000/bus'}
    subprocess.run(['systemctl', '--user', operation, unit], env=env, check=True,
                   capture_output=True, timeout=25)


def install_recipe(spec):
    """A model may use deployed code, but cannot install or restart shared code."""
    kind = 'rss' if spec['kind'] == 'rsshub_cards' else 'cover'
    path = RSS if kind == 'rss' else SOURCE/'content_input.py'
    recipe_raw = read(HOME/(kind+'.recipe.json'))
    if sha(recipe_raw) != spec['recipe_sha256']:
        raise ValueError('recipe_changed')
    recipe = json.loads(recipe_raw)
    actual = sha(read(path, 2*1024*1024))
    if actual != recipe['after_sha256']:
        raise ValueError('shared_source_change_forbidden')
    return {'changed': False, 'recipe': kind, 'after_sha256': actual}


def preview_case(spec, apply):
    os.environ['AI_NEWS_ROOT'] = str(SOURCE.parent)
    os.environ['AI_NEWS_OUTBOUND_PROXY'] = 'http://127.0.0.1:17890'
    sys.path.insert(0, str(SOURCE))
    import core, httpx
    with core.connect() as db:
        before = db.execute('SELECT * FROM analyses WHERE entry_id=?', (spec['id'],)).fetchone()
    if not before or before['feed_id'] != spec['feed_id'] or sha(before['url'].encode()) != spec['url_sha256']:
        return {'state': 'identity_changed_no_action', 'verified': False}
    # Fresh import; this subprocess does not inherit the web worker's module cache.
    import content_input
    async def resolve():
        try:
            cover, source = await content_input.discover_original_cover(before['url'], before['title'], strict=True)
            return cover, source, None
        except Exception as exc:
            return None, None, type(exc).__name__
    cover, source, error = asyncio.run(asyncio.wait_for(resolve(), 24))
    if error or not cover:
        return {'state': 'unresolved', 'verified': False, 'error_type': error or 'NoPublisherImage'}
    delivery = verify_cover_delivery(cover)
    if not delivery['verified']:
        return {'state': 'unresolved', 'verified': False, 'error_type': 'ReaderImageDeliveryError',
                'delivery_status': delivery['status']}
    if not apply:
        return {'state': 'inspected_image_available', 'verified': False}
    if before['cover_url'] == cover and before['preview_error'] is None:
        return {'state': 'already_recovered', 'verified': True}
    from repair_guard import ROW_FIELDS, transaction
    fields = list(ROW_FIELDS)
    backup = HOME/('case_'+str(spec['id'])+'.row-before.json')
    if not backup.exists():
        atomic(backup, {'entry_id': spec['id'], **{k: before[k] for k in fields}})
    now = time.time()
    original = {k: before[k] for k in fields}
    desired = {**original, 'cover_url': cover, 'cover_source': source, 'preview_error': None,
               'preview_checked_at': now, 'updated_at': now}
    with transaction('preview_row', spec['id'], original, desired) as tx:
        with core.connect() as db:
            changed = db.execute('UPDATE analyses SET cover_url=?,cover_source=?,preview_error=NULL,preview_checked_at=?,updated_at=? '
                                 'WHERE entry_id=? AND '+' AND '.join(k+' IS ?' for k in fields),
                                 (cover, source, now, now, spec['id'], *(before[k] for k in fields))).rowcount
            after = db.execute('SELECT * FROM analyses WHERE entry_id=?', (spec['id'],)).fetchone()
            verified = changed == 1 and all(after[k] == desired[k] for k in fields)
            if not verified and changed:
                raise ValueError('row_verification')
        if not changed:
            tx.cancel()  # Native CAS wrote nothing; preserve the concurrent update.
        else:
            # Recheck delivery after committing the metadata, before accepting repair.
            if not verify_cover_delivery(cover)['verified']:
                raise ValueError('post_commit_delivery_verification')
            tx.commit()
    return {'state': 'repaired' if verified else 'row_changed_no_action', 'verified': verified,
            'cover_sha256': sha(cover.encode()), 'feed_id': spec['feed_id']}


def rss_case(spec, apply):
    import httpx
    from xml.etree import ElementTree as ET
    with httpx.Client(timeout=75, trust_env=False) as client:
        rss = client.get('http://127.0.0.1:1200/anthropic/engineering').raise_for_status()
        items = ET.fromstring(rss.content).findall('.//item')
        if not items or any(not x.findtext('title') or not x.findtext('link', '').startswith('https://www.anthropic.com/') for x in items):
            raise ValueError('rss_verification')
        sys.path.insert(0, str(SOURCE))
        from initialize_secrets import read_env
        headers = {'X-Auth-Token': read_env('ai.env')['MINIFLUX_API_KEY']}
        url = 'http://127.0.0.1:8091/mf/v1/feeds/58'
        before = client.get(url, headers=headers).raise_for_status().json()
        if before['feed_url'] != 'http://127.0.0.1:1200/anthropic/engineering':
            raise ValueError('feed_identity')
        if not apply:
            return {'state': 'inspected', 'verified': False}
        client.put(url+'/refresh', headers=headers).raise_for_status()
        after = client.get(url, headers=headers).raise_for_status().json()
        verified = after['parsing_error_count'] == 0 and after['checked_at'] != before['checked_at']
        return {'state': 'repaired' if verified else 'unresolved', 'verified': verified,
                'rss_items': len(items), 'error_count': after['parsing_error_count'], 'checked_at': after['checked_at']}


def bind_parent_lifetime(expected_parent):
    # Linux children must not survive a killed orchestrator and continue writing.
    import ctypes
    import signal
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        os._exit(125)
    if os.getppid() != expected_parent:
        os._exit(125)


def execute_case(decision, now):
    target = decision['target']; apply = decision['action'] == 'repair_production_case'
    spec = spec_for(target, decision['incident']['precondition']['state_sha256'], now)
    if apply and spec['kind'] == 'blocked':
        raise ValueError('no_verified_recipe')
    outcome_path = HOME/(target+'.outcome.json')
    reservation = HOME/(target+'.reserved.json')
    if reservation.exists() or outcome_path.exists():
        return {'state': 'case_already_reserved_no_action'}
    with reservation.open('x') as stream:
        json.dump({'at': int(now), 'action': decision['action'], 'case_sha256': decision['incident']['precondition']['state_sha256']}, stream)
        stream.flush(); os.fsync(stream.fileno())
    reservation.chmod(0o600)
    # Fixed child invocation bounds network waits and isolates module imports.
    try:
        from functools import partial
        result = subprocess.run([PYTHON, str(Path(__file__).resolve()), '--case', target,
                                 decision['incident']['precondition']['state_sha256'], 'apply' if apply else 'inspect'],
                                capture_output=True, text=True, timeout=150, start_new_session=True,
                                preexec_fn=partial(bind_parent_lifetime, os.getpid()))
        outcome = json.loads(result.stdout)
        if not isinstance(outcome, dict):
            raise ValueError('case_child')
        if result.returncode:
            if outcome.get('verified') is not False:
                raise ValueError('case_child')
            error_type = outcome.get('error_type')
            if error_type is not None and (not isinstance(error_type, str)
                    or not error_type.isidentifier() or len(error_type) > 80):
                raise ValueError('case_child')
            outcome['child_returncode'] = result.returncode
    except Exception as exc:
        from repair_guard import recover
        outcome = {'state': 'failed', 'verified': False,
                   'error_type': 'CaseTimeout' if isinstance(exc, subprocess.TimeoutExpired) else 'CaseExecutionError',
                   'recovery': recover()}
    atomic(outcome_path, {'at': int(time.time()), 'target': target, **outcome})
    return outcome


def case_main(argv):
    try:
        if len(argv) != 5 or argv[1] != '--case' or argv[4] not in {'apply', 'inspect'}:
            raise ValueError('arguments')
        spec = spec_for(argv[2], argv[3], time.time())
        apply = argv[4] == 'apply'
        if apply and spec['kind'] != 'blocked':
            install_recipe(spec)  # Hash check only; never modifies shared source.
        outcome = (rss_case if spec['kind'] == 'rsshub_cards' else preview_case)(spec, apply)
        print(json.dumps(outcome, ensure_ascii=True, allow_nan=False))
        return 0 if outcome.get('verified') or not apply else 1
    except Exception as exc:
        print(json.dumps({'state': 'failed', 'verified': False, 'error_type': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    sys.exit(case_main(sys.argv))
