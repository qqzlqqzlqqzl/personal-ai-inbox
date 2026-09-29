import { persistentJSON } from "@nanostores/persistent"

export const AI_PAGE_SIZE = 24

const initialAiState = {
  mode: "recommended",
  auxiliary: "none",
  minimum: 6,
  sort: "score",
  direction: "desc",
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
  if (!["score", "technical", "business", "time", "note_updated"].includes(next.sort)) {
    next.sort = "score"
  }
  if (!["asc", "desc"].includes(next.direction)) next.direction = "desc"
  return next
}

export const aiState = persistentJSON("ai-view-state", initialAiState)
aiState.set(normalizeAiState({ ...aiState.get(), hydrated: false }))
aiState.setKey = (key, value) => aiState.set(normalizeAiState({ ...aiState.get(), [key]: value }))

export const getAiQuery = () => {
  const { mode, auxiliary, minimum, sort, direction } = normalizeAiState(aiState.get())
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
      ai_min: minimum,
      ai_sort: sort,
      direction,
      ...(auxiliary === "notes" ? { has_note: true } : {}),
      limit: AI_PAGE_SIZE,
    }
  }
  return {}
}

export const aiFilterEnabled = () => Boolean(getAiQuery().ai_view)
