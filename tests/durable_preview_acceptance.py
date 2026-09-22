"""Production refresh regression; restore the one controlled content mutation."""
import json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import core
from ops_common import client

IDS=[2900,2899,2898,2897,2896,2881,2880,2871]
report={'at':time.time(),'checks':[]}
def check(name,ok,detail=None):
    report['checks'].append({'name':name,'passed':bool(ok),'detail':detail})
    if not ok: raise AssertionError(name)
def copies():
    with core.connect() as db:
        return [tuple(r) for r in db.execute('SELECT entry_id,source_hash,title_zh,translated_at FROM card_translations WHERE entry_id IN ('+','.join('?' for _ in IDS)+') ORDER BY entry_id',IDS)]
def calls():
    with core.connect() as db:
        return db.execute('SELECT COUNT(*) FROM usage').fetchone()[0]

with client() as c:
    before=c.get('/v1/entries/2900').json()
    before_copies=copies();before_calls=calls()
    try:
        c.put('/v1/entries/2900',json={'content':'<p>Clipboard that adapts to wherever you paste it</p>'}).raise_for_status()
        shown=c.get('/v1/entries/2900').json()
        check('rss_teaser_cannot_erase_prepared_product','产品介绍（Product Hunt）' in shown['content'])
        check('rss_teaser_cannot_invalidate_chinese_cache',before_copies==copies())
    finally:
        c.put('/v1/entries/2900',json={'content':before['content']}).raise_for_status()
        report['controlled_mutation_restored']=True
    feed=c.get('/v1/feeds/40').json()
    old_check=feed.get('checked_at')
    refresh=c.put('/v1/feeds/40/refresh')
    check('product_feed_refresh_requested',refresh.status_code==204,refresh.status_code)
    for _ in range(30):
        feed=c.get('/v1/feeds/40').json()
        if feed.get('checked_at') != old_check:
            break
        time.sleep(1)
    check('product_feed_refreshed',feed.get('checked_at')!=old_check,feed.get('checked_at'))
    check('product_feed_refresh_not_failed',not feed.get('parsing_error_count'),feed.get('parsing_error_count'))
    for eid in IDS:
        shown=c.get('/v1/entries/'+str(eid)).json()
        check('after_refresh_product_'+str(eid), '产品介绍（Product Hunt）' in shown['content'] and shown.get('card',{}).get('language')=='zh-CN')
    check('refresh_keeps_cached_translation_versions',copies()==before_copies)
    check('refresh_does_not_call_model',calls()==before_calls)
report['passed']=all(x['passed'] for x in report['checks'])
(core.ROOT/'artifacts/durable-preview-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
sys.exit(0 if report['passed'] else 1)
