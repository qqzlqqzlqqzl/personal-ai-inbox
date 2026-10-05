import "./AiNews.css"

const labels = {
  content_excluded: "原始条目已保留 · 暂不推荐 · 未评分",
  requires_fulltext_adapter: "需要论文全文适配 · 未评分", insufficient_content: "原文信息过少 · 未评分", removed: "原条目已移除", pending: "等待后台处理", fetching: "正在抓取原文", analyzing: "正在分析",
  waiting_model: "原文已抓取 · 等待模型配置", fetch_error: "原文抓取受限",
  ai_error: "AI 分析失败，可重试", budget_paused: "已达今日预算 · 稍后重试",
}

// Only the fixed cache-only API contract is displayable here. Never infer access,
// prices or eligibility from a score, title, error text or an unknown reason code.
const processingLabels = {
  not_recommended: "原始条目保留，当前不进入 AI 精选",
  rss_summary_only: "来源仅提供 RSS 摘要，等待项目全文适配；不代表项目价值低",
  rss_feed_identity_unverified: "订阅来源身份未确认，后续内容处理已暂停",
  paused: "后台处理已暂停",
  schedule_disabled: "后台自动处理已关闭",
  reconcile_only: "当前仅核对已有任务，未开启新文章处理",
  submission_unknown: "后台仍有提交结果未确认，等待安全核实（不代表本篇已提交）",
  quota_reserved: "缓存额度未超过安全保留线，暂不启动新任务",
  quota_unknown: "额度状态尚未确认",
  cooldown: "后台处理等待重试时间",
  queued: "文章已进入待处理队列",
  processing: "最近后台快照显示任务正在处理",
  awaiting_import: "已缓存结果等待校验与入库",
  extraction_failed: "正文提取失败，等待重试；不代表文章价值低",
  source_review_required: "正文来源或完整性需要核实",
  ledger_unavailable: "任务台账暂不可读取，状态待核实",
  state_evidence_conflict: "当前控制与缓存状态不一致，状态待核实",
  unknown: "后台状态尚未确认",
}

export function qualityLabels(quality) {
  if (!quality) return []
  if (quality.policy_version !== "reader-content-quality-v1") return ["内容资格待核实"]
  const reasons = Array.isArray(quality.reason_codes) ? quality.reason_codes : []
  const result = []
  if (reasons.includes("conflicting_access_evidence")) result.push("访问条件存在冲突，待核实")
  else if (reasons.includes("publisher_nonfree_pending_review")) result.push("发布方标注非免费，访问条件待核实")
  else if (quality.access === "paid_fulltext") result.push("原站全文需付费")
  else if (quality.access === "paid_subscription") result.push("原站需付费订阅")
  else if (quality.access === "login_required") result.push("原站需要登录，访问条件待核实")
  if (quality.information === "low_information") result.push("原文有效信息不足")
  if (quality.recommendation_eligible === false) result.push("暂不推荐")
  else if (quality.recommendation_eligible == null) result.push("内容资格待核实")
  return result
}

function evidenceTime(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null
  const date = new Date(value * 1000)
  return Number.isFinite(date.getTime()) ? date : null
}

function Processing({ processing }) {
  if (!processing || typeof processing !== "object") return null
  const reasons = [...new Set([processing.reason_code, ...(Array.isArray(processing.reason_codes) ? processing.reason_codes : [])])]
    .filter(code => typeof code === "string" && Object.hasOwn(processingLabels, code)).slice(0, 8)
  const observed = evidenceTime(processing.observed_at), retry = evidenceTime(processing.next_retry_at)
  return <div className="ai-processing">
    <p>{(reasons.length ? reasons : ["unknown"]).map(code => processingLabels[code]).join("；")}</p>
    {processing.stale === true && <p>状态信息已过期，仅供参考</p>}
    {observed && <small><time dateTime={observed.toISOString()}>状态记录：{observed.toLocaleString("zh-CN")}</time></small>}
    {reasons.includes("cooldown") && retry && <small><time dateTime={retry.toISOString()}>缓存重试时间：{retry.toLocaleString("zh-CN")}</time></small>}
  </div>
}

function sourcePolicy(ai) {
  if (ai?.state === "requires_fulltext_adapter" && ai.processing?.reason_code === "rss_summary_only")
    return "Kicktraq RSS 摘要 · 项目全文分析未接入 · 未评分"
  if (ai?.state === "requires_source_review" && ai.processing?.reason_code === "rss_feed_identity_unverified")
    return "订阅来源身份待核实 · 未评分"
  return null
}

export default function AiBadge({ entry, detailed = false }) {
  const ai = entry?.ai
  const sourcePolicyLabel = sourcePolicy(ai)
  const note = ai?.has_note ? <span className="ai-note-chip">📝 有笔记</span> : null
  const quality = qualityLabels(ai?.content_quality)
  const qualityNote = quality.length ? <p className="ai-content-quality">{quality.join("；")}</p> : null
  if (!ai || ai.state !== "done") {return <div className="ai-pending" title={sourcePolicyLabel || ai?.error || ""}>{note}{sourcePolicyLabel || (Object.hasOwn(labels, ai?.state || "pending") ? labels[ai?.state || "pending"] : "处理状态待确认")}{qualityNote}<Processing processing={ai?.processing} />{detailed && ai?.error && <p>处理详情：{sourcePolicyLabel || ai.error}</p>}</div>}
  const excluded = ai.content_quality?.policy_version === "reader-content-quality-v1" && ai.content_quality.recommendation_eligible === false
  return <div className={detailed ? "ai-verdict ai-verdict-detail" : "ai-verdict"}>
    <div className="ai-scoreline"><strong>{excluded ? "AI 评分" : "推荐"} {ai.score}/10</strong><span>技术 {ai.technical_score}</span><span>商业启发 {ai.business_score}</span>{note}</div>
    {qualityNote}
    <p className="ai-reason">{ai.reason}</p>
    <div className="ai-tags">{ai.tags?.map(tag => <span key={tag}>{tag}</span>)}</div>
    {detailed && <>
      <p>{ai.summary}</p>
      <blockquote>{ai.evidence}</blockquote>
    </>}
  </div>
}
