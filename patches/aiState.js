import { persistentJSON } from "@nanostores/persistent"

export const AI_PAGE_SIZE = 24

const initialAiState = { mode: "recommended", minimum: 6, sort: "score", hydrated: false }
export const aiState = persistentJSON("ai-view-state", initialAiState)
aiState.setKey = (key, value) => aiState.set({ ...aiState.get(), [key]: value })
aiState.set({ ...aiState.get(), hydrated: false })

export const getAiQuery = () => {
  const { mode, minimum, sort } = aiState.get()
  return mode === "all"
    ? {}
    : { ai_view: mode, ai_min: minimum, ai_sort: sort, limit: AI_PAGE_SIZE }
}

export const aiFilterEnabled = () => aiState.get().mode !== "all"
