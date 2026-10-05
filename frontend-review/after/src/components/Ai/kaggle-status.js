// Presentation only: enabling dispatch, quota admission and remote execution are
// separate facts. A local observer/service being active is not a running GPU job.
const knownIdle = new Set(['empty', 'completed', 'cycle_window_complete', 'nothing_to_reconcile', 'no_progress'])
const waitingStates = new Set(['preparing', 'prepared_requires_worker', 'waiting_for_retry', 'retry_after_cycle_window', 'quota_reserved', 'quota_unknown'])
const recoveryStates = new Set(['resuming', 'reconciling', 'local_retry_scheduled', 'batch_deferred', 'retired_missing_remote', 'observation_timeout'])
const receiptErrors = new Set(['submit_receipt_invalid', 'submit_receipt_write_failed', 'submit_cas_conflict'])
const serviceActive = lane => ['active', 'activating', 'deactivating', 'reloading'].includes(lane?.service?.ActiveState)
const number = value => !['number', 'string'].includes(typeof value) || String(value).trim() === ''
  ? null : (Number.isFinite(Number(value)) ? Number(value) : null)
const reasonText = code => ({inaccessible: '远端状态暂不可读', provider_unavailable: '远端调用暂不可用', quota_exhausted: '额度不足', quota_unknown: '额度未确认'})[code] || '等待核对远端状态'

export function kaggleLaneStatus(lane, now = Date.now() / 1000, schedulerLane = null) {
  const retryAt = Math.max(number(lane?.recovery?.retry_at) || 0, number(schedulerLane?.retry_at) || 0)
  const cooling = retryAt > now || lane?.state === 'cooldown'
  const retryDetail = retryAt > now
    ? `下次检查 ${new Date(retryAt * 1000).toLocaleString([], {month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'})}`
    : '等待调度重新检查'
  const reason = reasonText(lane?.recovery?.code || lane?.outstanding?.error)
  const batch = lane?.outstanding
  const state = batch?.state
  const remote = typeof batch?.remote_status === 'string' ? batch.remote_status.toUpperCase() : null
  const isolated = lane?.quarantine?.batches
  const quarantineCountKnown = Number.isSafeInteger(isolated) && isolated >= 0
  const quarantined = quarantineCountKnown ? isolated : state === 'quarantined' ? 1 : 0
  const quarantineDetail = quarantineCountKnown ? `${quarantined} 批旧任务已隔离，等待核实` : '旧任务已隔离，等待核实'
  const result = (kind, text, detail, tone = 'warn') => ({kind, text,
    detail: quarantined && kind !== 'quarantined' ? `${detail} · ${quarantineDetail}` : detail,
    tone, cooling, retryAt, quarantined, quarantineCountKnown})
  if (state === 'quarantined') {
    return result('quarantined', '已隔离 · 等待核实', '提交结果未确认；原任务身份与占用记录继续保留')
  }
  if (lane?.ledger_state === 'unknown') return result('unknown', '状态未确认', '任务台账暂不可读取，等待核实')
  if (lane?.recovery_required === true || (typeof lane?.recovery_error === 'string' && lane.recovery_error.length > 0)
      || receiptErrors.has(batch?.error)) {
    return result('unknown', '提交结果未确认', '任务或提交回执需要核实，保留原身份与占用记录')
  }
  if (['submit_unknown', 'submitting'].includes(state)) {
    return result('submission_unknown', state === 'submitting' ? '提交中 · 尚未确认' : '提交结果未确认',
      cooling ? `冷却 · ${reason} · ${retryDetail}` : '先核对已有提交结果，尚未确认运行')
  }
  if (cooling) return result('cooldown', '冷却', `${state === 'running' && remote === 'RUNNING' ? '上次确认运行，当前状态待核对 · ' : ''}${reason} · ${retryDetail}`)
  if (remote === 'UNKNOWN') return result('unknown', '提交结果未确认', '远端状态未确认，等待核实')
  if (state === 'running' && remote === 'RUNNING') {
    return result('running', '运行中', '已有批次正在运行，完成后再导入结果', 'ok')
  }
  if (state === 'running' && !remote) return result('unknown', '状态未确认', '已有批次标记为运行，但缺少远端状态确认')
  if (state === 'submitted') return result('waiting', '已提交 · 等待运行', '已有批次尚未确认开始运行')
  if (state === 'prepared') return result('waiting', '等待调度', '批次已准备，尚未提交')
  if (state === 'downloaded' || ['COMPLETE', 'COMPLETED'].includes(remote)) {
    return result('recovery', '等待导入', '远端批次已结束，结果尚未完成导入')
  }
  if (state === 'terminal') return result('recovery', '批次已结束 · 待核对', '检查结果或失败原因，尚未完成恢复')
  if (state) return result('recovery', '待恢复', '已有批次需要核对或恢复，尚未确认运行')
  if (quarantined) return result('quarantined', '已隔离 · 等待核实', `${quarantineDetail}；新文章须单独通过当前调度条件`)
  if (recoveryStates.has(lane?.state)) return result('recovery', '待恢复', '等待核对或导入已有批次')
  if (waitingStates.has(lane?.state)) return result('waiting', '等待调度', '尚未确认有批次运行')
  if (lane?.state === 'not_started') return result('waiting', '尚未启动', '尚未确认有批次运行')
  if (knownIdle.has(lane?.state) && !serviceActive(lane)) return result('idle', '空闲', '当前没有待处理批次', '')
  return result('unknown', '状态未确认', serviceActive(lane) ? '本地检查服务已启动，远端批次状态未确认' : '尚未取得明确的批次状态')
}

export function kaggleStatus(kaggle, now = Date.now() / 1000) {
  const lanes = Object.entries(kaggle?.lanes || {}).map(([key, lane]) => kaggleLaneStatus(lane, now, kaggle?.scheduler?.lanes?.[key]))
  const running = lanes.filter(lane => lane.kind === 'running').length
  const cooldown = lanes.filter(lane => lane.cooling).length
  const submissionUnknown = lanes.filter(lane => lane.kind === 'submission_unknown').length
  const unknown = lanes.filter(lane => lane.kind === 'unknown').length
  const waiting = lanes.filter(lane => ['waiting', 'recovery'].includes(lane.kind)).length
  const quarantined = lanes.reduce((sum, lane) => sum + lane.quarantined, 0)
  const quarantineCountKnown = lanes.filter(lane => lane.quarantined).every(lane => lane.quarantineCountKnown)
  const enabled = kaggle?.enabled
  const parts = [enabled === true ? '已启用' : enabled === false ? '调度已暂停' : '启用状态未确认']
  if (!lanes.length) parts.push('批次状态未确认')
  else {
    parts.push(`已确认运行 ${running} 批`)
    if (cooldown) parts.push(`${cooldown} 条冷却`)
    if (submissionUnknown) parts.push(`${submissionUnknown} 批提交未确认`)
    if (unknown) parts.push(`${unknown} 条状态未确认`)
    if (waiting) parts.push(`${waiting} 条等待`)
    if (quarantined) parts.push(quarantineCountKnown ? `${quarantined} 批已隔离待核实` : '旧任务已隔离待核实')
    if (!running && !cooldown && !submissionUnknown && !unknown && !waiting && !quarantined && enabled === true) parts.push('等待调度')
  }
  return {text: `Kaggle ${parts.join(' · ')}`, running, cooldown, submissionUnknown, unknown, waiting, quarantined,
    tone: cooldown || submissionUnknown || unknown || quarantined || enabled !== true ? 'warn' : running ? 'ok' : ''}
}

export function kaggleQuotaText(lane, enabled, current = kaggleLaneStatus(lane)) {
  const remaining = number(lane?.quota?.gpu?.remaining_hours)
  const gate = lane?.quota_gate
  // A historical numeric remainder never outranks an unknown identity/quota
  // gate or a stale observation. Display is not new-work admission.
  if (remaining === null || lane?.quota?.state !== 'ok' || lane?.quota?.stale
      || !['available', 'quota_reserved'].includes(gate?.state)
      || (gate?.allowed !== true && gate?.state !== 'quota_reserved')) {
    return '额度未知或过期 · 停用新批次，保留已有任务等待核实'
  }
  if (lane?.quota_gate?.state === 'quota_reserved' || (remaining !== null && remaining <= 1)) {
    return '额度保护 / ≤1h 停用新批次 · 保留已有任务，等待核实'
  }
  if (enabled === false) return '额度条件满足 · 调度已暂停'
  if (current.kind === 'submission_unknown') return '额度条件满足 · 先核对已有提交结果'
  if (current.cooling) return '额度条件满足 · 冷却后仍需恢复调度'
  if (current.kind === 'quarantined') return '额度条件满足 · 旧任务隔离保留；新文章仍需身份与调度确认'
  if (current.kind === 'unknown' || enabled !== true) return '额度条件满足 · 调度状态未确认'
  if (['running', 'recovery'].includes(current.kind) || lane?.outstanding?.state) return '额度条件满足 · 先处理已有批次'
  return '额度条件满足 · 新批次仍需身份与调度确认'
}
