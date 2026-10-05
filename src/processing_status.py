"""Read-only local evidence and safe processing reasons; never refresh providers."""
import json
import math
from pathlib import Path
import sqlite3
import time
from feed_consumption import restricted_analysis_reason

LANES = ('primary', 'secondary', 'third', 'fourth', 'fifth')
SUBMIT_RECEIPT_ERRORS = ('submit_receipt_invalid', 'submit_receipt_write_failed', 'submit_cas_conflict')
MESSAGES = {
    'complete': '分析已完成',
    'not_recommended': '已保留原始条目；当前内容资格不进入AI精选',
    'paused': '后台处理已暂停；已有提交状态不明的任务会保留等待核实',
    'schedule_disabled': '后台自动处理已关闭；文章会保留待处理',
    'reconcile_only': '当前仅允许核对已有任务，未开启新文章处理',
    'submission_unknown': '已有批次的提交结果尚未确认，正在保留任务身份等待安全核实',
    'submission_quarantined': '旧任务已隔离，提交结果未确认，等待核实',
    'quota_reserved': '缓存显示剩余额度未超过安全保留线，暂不启动新任务',
    'quota_unknown': '额度状态尚未确认，暂不能保证开始处理',
    'cooldown': '后台处理正在等待重试时间',
    'queued': '文章已进入待处理队列',
    'processing': '最近后台快照显示任务正在处理',
    'awaiting_import': '已缓存结果等待校验与入库',
    'extraction_failed': '正文提取失败，等待重试；这不代表文章价值低',
    'source_review_required': '正文来源或完整性需要核实，暂不生成可靠评价',
    'rss_summary_only': '仅有RSS摘要，项目全文分析尚未接入，未生成价值评分',
    'rss_feed_identity_unverified': '订阅源身份待核实，未扩展抓取或分析',
    'unknown': '后台状态尚未确认，请稍后查看状态更新时间',
}


def _time(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def project(*, entry_state, evidence, now, max_snapshot_age=300):
    """evidence is a caller-built allowlisted local observation, never a provider.

    A global unknown batch is described as global. Without an entry-claim match
    this function does not claim that the particular article was submitted.
    """
    now = _time(now)
    if now is None:
        raise ValueError('invalid observation time')
    evidence = evidence if isinstance(evidence, dict) else {}
    scheduler = evidence.get('scheduler')
    scheduler = scheduler if isinstance(scheduler, dict) else {}
    scheduler_state = scheduler.get('state') if isinstance(scheduler.get('state'), str) else None
    observed_at = _time(scheduler.get('at'))
    fresh = observed_at is not None and 0 <= now-observed_at <= max_snapshot_age
    local_at = _time(evidence.get('local_observed_at'))
    local_fresh = local_at is not None and 0 <= now-local_at <= max_snapshot_age
    configs = evidence.get('configs')
    configs = configs if isinstance(configs, dict) else {}
    complete_configs = all(isinstance(configs.get(key), dict) for key in LANES)
    automatic = [key for key in LANES if isinstance(configs.get(key), dict)
                 and configs[key].get('schedule_enabled') is True
                 and (configs[key].get('reconcile_only') is False or configs[key].get('reconcile_only') is None)]
    unknown_count = evidence.get('submission_unknown_count')
    unknown_count = unknown_count if type(unknown_count) is int and 0 <= unknown_count <= 10000 else 0
    reasons = []
    reason = 'unknown'
    next_retry = None
    quarantined = local_fresh and evidence.get('entry_claim_quarantined') is True
    unconfirmed = local_fresh and (evidence.get('entry_claim_submission_unknown') is True
                                  or evidence.get('entry_claim_receipt_conflict') is True)
    claim_held = entry_state != 'done' and (quarantined or unconfirmed)
    ledger_unknown = local_fresh and evidence.get('ledgers_complete') is False
    if entry_state == 'done':
        reason = 'complete'
        observed_at = _time(evidence.get('analyzed_at'))
        fresh = observed_at is not None
    elif entry_state == 'content_excluded' and not claim_held:
        reason = 'not_recommended'
        observed_at = _time(evidence.get('analyzed_at')) or local_at
        fresh = observed_at is not None
    elif entry_state in ('fetch_error', 'ai_error') and not claim_held and not ledger_unknown:
        reason = 'extraction_failed' if entry_state == 'fetch_error' else 'source_review_required'
    elif entry_state in ('requires_fulltext_adapter', 'requires_source_review', 'requires_model_review', 'insufficient_content') and not claim_held:
        reason = 'source_review_required'
    elif local_fresh and evidence.get('pause_exists') is True:
        reason, observed_at, fresh = 'paused', local_at, True
    elif local_fresh and complete_configs and all(configs[key].get('schedule_enabled') is False for key in LANES):
        reason, observed_at, fresh = 'schedule_disabled', local_at, True
    elif local_fresh and complete_configs and not automatic and any(
            configs[key].get('schedule_enabled') is True and configs[key].get('reconcile_only') is True for key in LANES):
        reason, observed_at, fresh = 'reconcile_only', local_at, True
    elif quarantined:
        reason, observed_at, fresh = 'submission_quarantined', local_at, True
    elif unconfirmed:
        reason, observed_at, fresh = 'submission_unknown', local_at, True
    elif ledger_unknown:
        reason, observed_at, fresh = 'unknown', local_at, False
    elif fresh:
        reason = {
            'schedule_disabled': 'schedule_disabled',
            'paused': 'paused',
            'waiting_for_reconciliation': 'submission_unknown',
            'waiting_for_cached_output_import': 'awaiting_import',
            'waiting_for_lane_recovery': 'cooldown',
            'waiting_for_item_retry': 'cooldown',
            'processing': 'processing',
            'started': 'processing',
            'idle': 'queued',
            'blocked_source_or_review': 'source_review_required',
        }.get(scheduler_state, 'unknown')
        if local_fresh and automatic and reason in {'schedule_disabled', 'paused'}:
            # Freshly read control flags supersede a contradictory cached cycle.
            reason, fresh = 'unknown', False
            reasons.append('state_evidence_conflict')
        if scheduler_state == 'no_healthy_lane' and local_fresh and automatic:
            lanes = scheduler.get('lanes')
            lanes = lanes if isinstance(lanes, dict) else {}
            gates = [(lanes.get(key) or {}).get('quota_gate') for key in automatic if isinstance(lanes.get(key), dict)]
            if len(gates) == len(automatic) and all(isinstance(gate, dict) and gate.get('allowed') is False for gate in gates):
                gate_states = {gate.get('state') for gate in gates if isinstance(gate.get('state'), str)}
                if gate_states == {'quota_reserved'}:
                    reason = 'quota_reserved'
                elif gate_states <= {'quota_reserved', 'quota_unknown'} and 'quota_unknown' in gate_states:
                    reason = 'quota_unknown'
    reasons.insert(0, reason)
    if claim_held and quarantined and reason != 'submission_quarantined':
        reasons.append('submission_quarantined')
    elif claim_held and unconfirmed and reason != 'submission_unknown':
        reasons.append('submission_unknown')
    if entry_state != 'done' and local_fresh and unknown_count and reason != 'submission_unknown':
        if 'submission_unknown' not in reasons:reasons.append('submission_unknown')
    retry = _time(scheduler.get('next_retry_at'))
    if fresh and reason == 'cooldown' and retry is not None and retry > now:
        next_retry = retry
    return {'reason_code': reason, 'reason_codes': reasons, 'message': MESSAGES[reason],
            'observed_at': observed_at, 'next_retry_at': next_retry, 'stale': not fresh,
            'claim_held': bool(claim_held)}


def _json(path):
    try:
        if path.stat().st_size > 262144:
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None


def observe(root, entry_ids=(), *, now=None, control_root=None):
    """Only files and existing mode=ro ledgers; no product imports or migrations.

    One bounded snapshot is shared across a page. Never instantiate Controller,
    call month_control.status, query provider quota, or create a missing ledger.
    control_root selects the live dispatch switches; state and claims stay in root.
    """
    root = Path(root)
    control_root = root if control_root is None else Path(control_root)
    now = time.time() if now is None else now
    ids = sorted({eid for eid in entry_ids if type(eid) is int and eid > 0})[:100]
    pause = control_root/'runtime/qwen-month-20260925/paused.json'
    try:
        pause.stat()
        paused = True
    except FileNotFoundError:
        paused = False
    except OSError:
        paused = None
    configs = {}
    for key in LANES:
        config = _json(control_root/f'src/kaggle_batch/cloud-config-month-{key}.json')
        if config is not None:
            configs[key] = {name: config.get(name) for name in ('schedule_enabled', 'reconcile_only')}
    raw = _json(root/'state/kaggle-month-dispatch/scheduler.json') or {}
    scheduler = {key: raw.get(key) for key in ('state', 'at', 'next_retry_at')}
    raw_lanes = raw.get('lanes')
    raw_lanes = raw_lanes if isinstance(raw_lanes, dict) else {}
    scheduler['lanes'] = {}
    for key in LANES:
        lane = raw_lanes.get(key)
        gate = lane.get('quota_gate') if isinstance(lane, dict) else None
        if isinstance(gate, dict):
            scheduler['lanes'][key] = {'quota_gate': {name: gate.get(name) for name in ('allowed', 'state')}}
    count = 0
    claimed = set()
    quarantined_count = 0
    quarantined_claim_count = 0
    quarantined_claimed = set()
    receipt_conflict_claimed = set()
    complete = True
    for key in LANES:
        database = root/f'state/kaggle-month-{key}/batches.sqlite3'
        connection = None
        try:
            connection = sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True, timeout=1)
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            count += connection.execute("SELECT COUNT(*) FROM batches WHERE state='submit_unknown'").fetchone()[0]
            quarantined_count += connection.execute("SELECT COUNT(*) FROM batches WHERE state='quarantined'").fetchone()[0]
            quarantined_claim_count += connection.execute("SELECT COUNT(*) FROM batch_claims c JOIN batches b ON b.id=c.batch_id WHERE b.state='quarantined'").fetchone()[0]
            if ids:
                rows = connection.execute(
                    "SELECT DISTINCT c.entry_id FROM batch_claims c JOIN batches b ON b.id=c.batch_id "
                    "WHERE b.state='submit_unknown' AND c.entry_id IN ("+','.join('?' for _ in ids)+')', ids)
                claimed.update(row[0] for row in rows)
                rows = connection.execute(
                    "SELECT DISTINCT c.entry_id FROM batch_claims c JOIN batches b ON b.id=c.batch_id "
                    "WHERE b.state='quarantined' AND c.entry_id IN ("+','.join('?' for _ in ids)+')', ids)
                quarantined_claimed.update(row[0] for row in rows)
                rows = connection.execute(
                    "SELECT DISTINCT c.entry_id FROM batch_claims c JOIN batches b ON b.id=c.batch_id "
                    "WHERE b.state NOT IN ('imported','retired','resolved') AND b.error IN (?,?,?) "
                    "AND c.entry_id IN ("+','.join('?' for _ in ids)+')', (*SUBMIT_RECEIPT_ERRORS, *ids))
                receipt_conflict_claimed.update(row[0] for row in rows)
        except (OSError, sqlite3.Error):
            complete = False
        finally:
            if connection is not None:
                connection.close()
    return {'local_observed_at': now, 'pause_exists': paused, 'configs': configs,
            'scheduler': scheduler, 'submission_unknown_count': count if complete else None,
            'entry_claim_unknown_ids': claimed, 'ledgers_complete': complete,
            'quarantined_batch_count': quarantined_count if complete else None,
            'quarantined_claim_count': quarantined_claim_count if complete else None,
            'entry_claim_quarantined_ids': quarantined_claimed,
            'entry_claim_receipt_conflict_ids': receipt_conflict_claimed}


def for_entry(row, evidence, *, now=None):
    row = dict(row)
    evidence = evidence if isinstance(evidence, dict) else {}
    result = project(entry_state=row.get('state', 'pending'), now=time.time() if now is None else now,
        evidence={**evidence, 'entry_claim_submission_unknown': row.get('entry_id') in evidence.get('entry_claim_unknown_ids', set()),
                  'entry_claim_quarantined': row.get('entry_id') in evidence.get('entry_claim_quarantined_ids', set()),
                  'entry_claim_receipt_conflict': row.get('entry_id') in evidence.get('entry_claim_receipt_conflict_ids', set()),
                  'analyzed_at': row.get('analyzed_at')})
    policy_reason = restricted_analysis_reason(row.get('error'))
    expected_state = {'rss_summary_only': 'requires_fulltext_adapter',
                      'rss_feed_identity_unverified': 'requires_source_review'}.get(policy_reason)
    if policy_reason and row.get('state') == expected_state and result['reason_code'] == 'source_review_required':
        result['reason_code'] = policy_reason
        result['reason_codes'] = [policy_reason if code == 'source_review_required' else code
                                  for code in result['reason_codes']]
        result['message'] = MESSAGES[policy_reason]
    if row.get('state') != 'done' and evidence.get('ledgers_complete') is False and 'ledger_unavailable' not in result['reason_codes']:
        result['reason_codes'].append('ledger_unavailable')
    return result
