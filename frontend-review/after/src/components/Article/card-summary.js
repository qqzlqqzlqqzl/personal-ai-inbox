const ORIGINAL_LINK_ONLY = /^[\p{P}\s←-⇿➜➡]*(?:点击)?查看原文[\p{P}\s←-⇿➜➡]*$/u
const TEASER_SUFFIX = /(?:查看原文|继续阅读|\bread more|\bcontinue reading)[\p{P}\s←-⇿➜➡]*$/iu
const ENDS_IN_LINK = /<\/a>\s*(?:<\/[a-z][\w:-]*>\s*)*$/i

function hasSummaryText(text) {
  return text && !text.includes("点击查看原文") && !ORIGINAL_LINK_ONLY.test(text)
}

function isTeaser(text, entry, previewContent) {
  const preview = previewContent?.trim()
  if (TEASER_SUFFIX.test(text) && TEASER_SUFFIX.test(preview) && ENDS_IN_LINK.test(entry.content)) return true
  if (text !== preview) return false

  const { body_completeness: body, content_source: source, source_chars: sourceLength } = entry.ai ?? {}
  if (body?.status === "verified") return false
  if (body?.status === "incomplete") return true
  // Match the reader's detail fallback for a teaser backed by a site-rule extraction.
  return source === "original_url_site_rule" && sourceLength >= 800 &&
    text.length < Math.min(500, Math.max(120, Math.floor(sourceLength * 0.35)))
}

export function entryCardSummary(entry, previewContent) {
  const aiSummary = entry.ai?.summary
  const base = entry.card?.summary || (entry.ai?.state === "done" ? aiSummary : previewContent)
  const text = typeof base === "string" ? base.trim() : ""
  if (hasSummaryText(text) && !isTeaser(text, entry, previewContent)) return base
  const fallback = typeof aiSummary === "string" ? aiSummary.trim() : ""
  return hasSummaryText(fallback) ? fallback : ""
}
