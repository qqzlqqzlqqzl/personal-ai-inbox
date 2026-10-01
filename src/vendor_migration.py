"""Reversible in-place feed URL cutover; dry-run unless --apply is explicit."""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import tempfile
from xml.etree import ElementTree as ET
import httpx
from vendor_sources import CONFIGS, ROOT


def plan(feeds):
    result = []
    for key, cfg in CONFIGS.items():
        old = 'http://127.0.0.1:5678/webhook/' + cfg['legacy_path']
        new = 'http://127.0.0.1:8092/internal/vendor-feeds/' + key + '.xml'
        matches = [f for f in feeds if f.get('feed_url') in (old, new)]
        if len(matches) != 1:
            raise ValueError('expected exactly one subscription: ' + key)
        f = matches[0]
        result.append({'key': key, 'id': f['id'], 'old_url': old, 'new_url': new,
                       'current_url': f['feed_url'], 'fetch_via_proxy': f.get('fetch_via_proxy', False)})
    return result


def rewrite_catalog(rows, changes, *, rollback=False):
    rows = copy.deepcopy(rows)
    replacements = {(x['new_url'] if rollback else x['old_url']): (x['old_url'] if rollback else x['new_url']) for x in changes}
    for row in rows:
        if row.get('url') in replacements:
            row['url'] = replacements[row['url']]
    return rows


def atomic(path, value):
    path = Path(path)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + '-')
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def get_json(client, url):
    r = client.get(url); r.raise_for_status(); return r.json()


def switch(client, base, changes, *, rollback=False):
    for row in changes:
        current = get_json(client, base + '/v1/feeds/' + str(row['id']))
        if current['feed_url'] not in (row['old_url'], row['new_url']):
            raise ValueError('feed changed concurrently; refusing overwrite')
        url = row['old_url'] if rollback else row['new_url']
        body = {'feed_url': url, 'fetch_via_proxy': row['fetch_via_proxy'] if rollback else False}
        response = client.put(base + '/v1/feeds/' + str(row['id']), json=body)
        response.raise_for_status()
        actual = get_json(client, base + '/v1/feeds/' + str(row['id']))
        if actual['id'] != row['id'] or actual['feed_url'] != url:
            raise ValueError('feed update verification failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    if args.apply and args.rollback: parser.error('apply and rollback are exclusive')
    from initialize_secrets import read_env
    from worker import MF
    headers = {'X-Auth-Token': read_env('ai.env')['MINIFLUX_API_KEY']}
    os.umask(0o077)
    with httpx.Client(headers=headers, trust_env=False, timeout=45) as client:
        if args.rollback:
            manifest = json.loads(args.rollback.read_text())
            switch(client, MF, manifest['changes'], rollback=True)
            catalog = ROOT / 'sources.catalog.json'
            atomic(catalog, rewrite_catalog(json.loads(catalog.read_text()), manifest['changes'], rollback=True))
            print(json.dumps({'state': 'rolled_back', 'feeds': len(manifest['changes'])}))
            return 0
        feeds = get_json(client, MF + '/v1/feeds')
        changes = plan(feeds)
        if not args.apply:
            print(json.dumps({'state': 'dry_run', 'changes': changes}, ensure_ascii=False))
            return 0
        if args.backup is None or not args.backup.resolve().is_relative_to((ROOT / '.private').resolve()):
            parser.error('--backup must be inside AI_NEWS_ROOT/.private')
        args.backup.mkdir(parents=True, exist_ok=False, mode=0o700)
        selected = [f for f in feeds if f['id'] in {x['id'] for x in changes}]
        manifest = {'state': 'prepared', 'changes': changes, 'feeds': selected}
        atomic(args.backup / 'manifest.json', manifest)
        atomic(args.backup / 'sources.catalog.before.json', json.loads((ROOT / 'sources.catalog.json').read_text()))
        # Probe with a separate header-free local client; never forward reader secrets.
        with httpx.Client(trust_env=False, timeout=20) as local:
            for row in changes:
                response = local.get(row['new_url']); response.raise_for_status()
                if not ET.fromstring(response.content).findall('./channel/item'):
                    raise ValueError('new snapshot is empty: ' + row['key'])
        manifest['state'] = 'switching'; atomic(args.backup / 'manifest.json', manifest)
        try:
            switch(client, MF, changes)
            catalog = ROOT / 'sources.catalog.json'
            atomic(catalog, rewrite_catalog(json.loads(catalog.read_text()), changes))
        except Exception:
            # Reconcile all planned rows, including an ambiguous timed-out PUT.
            try:
                switch(client, MF, changes, rollback=True)
                manifest['state'] = 'rolled_back_after_error'
            except Exception:
                manifest['state'] = 'rollback_required'
            atomic(args.backup / 'manifest.json', manifest)
            raise
        manifest['state'] = 'cutover_complete'; atomic(args.backup / 'manifest.json', manifest)
        print(json.dumps({'state': manifest['state'], 'feeds': len(changes), 'ids': [x['id'] for x in changes]}))
        return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({'state': 'migration_failed', 'error_type': type(exc).__name__}))
        raise SystemExit(1)
