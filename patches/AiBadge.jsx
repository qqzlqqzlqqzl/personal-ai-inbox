import "./AiNews.css"

const labels = {
  pending: "等待后台处理", fetching: "正在抓取原文", analyzing: "正在分析",
  waiting_model: "原文已抓取 · 等待模型配置", fetch_error: "原文抓取受限",
  ai_error: "AI 分析失败，可重试", budget_paused: "已达今日预算 · 稍后重试",
}

export default function AiBadge({ entry, detailed = false }) {
  const ai = entry?.ai
  if (!ai || ai.state !== "done") return <div className="ai-pending">{labels[ai?.state || "pending"] || ai?.state}</div>
  return <div className={detailed ? "ai-verdict ai-verdict-detail" : "ai-verdict"}>
    <div className="ai-scoreline"><strong>推荐 {ai.score}/10</strong><span>技术 {ai.technical_score}</span><span>商业启发 {ai.business_score}</span></div>
    <p className="ai-reason">{ai.reason}</p>
    <div className="ai-tags">{ai.tags?.map(tag => <span key={tag}>{tag}</span>)}</div>
    {detailed && <>
      <p>{ai.summary}</p>
      <blockquote>{ai.evidence}</blockquote>
      <small>依据抓取的原文文本 · {ai.input_chars} 字符 · 保留 {ai.image_count} 张正文图片{ai.truncated ? " · 输入已截断" : ""} · {ai.model}</small>
      <p className="ai-pending">评分是模型判断，不是事实保证；图片保留不代表模型理解了图片内容。</p>
    </>}
  </div>
}
