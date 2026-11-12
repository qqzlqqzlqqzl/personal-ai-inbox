"""Read-only evidence report. Approval or a function call is not a repair."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import production_cases as cases


def delivered_image(status, body):
    return cases.delivered_image(status, body)


def report():
    from ops_review import ops_read, validate_operations_result
    private = cases.HOME.parent
    settings = ops_read(private/'qwen-settings.json')
    root = Path(settings['state_root'])
    marker = ops_read(cases.HOME/'active.json')
    rows = []
    with sqlite3.connect((root/'batches.sqlite3').as_uri()+'?mode=ro', uri=True, timeout=3) as db:
        for bid, expected in db.execute('SELECT id,manifest_hash FROM batches ORDER BY updated DESC LIMIT 24'):
            folder = root/bid
            manifest = ops_read(folder/'manifest.json', 32*1024*1024)
            snapshot = manifest.get('operations_review', {})
            if snapshot.get('protocol_revision') != 4 or not any(i['target'].startswith('case_') for i in snapshot.get('incidents', [])):
                continue
            from batch_control import digest
            if manifest['manifest_hash'] != expected or digest({k:v for k,v in manifest.items() if k not in {'batch_id','manifest_hash'}}) != expected:
                raise ValueError('manifest_changed')
            output = folder/'output/operations-review.json'
            if not output.exists():
                return {'at': int(time.time()), 'state': 'model_pending', 'batch_id': bid,
                        'selected_cases': len(marker['cases']), 'incident_count': len(snapshot['incidents'])}
            result = ops_read(output, 128*1024)
            if not (folder/'operations-execution.json').exists():
                return {'at': int(time.time()), 'state': 'model_complete_waiting_executor', 'batch_id': bid,
                        'selected_cases': len(marker['cases']), 'attempted_cases': len(result.get('attempts', []))}
            # Verify freshness at the actual execution receipt time, so this audit
            # can still read historical evidence after the action TTL has elapsed.
            execution = ops_read(folder/'operations-execution.json')
            validate_operations_result(manifest, result, execution['at'])
            rows.append((bid, manifest, result, execution))
    if not rows:
        return {'at': int(time.time()), 'state': 'not_dispatched', 'selected_cases': len(marker['cases'])}
    bid, manifest, result, execution = rows[0]
    os.environ['AI_NEWS_ROOT'] = str(cases.SOURCE.parent)
    os.environ['AI_NEWS_OUTBOUND_PROXY'] = 'http://127.0.0.1:17890'
    sys.path.insert(0, str(cases.SOURCE))
    import core, content_input
    attempts = {a['incident_id'].split(':')[-1]: a for a in result['attempts']}
    verified = []
    async def check(ident):
        target = 'case_'+ident
        spec = json.loads(cases.read(cases.HOME/(target+'.json')))
        attempt = attempts.get(target, {})
        path = cases.HOME/(target+'.outcome.json')
        outcome = ops_read(path) if path.exists() else {}
        value = {'id': int(ident), 'feed_id': spec['feed_id'], 'root_cause': spec['kind'],
                 'proposal_state': attempt.get('state', 'missing'),
                 'proposal': attempt.get('plan'), 'review': attempt.get('review'),
                 'planner': attempt.get('planner'), 'reviewer': attempt.get('reviewer'),
                 'execution': outcome, 'currently_recovered': False}
        try:
            if int(ident) == 58:
                import httpx
                from initialize_secrets import read_env
                async with httpx.AsyncClient(timeout=12, trust_env=False) as client:
                    response = await client.get('http://127.0.0.1:8091/mf/v1/feeds/58', headers={
                        'X-Auth-Token': read_env('ai.env')['MINIFLUX_API_KEY']})
                    feed = response.raise_for_status().json()
                value['current_error_count'] = feed['parsing_error_count']
                value['currently_recovered'] = feed['parsing_error_count'] == 0
            else:
                with core.connect() as db:
                    row = db.execute('SELECT url,title,feed_id,cover_url,preview_error FROM analyses WHERE entry_id=?', (int(ident),)).fetchone()
                if row and row['feed_id'] == spec['feed_id'] and cases.sha(row['url'].encode()) == spec['url_sha256']:
                    cover, source = await content_input.discover_original_cover(row['url'], row['title'], strict=True)
                    value['currently_recovered'] = (bool(cover) and cover == row['cover_url'] and row['preview_error'] is None
                        and cases.sha(cover.encode()) == outcome.get('cover_sha256', cases.sha(cover.encode())))
                    if cover:
                        import httpx
                        from reader_cover_proxy import media_proxy_key
                        from stabilize_media import signed_url
                        url = 'http://127.0.0.1:8092'+signed_url(cover, media_proxy_key())+'?reader_width=960'
                        async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
                            async with client.stream('GET', url, headers={'Accept': 'image/webp,image/*'}) as response:
                                body = bytearray()
                                async for chunk in response.aiter_bytes():
                                    if len(body)+len(chunk) > 8*1024*1024:
                                        break
                                    body.extend(chunk)
                                delivery = {'status': response.status_code, 'mime': response.headers.get('content-type', ''),
                                            'valid_image': delivered_image(response.status_code, bytes(body))}
                        value['delivery'] = delivery
                        value['currently_recovered'] = value['currently_recovered'] and delivery['valid_image']
        except Exception as exc:
            value['verification_error_type'] = type(exc).__name__
        value['counted_repair'] = (value['currently_recovered'] and outcome.get('verified') is True and attempt.get('state') == 'approved'
            and outcome.get('state') in {'repaired', 'already_recovered'}
            and attempt.get('plan', {}).get('action') == 'repair_production_case')
        verified.append(value)
    async def all_cases():
        sem = asyncio.Semaphore(3)
        async def limited(ident):
            async with sem:
                await asyncio.wait_for(check(ident), 35)
        await asyncio.gather(*(limited(i) for i in marker['cases']))
    asyncio.run(all_cases())
    success = sum(v['counted_repair'] for v in verified)
    return {'at': int(time.time()), 'state': 'measured', 'batch_id': bid,
            'model': manifest['model']['filename'], 'total_real_cases': len(marker['cases']),
            'attempted_cases': len(result['attempts']),
            'planned_cases': sum(bool(a.get('planner')) for a in result['attempts']),
            'reviewed_cases': sum(bool(a.get('reviewer')) for a in result['attempts']),
            'approved_cases': sum(a['state']=='approved' for a in result['attempts']),
            'repair_approved_cases': sum(a['state']=='approved' and a.get('plan', {}).get('action')=='repair_production_case' for a in result['attempts']),
            'repair_execution_count': sum(o.get('action')=='repair_production_case' for o in execution['outcomes']),
            'pending_execution': len(execution.get('pending_decisions', [])), 'verified_repairs': success,
            'success_rate': success/len(marker['cases']), 'canary_excluded': True,
            'cases': sorted(verified, key=lambda v:v['id'])}


if __name__ == '__main__':
    sys.path.insert(1, '/home/ubuntu/website-release-20261004/reader-3349cf2-source/src/kaggle_batch')
    print(json.dumps(report(), ensure_ascii=False, allow_nan=False))
