import { persistentJSON } from "@nanostores/persistent"
import { aiState } from "@/store/aiState"
import { contentState, setFilterDate, setFilterString } from "@/store/contentState"
import { getHomeIdentityKey } from "@/utils/home-page"
import { settingsState, updateSettings } from "@/store/settingsState"

const saved = persistentJSON("reader-last-reading-v1", { entries: {} })
const AI_KEYS = ["mode", "auxiliary", "minimum", "sort", "direction"]
export function lastReading(identity) {
  const value = saved.get()?.entries?.[getHomeIdentityKey(identity)]
  if (!value || !["view", "feed", "category"].includes(value.target?.type)) return null
  if (value.target.type === "view" && !["all", "today", "starred", "history"].includes(value.target.id)) return null
  if (value.target.type !== "view" && !(Number.isSafeInteger(value.target.id) && value.target.id > 0)) return null
  return value
}
export function restoreReading(identity) {
  const value = lastReading(identity)
  if (!value) return
  const state = aiState.get()
  aiState.set({ ...state, ...Object.fromEntries(AI_KEYS.filter(key => Object.hasOwn(value.ai || {}, key)).map(key => [key, value.ai[key]])) })
  if (value.settings) updateSettings(value.settings)
  setFilterDate(typeof value.date === "string" ? value.date : null)
  setFilterString(typeof value.search === "string" ? value.search : "")
}
export function rememberReading(identity, pathname) {
  const key = getHomeIdentityKey(identity)
  const match = pathname.match(/^\/(all|today|starred|history|feed|category)(?:\/(\d+))?(?:\/|$)/)
  if (!key || !match) return
  const content = contentState.get(), scope = match[1]
  // A route selected before its list commits must not save the preceding
  // scope's filters under the new route, or across an account transition.
  if (content.infoFrom !== scope || String(content.infoId ?? "") !== String(match[2] ?? "")) return
  const target = ["feed", "category"].includes(scope)
    ? { type: scope, id: Number(match[2]) } : { type: "view", id: scope }
  const ai = aiState.get()
  const settings = settingsState.get()
  const value = { target, ai: Object.fromEntries(AI_KEYS.map(name => [name, ai[name]])),
    settings: Object.fromEntries(["showStatus", "orderBy", "orderDirection"].map(name => [name, settings[name]])),
    date: content.filterDate, search: content.filterString }
  const current = saved.get() || { entries: {} }
  if (JSON.stringify(current.entries?.[key]) === JSON.stringify(value)) return
  saved.set({ entries: { ...current.entries, [key]: value } })
}
