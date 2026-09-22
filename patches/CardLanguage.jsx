const states = {
  pending: "中文卡片等待翻译",
  processing: "正在生成中文卡片",
  error: "翻译失败，暂显示原文",
  budget_paused: "中文翻译等待预算恢复",
  waiting_model: "中文翻译等待模型配置",
}

export default function CardLanguage({ entry }) {
  const card = entry.card
  if (!card || ["done", "native"].includes(card.status)) return null
  return <span className="card-language-status" title={entry.title}>
    {states[card.status] || "中文卡片准备中"}
  </span>
}
