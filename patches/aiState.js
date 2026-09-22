import { map } from "nanostores"

export const aiState = map({ mode: "recommended", minimum: 6, sort: "score", hydrated: false })

export const getAiQuery = () => {
  const { mode, minimum, sort } = aiState.get()
  return mode === "all" ? {} : { ai_view: mode, ai_min: minimum, ai_sort: sort }
}

export const aiFilterEnabled = () => aiState.get().mode !== "all"
