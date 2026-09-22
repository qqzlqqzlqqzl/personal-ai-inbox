"""Bounded maintenance entry point: seed/translate cards, never re-run value scoring."""
import argparse
import asyncio
import json
from pathlib import Path
import core
import card_translation as cards
from ops_common import client, load_worker_environment

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--feed', type=int)
    parser.add_argument('--limit', type=int, default=200)
    parser.add_argument('--ids', type=int, nargs='*')
    parser.add_argument('--batches', type=int, default=1)
    parser.add_argument('--report')
    args = parser.parse_args()
    core.init_db(); core.init_usage(); core.migrate(); load_worker_environment()
    with client() as c:
        if args.ids:
            entries = [c.get('/v1/entries/'+str(e)).json() for e in args.ids[:100]]
        else:
            params = {'limit':max(1,min(args.limit,1000)), 'order':'published_at','direction':'desc'}
            if args.feed:
                params['feed_id'] = args.feed
            r = c.get('/v1/entries',params=params);r.raise_for_status();entries = r.json()['entries']
    cards.enqueue(entries,priority=50)
    reports = []
    for _ in range(max(0,min(args.batches,60))):
        result = await cards.run_once()
        reports.append(result)
        if result.get('budget_paused') or result.get('waiting_model') or result.get('busy') or (not result.get('processed') and not result.get('failed')):
            break
        await asyncio.sleep(1)
    out = {'seeded':len(entries),'batches':reports,'value_analysis_requests':0,
           'status':cards.status(entries[0]['user_id']) if entries else {}}
    if args.report:
        (core.ROOT/args.report).write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(json.dumps(out,ensure_ascii=False))

if __name__ == '__main__':
    asyncio.run(main())
