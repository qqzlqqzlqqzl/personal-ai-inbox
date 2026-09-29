import "./AiNews.css"

const labels = {
  requires_fulltext_adapter: "需要论文全文适配 · 未评分", insufficient_content: "原文信息过少 · 未评分", removed: "原条目已移除", pending: "等待后台处理", fetching: "正在抓取原文", analyzing: "正在分析",
  waiting_model: "原文已抓取 · 等待模型配置", fetch_error: "原文抓取受限",
  ai_error: "AI 分析失败，可重试", budget_paused: "已达今日预算 · 稍后重试",
}

export default function AiBadge({ entry, detailed = false }) {
  const ai = entry?.ai
  const note = ai?.has_note ? <span className="ai-note-chip">📝 有笔记</span> : null
  if (!ai || ai.state !== "done") {return <div className="ai-pending" title={ai?.error || ""}>{note}{labels[ai?.state || "pending"] || ai?.state}{detailed && ai?.error && <p>处理详情：{ai.error}</p>}</div>}
  return <div className={detailed ? "ai-verdict ai-verdict-detail" : "ai-verdict"}>
    <div className="ai-scoreline"><strong>推荐 {ai.score}/10</strong><span>技术 {ai.technical_score}</span><span>商业启发 {ai.business_score}</span>{note}</div>
    <p className="ai-reason">{ai.reason}</p>
    <div className="ai-tags">{ai.tags?.map(tag => <span key={tag}>{tag}</span>)}</div>
    {detailed && <>
      <p>{ai.summary}</p>
      <blockquote>{ai.evidence}</blockquote>
    </>}
  </div>
}
