import { draftKey, draftScope, MAX_NOTE, readDraft, scopeKeys } from "./note-drafts.js"

export const NOTE_SESSION_FIELD = "readerNoteSession"
let sequence = 0
const uniqueId = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${++sequence}-${Math.random()}`
const markerFrom = auth => {
  const value = auth[NOTE_SESSION_FIELD]
  return value?.version === 1 && typeof value.id === "string" && value.id.length > 0 &&
    value.server === String(auth.server) && (value.owner === null || typeof value.owner === "string")
    ? value : null
}
export function createNoteSessionAuth(auth, currentUser, newId = uniqueId) {
  return { ...auth, [NOTE_SESSION_FIELD]: {
    version: 1, id: newId(), server: String(auth.server),
    owner: currentUser?.id == null ? null : String(currentUser.id), legacy: false,
  } }
}

// The scope and leases belong to the session, not to mounted editors. Storage
// errors never restore a retired lease; the persisted session id also prevents
// recovery of a physically undeletable old draft after a new login.
export function createNoteSessionManager({
  auth, data, authKey, validAuth, getRevision = () => data.get().sessionRevision,
  getStorage = () => window.sessionStorage, newId = uniqueId,
}) {
  let current = null, epoch = 0, blockedId = null, blockedKey = null, syncing = false
  let hasRetired = false, disposed = false, lastCleanup = { ok: true, removed: 0 }
  const initialLegacy = validAuth(auth.get()) && !markerFrom(auth.get())
  const memory = new Map(), writers = new Map(), knownKeys = new Set(), listeners = new Set()
  const storage = () => { try { return getStorage() } catch { return null } }
  const matchesScope = (key, scope) => {
    const parsed = draftScope(key)
    return !!scope && parsed?.server === scope.server && parsed.owner === scope.owner
  }
  const notify = () => { for (const fn of [...listeners]) fn() }
  const invalidateWriters = () => {
    const outgoing = [...writers.values()]
    writers.clear()
    for (const lease of outgoing) {
      lease.active = false
      try { lease.invalidate?.() } catch {}
    }
  }
  const purge = scope => {
    const target = storage(), found = scope ? scopeKeys(target, scope.server, scope.owner) : { keys: [], ok: true }
    const keys = new Set(found.keys)
    for (const key of knownKeys) if (matchesScope(key, scope)) keys.add(key)
    for (const key of memory.keys()) if (matchesScope(key, scope)) keys.add(key)
    let ok = found.ok, removed = 0
    for (const key of keys) {
      try { target.removeItem(key); removed++ } catch { ok = false }
      memory.delete(key); knownKeys.delete(key)
    }
    return { ok, removed }
  }
  const retire = scope => {
    const outgoing = current
    blockedId = outgoing?.id ?? markerFrom(auth.get())?.id ?? null
    blockedKey = outgoing?.authKey ?? authKey(auth.get())
    current = null; epoch++; hasRetired = true
    // Invalidate synchronously before storage, auth resets, or editor unmounts.
    invalidateWriters()
    lastCleanup = purge(scope ?? outgoing?.scope)
    notify()
    return lastCleanup
  }
  const sync = () => {
    if (syncing || disposed) return current
    syncing = true
    try {
      let a = auth.get(), marker = markerFrom(a)
      const key = authKey(a), revision = getRevision(), d = data.get()
      if (!validAuth(a)) {
        if (current) retire(current.scope)
        return null
      }
      if (blockedKey === key && (marker?.id ?? null) === blockedId) return null
      const verifiedOwner = d.identityAuthSessionKey === key && d.currentUser?.id != null
        ? String(d.currentUser.id) : null
      const incomingScope = marker?.owner == null ? null : { server: String(a.server), owner: marker.owner }
      const previousId = current?.id ?? marker?.id
      const replaced = !!current && (
        current.authKey !== key || current.revision !== revision || current.id !== marker?.id ||
        (verifiedOwner !== null && current.scope && current.scope.owner !== verifiedOwner)
      )
      const cachedOwnerReplaced = !current && verifiedOwner !== null && incomingScope &&
        incomingScope.owner !== verifiedOwner
      if (replaced || cachedOwnerReplaced) retire(current?.scope ?? incomingScope)
      if (!marker || ((replaced || cachedOwnerReplaced) && marker.id === previousId)) {
        marker = {
          version: 1, id: newId(), server: String(a.server), owner: verifiedOwner,
          legacy: initialLegacy && !hasRetired,
        }
        a = { ...a, [NOTE_SESSION_FIELD]: marker }; auth.set(a)
      } else if (verifiedOwner !== null && marker.owner !== verifiedOwner) {
        marker = { ...marker, owner: verifiedOwner }
        a = { ...a, [NOTE_SESSION_FIELD]: marker }; auth.set(a)
      }
      blockedId = null; blockedKey = null
      current = {
        id: marker.id, epoch, revision, authKey: key, legacy: !!marker.legacy,
        scope: marker.owner === null ? null : { server: String(a.server), owner: marker.owner },
        ready: verifiedOwner !== null && marker.owner === verifiedOwner,
      }
      return current
    } finally { syncing = false }
  }
  const context = () => {
    const value = sync()
    return value ? { id: value.id, epoch, revision: value.revision, authKey: value.authKey } : null
  }
  const matchesContext = expected => {
    const value = sync()
    return !!expected && !!value && expected.id === value.id && expected.epoch === epoch &&
      expected.revision === value.revision && expected.authKey === value.authKey
  }
  const isCurrentLease = lease => {
    const value = sync()
    return !!value?.ready && !!lease?.active && lease.id === value.id && lease.epoch === epoch &&
      lease.revision === value.revision && lease.authKey === value.authKey &&
      writers.get(lease.key) === lease && matchesScope(lease.key, value.scope)
  }
  const validRecord = (record, value = current) => !!record && !!value &&
    (record.session === value.id || (!record.session && value.legacy))
  const subscribeStore = store => (store.listen ?? store.subscribe).call(store, () => { sync(); notify() })
  const stopAuth = subscribeStore(auth), stopData = subscribeStore(data)
  sync()
  return {
    context, matchesContext, isCurrentLease,
    requestStamp() { const value = sync(); return value ? `${value.id}:${epoch}` : `blocked:${epoch}` },
    editorContext() { const value = sync(); return value?.ready ? { ...value } : null },
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    acquire(entryId, invalidate) {
      const value = sync()
      if (!value?.ready) return null
      const key = draftKey(value.scope.server, value.scope.owner, entryId), previous = writers.get(key)
      if (previous) {
        previous.active = false
        try { previous.invalidate?.() } catch {}
      }
      const lease = { ...context(), key, writer: newId(), active: true, invalidate }
      writers.set(key, lease); knownKeys.add(key)
      return lease
    },
    release(lease) {
      if (writers.get(lease?.key) !== lease) return
      lease.active = false; writers.delete(lease.key)
    },
    read(lease) {
      if (!isCurrentLease(lease)) return null
      const value = memory.get(lease.key) ?? readDraft(storage(), lease.key)
      return validRecord(value) ? value : null
    },
    store(lease, note, base) {
      if (!isCurrentLease(lease)) return false
      const value = {
        version: 1, note: String(note).slice(0, MAX_NOTE), base: String(base).slice(0, MAX_NOTE),
        at: Date.now(), session: lease.id, writer: lease.writer,
      }
      memory.set(lease.key, value); knownKeys.add(lease.key)
      let ok = true
      try { storage().setItem(lease.key, JSON.stringify(value)) } catch { ok = false }
      notify(); return ok
    },
    remove(lease, expectedNote) {
      if (!isCurrentLease(lease)) return false
      const cached = memory.get(lease.key)
      if (cached && expectedNote !== undefined && cached.note !== expectedNote) return true
      memory.delete(lease.key)
      let ok = true
      const target = storage()
      try {
        const raw = target.getItem(lease.key), value = raw ? JSON.parse(raw) : null
        if (!value || (validRecord(value) && (expectedNote === undefined || value.note === expectedNote))) {
          target.removeItem(lease.key)
        }
      } catch { ok = false }
      notify(); return ok
    },
    inspect(expected = context()) {
      const value = sync()
      if (!matchesContext(expected)) return { current: false, notes: [], count: 0, storageOK: true }
      const target = storage(), found = value.scope ? scopeKeys(target, value.scope.server, value.scope.owner) : { keys: [], ok: true }
      const keys = new Set(found.keys)
      for (const key of memory.keys()) if (matchesScope(key, value.scope)) keys.add(key)
      const notes = [], unreadable = []
      let storageOK = found.ok
      for (const key of keys) {
        let record = memory.get(key)
        if (!record) {
          try { target.getItem(key) } catch { storageOK = false }
          record = readDraft(target, key)
        }
        if (validRecord(record, value)) {
          notes.push({ entryId: draftScope(key).entryId, note: record.note, base: record.base, at: record.at })
        } else if (!record) unreadable.push(key)
      }
      return { current: true, scope: value.scope, notes, count: notes.length, unreadableCount: unreadable.length, storageOK }
    },
    exportText(expected) {
      const snapshot = this.inspect(expected)
      if (!snapshot.current) return null
      return JSON.stringify({
        version: 1, kind: "本标签页未同步笔记草稿", exportedAt: new Date().toISOString(),
        server: snapshot.scope?.server, owner: snapshot.scope?.owner, notes: snapshot.notes,
      }, null, 2)
    },
    end(expected) {
      if (expected && !matchesContext(expected)) return { retired: false, ok: true, removed: 0 }
      const value = sync()
      const result = retire(value?.scope)
      return { retired: true, ...result }
    },
    resumeData() {
      const a = auth.get()
      if (!validAuth(a)) return
      auth.set(createNoteSessionAuth(a, null, newId))
      sync(); notify()
    },
    lastCleanup() { return lastCleanup },
    dispose() {
      disposed = true; current = null; epoch++; invalidateWriters()
      stopAuth(); stopData(); listeners.clear()
      // Disposal/reload is not logout: retained drafts keep their session id.
    },
  }
}
