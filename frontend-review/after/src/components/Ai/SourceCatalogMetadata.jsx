function timestamp(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null
  const date = new Date(value * 1000)
  return Number.isNaN(date.getTime()) ? null : date.toISOString()
}

export default function SourceCatalogMetadata({ source: s }) {
  const snapshot = s.vendor_snapshot
  const archived = snapshot?.source_kind === "newsletter_archive"
  const summaryOnly = s.provider === "Kicktraq" && s.rss_summary_only === true
  const note = typeof s.note === "string" && s.note.trim() ? s.note : null
  const checkedDate = typeof s.checked_at === "string" && /^\d{4}-\d{2}-\d{2}T/.test(s.checked_at) ? new Date(s.checked_at) : null
  const checkedAt = checkedDate && Number.isFinite(checkedDate.getTime()) ? checkedDate.toISOString() : null
  const lastSuccess = timestamp(snapshot?.last_success_at)
  const lastAttempt = timestamp(snapshot?.last_attempt_at)
  let health = null
  if (snapshot) {
    if (snapshot.state_available !== true) health = "上游缓存状态未知"
    else if (snapshot.freshness === "never_collected") health = "尚无成功采集记录"
    else if (snapshot.freshness === "stale") health = "上游快照已过期，保留上次内容"
    else if (snapshot.freshness === "fresh") health = "上次成功快照仍在 6 小时检查窗口内"
    else health = "快照新鲜度未知"
  }
  return <>
    <small>{s.category} · {s.subscribed ? "已订阅 · " : ""}{s.live_error ? "阅读器抓取异常 · " : ""}{s.status === "ok" ? "订阅格式可解析" : (s.subscribed ? "已接入阅读器" : (s.error || "待验证"))}</small>
    {archived && <small>精选邮件的第三方归档；非项目列表，项目详情与直达链接缺失</small>}
    {summaryOnly && <small>Kicktraq 第三方项目 RSS 摘要；项目页在 Kicktraq，未提供 Kickstarter 直达链接；未接入项目全文评分{s.summary_policy_ready !== true && " · 摘要保护或来源身份待核实，暂不可新增订阅"}</small>}
    {!summaryOnly && s.subscription_block_reason === "source_identity_or_policy_unverified" && <small>来源身份或摘要保护待核实，暂不可新增订阅</small>}
    {note && <small className="ai-source-note">{note}</small>}
    {summaryOnly && checkedAt && <small>格式样本核验：<time dateTime={checkedAt}>{checkedAt}</time>；不代表本次源更新成功</small>}
    {s.subscribed && typeof s.subscription_category === "string" && s.subscription_category && s.subscription_category !== s.category && <small>当前订阅分类：{s.subscription_category}</small>}
    {snapshot && <small className="ai-source-upstream">{snapshot.last_error && "上游读取失败；"}{health}{lastSuccess && <> · 上次成功 <time dateTime={lastSuccess}>{lastSuccess}</time></>}{lastAttempt && <> · 最近尝试 <time dateTime={lastAttempt}>{lastAttempt}</time></>}</small>}
  </>
}
