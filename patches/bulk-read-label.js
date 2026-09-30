// Describe the server-side scope, independently of the number of mounted cards.
export const bulkReadLabel = (scope, filtered = false) => {
  const scopes = { today: "今天", feed: "当前订阅源", category: "当前分类", starred: "收藏" }
  const prefix = scopes[scope]
  if (prefix) return `标记${prefix}${filtered ? "筛选出的" : "的全部"}文章为已读`
  return filtered ? "标记当前筛选结果为已读" : "标记全部文章为已读"
}
