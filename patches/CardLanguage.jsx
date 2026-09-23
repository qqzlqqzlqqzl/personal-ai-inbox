const states = {
  pending: ["待译", "中文标题与简介已排队，暂时保留现有内容"],
  processing: ["翻译中", "正在生成中文标题与简介"],
  error: ["翻译失败", "翻译失败，现有内容仍可阅读；可在 AI 设置查看原因"],
  budget_paused: ["翻译暂缓", "今日模型预算不足，现有内容仍可阅读"],
  waiting_model: ["模型未配置", "中文翻译等待模型配置"],
}

export default function CardLanguage({ entry }) {
  const card = entry.card
  if (!card || card.enabled === false || ["done", "native", "disabled"].includes(card.status)) return null
  const [label, description] = states[card.status] || states.pending
  return <span className="card-language-status" title={description} aria-label={description}>
    {label}
  </span>
}
