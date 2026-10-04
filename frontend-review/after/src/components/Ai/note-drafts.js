export const MAX_NOTE = 20000
const PREFIX = "reader.note.draft:v1:"
export function draftKey(server, owner, entryId) {
  return PREFIX + encodeURIComponent(JSON.stringify([String(server), String(owner), String(entryId)]))
}
export function draftScope(key) {
  try {
    if (!key.startsWith(PREFIX)) return null
    const parts = JSON.parse(decodeURIComponent(key.slice(PREFIX.length)))
    return Array.isArray(parts) && parts.length === 3 && parts.every(value => typeof value === "string")
      ? { server: parts[0], owner: parts[1], entryId: parts[2] } : null
  } catch { return null }
}
export function scopeKeys(storage, server, owner) {
  const keys = []
  try {
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i), scope = typeof key === "string" ? draftScope(key) : null
      if (scope?.server === String(server) && scope.owner === String(owner)) keys.push(key)
    }
    return { keys, ok: true }
  } catch { return { keys, ok: false } }
}
export function readDraft(storage, key) {
  try {
    const value = JSON.parse(storage.getItem(key) || "null")
    if (!value || value.version !== 1 || typeof value.note !== "string" || value.note.length > MAX_NOTE ||
        typeof value.base !== "string" || value.base.length > MAX_NOTE || !Number.isFinite(value.at) ||
        Date.now() - value.at > 7 * 86400000) return null
    return value
  } catch { return null }
}
export function storeDraft(storage, key, note, base, ownership = {}) {
  try {
    storage.setItem(key, JSON.stringify({
      version: 1, note: String(note).slice(0, MAX_NOTE), base: String(base).slice(0, MAX_NOTE), at: Date.now(),
      ...(ownership.session ? { session: ownership.session, writer: ownership.writer } : {}),
    }))
    return true
  } catch { return false }
}
export function removeDraft(storage, key, expectedNote) {
  try {
    if (expectedNote === undefined || readDraft(storage, key)?.note === expectedNote) storage.removeItem(key)
    return true
  } catch { return false }
}
