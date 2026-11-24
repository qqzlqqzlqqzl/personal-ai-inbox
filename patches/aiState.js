import { persistentJSON } from "@nanostores/persistent"

export const AI_PAGE_SIZE = 24

const initialAiState = {
  mode: "recommended",
  auxiliary: "none",
  minimum: 6,
  sort: "fresh",
  sortVersion: 2,
  direction: "desc",
  serverMinimum: null,
  hydrated: false,
}

const normalizeAiState = (value = {}) => {
  const next = { ...initialAiState, ...value }
  // Migrate the earlier single-axis state without dropping the user's choice.
  if (next.mode === "notes" || next.mode === "pending") {
    next.auxiliary = next.mode
    next.mode = "all"
  }
  if (!["all", "recommended"].includes(next.mode)) next.mode = "recommended"
  if (!["none", "notes", "pending"].includes(next.auxiliary)) next.auxiliary = "none"
  // v2: the plain all-time score order surfaced years-old articles first; move
  // the old default once to the freshness-weighted order, keep explicit choices.
  if (next.sortVersion !== 2) {
    if (next.sort === "score" && next.direction === "desc") next.sort = "fresh"
    next.sortVersion = 2
  }
  if (!["fresh", "score", "technical", "business", "time", "note_updated"].includes(next.sort)) {
    next.sort = "fresh"
  }
  if (!["asc", "desc"].includes(next.direction)) next.direction = "desc"
  if (next.sort === "fresh") next.direction = "desc"
  const minimum = Number(next.minimum)
  next.minimum = Number.isFinite(minimum) && minimum >= 0 && minimum <= 10 ? minimum : 6
  if (!Number.isFinite(next.serverMinimum)) next.serverMinimum = null
  return next
}

export const aiState = persistentJSON("ai-view-state", initialAiState)
aiState.set(normalizeAiState({ ...aiState.get(), hydrated: false }))
aiState.setKey = (key, value) => aiState.set(normalizeAiState({ ...aiState.get(), [key]: value }))

export const getAiQuery = () => {
  const { mode, auxiliary, minimum, sort, direction, hydrated, serverMinimum } = normalizeAiState(aiState.get())
  if (auxiliary === "pending") {
    return { ai_view: "pending", ai_sort: "time", direction, limit: AI_PAGE_SIZE }
  }
  if (auxiliary === "notes" && mode === "all") {
    return {
      ai_view: "notes",
      ai_sort: sort === "time" ? "time" : "note_updated",
      has_note: true,
      direction,
      limit: AI_PAGE_SIZE,
    }
  }
  if (mode === "recommended") {
    return {
      ai_view: "recommended",
      // Omit ai_min while it is the server's saved threshold (or not yet known):
      // the server applies the same value, so loading settings never changes the
      // query (or the list request key) and the first list request is final.
      ...(hydrated && minimum !== serverMinimum ? { ai_min: minimum } : {}),
      ai_sort: sort,
      direction,
      ...(auxiliary === "notes" ? { has_note: true } : {}),
      limit: AI_PAGE_SIZE,
    }
  }
  return {}
}

export const aiFilterEnabled = () => Boolean(getAiQuery().ai_view)
