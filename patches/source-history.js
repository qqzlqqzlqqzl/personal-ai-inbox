// Keep unknown data distinct from a genuinely empty source.
export function historyDate(value) {
  if (!value) return "未知"
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? "未知" : date.toISOString().replace("T", " ").replace(/\.\d{3}Z$/, " UTC")
}

export function historyLines(history) {
  const stored = history?.stored
  const window = history?.feed_window
  const lines = []
  if (stored?.state === "ok") {
    lines.push(`已存储 ${stored.count} 条${stored.includes_removed === false ? "（已读、未读；当前后端不提供已移除条目）" : "（含已读、未读及仍保留的已移除条目）"}`)
    if (stored.count) {
      lines.push(`最旧 published_at：${historyDate(stored.oldest_published_at)}`)
      lines.push(`最新 published_at：${historyDate(stored.newest_published_at)}`)
    } else lines.push("当前没有已存储条目")
  } else lines.push(stored?.state === "changing" ? "存储条目正在变化，请稍后刷新" : "存储历史暂不可读，条目数与时间范围未知")
  if (stored?.checked_at) lines.push(`存储查询时间：${historyDate(stored.checked_at)}`)
  if (window?.state === "ok") {
    lines.push(`RSS/Atom 本次暴露 ${window.count} 条`)
    if (window.dated_count) {
      lines.push(`Feed 日期：${historyDate(window.oldest_at)} 至 ${historyDate(window.newest_at)}`)
      lines.push(`有日期条目跨度 ${window.span_days} 天${window.undated_count ? `；另有 ${window.undated_count} 条日期未知，范围可能更宽` : ""}`)
    } else if (window.count) lines.push(`全部 ${window.count} 条日期未知，无法判断历史时间跨度`)
    else lines.push("本次 feed 为空，不能据此判断站点历史为空")
    if (window.updated_fallback_count) lines.push(`${window.updated_fallback_count} 条缺少有效发布时间，范围采用其更新时间`)
  } else {
    const reason = window?.reason === "unsupported_or_authenticated_feed"
      ? "地址或认证方式不支持安全的独立检查"
      : window?.reason?.startsWith("http_") ? `HTTP ${window.reason.slice(5)}` : "本次未能读取有效 feed"
    lines.push(`RSS/Atom 暴露历史未知：${reason}`)
  }
  if (window?.checked_at) lines.push(`Feed 检查时间：${historyDate(window.checked_at)}${window.cached ? "（复用 5 分钟内快照）" : ""}`)
  return lines
}
