import assert from "node:assert/strict"
import fs from "node:fs"

const sourcePath = new URL("../upstream/reactflux/src/store/aiState.js", import.meta.url)
const raw = fs.readFileSync(sourcePath, "utf8")
  .replace(/^import .*$/gm, "")
  .replace(/^export /gm, "")

const load = (saved) => {
  let value = structuredClone(saved)
  const persistentJSON = (_key, fallback) => {
    if (!value) value = structuredClone(fallback)
    return {
      get: () => value,
      set: (next) => { value = next },
    }
  }
  const module = new Function("persistentJSON", raw + ";return { aiState, getAiQuery, aiFilterEnabled }")(persistentJSON)
  return module
}

{
  const { aiState, getAiQuery } = load({mode:"notes",minimum:6,sort:"score",hydrated:true})
  assert.equal(aiState.get().mode, "all")
  assert.equal(aiState.get().auxiliary, "notes")
  assert.equal(aiState.get().direction, "desc")
  assert.deepEqual(getAiQuery(), {
    ai_view:"notes", ai_sort:"note_updated", has_note:true, direction:"desc", limit:24,
  })
}

{
  const { aiState, getAiQuery, aiFilterEnabled } = load({mode:"all",auxiliary:"none",minimum:6,sort:"score",direction:"asc"})
  assert.deepEqual(getAiQuery(), {})
  assert.equal(aiFilterEnabled(), false)
  aiState.setKey("mode", "recommended")
  assert.deepEqual(getAiQuery(), {
    ai_view:"recommended", ai_min:6, ai_sort:"score", direction:"asc", limit:24,
  })
  aiState.setKey("auxiliary", "notes")
  assert.equal(getAiQuery().has_note, true)
  aiState.setKey("auxiliary", "pending")
  assert.deepEqual(getAiQuery(), {ai_view:"pending", ai_sort:"time", direction:"asc", limit:24})
}

console.log("PASS scope × AI state migration and query composition")
