import { getAiQuery } from "@/store/aiState"
import { authState } from "@/store/authState"
import { dataState } from "@/store/dataState"
import { getReadingCalendarSnapshot } from "@/store/readingCalendarState"
import { articleSnapshotQuery, snapshotOwnerKey } from "@/utils/article-list-snapshot"
import { getAuthSessionKey } from "@/utils/auth"
import { createHomeIdentity } from "@/utils/home-page"
import { isSessionClearing } from "@/utils/session"

export function getArticleSnapshotContext(content, settings, info) {
  const calendar = getReadingCalendarSnapshot()
  const auth = authState.get()
  const data = dataState.get()
  if (!calendar.ready || isSessionClearing() || data.verifiedAuthSessionKey !== getAuthSessionKey(auth)) return null
  const owner = snapshotOwnerKey(createHomeIdentity(auth.server, data.currentUser?.id))
  if (!owner) return null
  const categoryHidden = info.from === "category" && data.categoriesData.some(
    category => category.id === Number(info.id) && category.hide_globally,
  )
  return { owner, ...articleSnapshotQuery({ content, settings, info, ai: getAiQuery(), calendar, categoryHidden }) }
}

export const matchesArticleSnapshot = (cache, context) => Boolean(cache && context &&
  cache.owner === context.owner && cache.signature === context.signature)
