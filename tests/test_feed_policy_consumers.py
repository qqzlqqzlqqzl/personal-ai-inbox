"""Real local SQLite consumer boundaries; no Controller/provider or live config."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import core, worker, card_translation, processing_status
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/kaggle_batch'))
import qwen_exceptions as qe
from qwen_exceptions import prepare
import lane_scheduler

SUMMARY = 'rss_summary_only:kicktraq-rss-preview-only-v1'
UNVERIFIED = 'rss_feed_identity_unverified:kicktraq-rss-preview-only-v1'


def row():
    with core.connect() as db:
        return dict(db.execute('SELECT * FROM analyses WHERE entry_id=1').fetchone())


# Original independent reviewer assertion, retained verbatim.
@pytest.mark.asyncio
async def test_restricted_summary_cannot_reenter_exception_model_lane(db,entry,monkeypatch):
    entry['feed']['feed_url']='https://www.kicktraq.com/categories/technology/latest.rss'
    core.discover([entry])
    with core.connect() as c:row=dict(c.execute('SELECT * FROM analyses WHERE entry_id=1').fetchone())
    monkeypatch.setattr(worker,'mf_get',AsyncMock(return_value=entry))
    await worker.process_one(None,row,core.settings())
    with core.connect() as c:
        current=dict(c.execute('SELECT * FROM analyses WHERE entry_id=1').fetchone())
    assert current['state']=='requires_fulltext_adapter'
    generated=prepare(core.DB,[1],set(),limit=8)
    print({'state':current['state'],'exception_items':len(generated),'kinds':[x['kind'] for x in generated],'max_tokens':[x['max_tokens'] for x in generated]})
    assert generated==[], 'RSS restriction must survive the later qwen_exception_review consumer'


@pytest.mark.parametrize('marker', [SUMMARY, UNVERIFIED])
@pytest.mark.parametrize('state', ['requires_fulltext_adapter', 'insufficient_content', 'fetch_error'])
def test_every_exception_state_rejects_fixed_policy_marker(db, entry, marker, state):
    core.discover([entry]);core.update(1,state=state,error=marker,attempts=3)
    before=row()
    assert qe.prepare(core.DB,[1],set(),limit=8)==[]
    assert qe.retry_allowed(before) is False
    assert row()==before


@pytest.mark.parametrize('error', [None,'original_http_503','unrelated mentions rss_summary_only',
                                 'rss_summary_only:kicktraq-rss-preview-only-v1x'])
def test_other_source_exceptions_are_not_broadly_suppressed(db, entry, error):
    core.discover([entry]);core.update(1,state='requires_fulltext_adapter',error=error)
    assert len(qe.prepare(core.DB,[1],set(),limit=8))==1


@pytest.mark.parametrize('has_reviews', [False,True])
@pytest.mark.parametrize('with_card', [False,True])
def test_scheduler_never_readds_restricted_exception_or_card(db,entry,monkeypatch,has_reviews,with_card):
    core.discover([entry]);card_translation.migrate()
    core.update(1,state='requires_fulltext_adapter',error=SUMMARY)
    with core.connect() as conn:
        conn.execute("UPDATE card_translations SET status=?,attempts=0,next_try=0 WHERE entry_id=1",('pending' if with_card else 'done',))
    if has_reviews: qe.connect(core.DB).close()
    monkeypatch.setattr(lane_scheduler,'required_roots',lambda config: [])
    monkeypatch.setattr(lane_scheduler,'claimed_entries',lambda roots: set())
    monkeypatch.setattr(lane_scheduler,'resolve_entry_ids',lambda config,**kwargs: [1])
    cfg={'database':str(core.DB),'qwen_exception_review':True}
    assert lane_scheduler.due_entries(1000,cfg)==(set(),set())
    # Same source-independent state remains eligible when it has no restriction.
    core.update(1,error='ordinary missing adapter')
    assert lane_scheduler.due_entries(1000,cfg)==({1},set())


def test_old_prepared_exception_cannot_apply_against_restricted_row(db,entry):
    core.discover([entry]);core.update(1,state='requires_fulltext_adapter',error=SUMMARY)
    with patch.object(qe,'restricted_analysis_reason',lambda value: None):
        items=qe.prepare(core.DB,[1],set())  # Simulates the previous permissive consumer.
    item=items[0]
    manifest={'batch_id':'synthetic','manifest_hash':'synthetic-hash','model':{},'items':items}
    output=[{'id':item['id'],'batch_id':manifest['batch_id'],'manifest_hash':manifest['manifest_hash'],
             'input_hash':item['input_hash'],'status':'ok',
             'content':json.dumps({'action':'keep_blocked','confidence':1,'reason':'synthetic'})}]
    before=row()
    result=qe.apply(core.DB,manifest,output,SimpleNamespace(append=lambda *a,**kw: None))
    assert result[0]['action']=='source_changed_no_action'
    assert row()==before
    with core.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM qwen_exception_reviews').fetchone()[0]==0


@pytest.mark.parametrize('marker,state,reason', [(SUMMARY,'requires_fulltext_adapter','rss_summary_only'),
                                                (UNVERIFIED,'requires_source_review','rss_feed_identity_unverified')])
def test_processing_exposes_fixed_reason_and_keeps_unknown_batch(marker,state,reason):
    evidence={'local_observed_at':100,'submission_unknown_count':5,'ledgers_complete':False}
    value=processing_status.for_entry({'entry_id':1,'state':state,'error':marker},evidence,now=100)
    assert value['reason_code']==reason
    assert value['reason_codes']==[reason,'submission_unknown','ledger_unavailable']
    assert '论文' not in value['message']


@pytest.mark.parametrize('state,error,expected', [('requires_fulltext_adapter','ordinary','source_review_required'),
                                                ('done',SUMMARY,'complete'),
                                                ('pending',SUMMARY,'schedule_disabled')])
def test_processing_preserves_other_states_and_stops(state,error,expected):
    evidence={'local_observed_at':100,'configs':{k:{'schedule_enabled':False} for k in processing_status.LANES}}
    value=processing_status.for_entry({'entry_id':1,'state':state,'error':error},evidence,now=100)
    assert value['reason_code']==expected
