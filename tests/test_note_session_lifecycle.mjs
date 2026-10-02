import assert from "node:assert/strict"
import test from "node:test"
import { createNoteSessionAuth, createNoteSessionManager } from "../frontend-review/after/src/components/Ai/note-session-core.js"
import { draftKey, readDraft, storeDraft } from "../frontend-review/after/src/components/Ai/note-drafts.js"

const credentials = { server: "https://reader.example.test/mf", token: "synthetic-token", username: "", password: "" }
const authKey = ({ server, token, username, password }) => JSON.stringify([server, token, username, password])
function store(value) {
  const listeners = new Set()
  return { get: () => value, set(next) { value = next; for (const fn of [...listeners]) fn() },
    listen(fn) { listeners.add(fn); return () => listeners.delete(fn) } }
}
function storage() {
  const map = new Map()
  return { map, clearCalls: 0, get length() { if (this.denied) throw Error("storage refused"); return map.size },
    key(index) { return [...map.keys()][index] ?? null },
    getItem(key) { if (this.denied) throw Error("storage refused"); return map.get(key) ?? null },
    setItem(key, value) { if (this.denied) throw Error("storage refused"); map.set(key, value) },
    removeItem(key) { if (this.denied) throw Error("storage refused"); map.delete(key) },
    clear() { this.clearCalls++; throw Error("unscoped clear forbidden") } }
}
function fixture({ authValue = credentials, owner = 1, disk = storage(), revision = 7, onRetire } = {}) {
  const auth = store({ ...authValue }), data = store({ currentUser: owner === null ? null : { id: owner },
    identityAuthSessionKey: owner === null ? "" : authKey(authValue), sessionRevision: revision })
  let id = 0
  const manager = createNoteSessionManager({ auth, data, authKey, validAuth: value => !!value.server && !!value.token,
    getStorage: () => disk, onRetire, newId: () => "fixture-session-" + ++id })
  return { auth, data, disk, manager, newAuth(user = { id: 1 }) {
    const value = createNoteSessionAuth(credentials, user, () => "new-login-" + ++id)
    auth.set(value); data.set({ ...data.get(), currentUser: user, identityAuthSessionKey: authKey(value) })
  } }
}

await test("closed A+B failed drafts are retained by session, exported locally, then purged exactly", () => {
  const f = fixture(), a = f.manager.acquire(101), b = f.manager.acquire(102)
  f.manager.store(a, "synthetic A unsynced", "server A"); f.manager.release(a)
  f.manager.store(b, "synthetic B unsynced", "server B"); f.manager.release(b)
  const elsewhere = [draftKey(credentials.server, 2, 101), draftKey("https://other.example.test/mf", 1, 101)]
  for (const key of elsewhere) storeDraft(f.disk, key, "other scope", "server")
  f.disk.setItem("unrelated-setting", "retain")
  const context = f.manager.context(), before = [...f.disk.map], exportJSON = f.manager.exportText(context)
  assert.deepEqual(JSON.parse(exportJSON).notes.map(note => note.note).sort(), ["synthetic A unsynced", "synthetic B unsynced"])
  assert.ok(!exportJSON.includes("synthetic-token")); assert.equal(f.manager.inspect(context).count, 2)
  assert.deepEqual([...f.disk.map], before, "export/cancel must not discard")
  const result = f.manager.end(context)
  assert.equal(result.retired, true); assert.equal(result.ok, true)
  assert.equal(f.disk.getItem(a.key), null); assert.equal(f.disk.getItem(b.key), null)
  for (const key of elsewhere) assert.notEqual(f.disk.getItem(key), null)
  assert.equal(f.disk.getItem("unrelated-setting"), "retain"); assert.equal(f.disk.clearCalls, 0)
  assert.equal(f.manager.exportText(context), null); f.manager.dispose()
})
await test("all writer leases become stale synchronously before auth reset; late same-owner writes cannot reappear", () => {
  const f = fixture(); let invalidatedWhileAuthenticated = false
  const old = f.manager.acquire(101, () => { invalidatedWhileAuthenticated = !!f.auth.get().token })
  f.manager.store(old, "old unsynced", "server")
  const oldContext = f.manager.context(); f.manager.end(oldContext)
  assert.equal(invalidatedWhileAuthenticated, true)
  f.newAuth()
  const fresh = f.manager.acquire(101); f.manager.store(fresh, "new login draft", "server")
  assert.equal(f.manager.store(old, "late old PUT/timer", "old server"), false)
  assert.equal(f.manager.remove(old), false); f.manager.release(old)
  assert.equal(readDraft(f.disk, fresh.key).note, "new login draft")
  assert.equal(f.manager.end(oldContext).retired, false); assert.ok(f.auth.get().token); f.manager.dispose()
})
await test("verified owner replacement with unchanged revision purges outgoing owner and changes request stamp", () => {
  const f = fixture(), old = f.manager.acquire(101), closed = f.manager.acquire(102)
  f.manager.store(old, "owner one open", "server"); f.manager.store(closed, "owner one closed", "server"); f.manager.release(closed)
  const other = draftKey(credentials.server, 2, 202); storeDraft(f.disk, other, "owner two retain physically", "server")
  const revision = f.data.get().sessionRevision, stamp = f.manager.requestStamp()
  f.data.set({ ...f.data.get(), currentUser: { id: 2 } })
  assert.equal(f.data.get().sessionRevision, revision); assert.notEqual(f.manager.requestStamp(), stamp)
  assert.equal(f.manager.isCurrentLease(old), false); assert.equal(f.disk.getItem(old.key), null); assert.equal(f.disk.getItem(closed.key), null)
  assert.notEqual(f.disk.getItem(other), null); assert.equal(f.manager.editorContext().scope.owner, "2")
  assert.equal(f.manager.store(old, "late secret", "server"), false); f.manager.dispose()
})
await test("old same-session writer and unmount cannot erase the latest same-account draft, even with identical text", () => {
  const f = fixture(), old = f.manager.acquire(101)
  f.manager.store(old, "same text", "server")
  const fresh = f.manager.acquire(101); f.manager.store(fresh, "same text", "new server")
  assert.equal(f.manager.remove(old, "same text"), false); f.manager.release(old)
  assert.equal(f.manager.isCurrentLease(fresh), true); assert.equal(readDraft(f.disk, fresh.key).base, "new server")
  f.manager.dispose()
})
await test("same-session reload remembers outgoing scope before editors and recovers after identity verifies", () => {
  const first = fixture(), lease = first.manager.acquire(101)
  first.manager.store(lease, "recover on reload", "server"); first.manager.release(lease)
  const authValue = first.auth.get(), disk = first.disk; first.manager.dispose()
  const next = fixture({ authValue, owner: null, disk, revision: 0 })
  assert.equal(next.manager.inspect().count, 1); assert.equal(next.manager.acquire(101), null)
  next.data.set({ ...next.data.get(), currentUser: { id: 1 }, identityAuthSessionKey: authKey(authValue) })
  assert.equal(next.manager.read(next.manager.acquire(101)).note, "recover on reload")
  next.manager.dispose()
  const expired = fixture({ authValue, owner: null, disk, revision: 0 })
  expired.manager.end()
  assert.equal(disk.getItem(lease.key), null, "401 before identity reload must still know outgoing scope")
  expired.manager.dispose()
})
await test("storage failure preserves memory export, reports purge refusal, and does not restore old-generation drafts", () => {
  const f = fixture(), a = f.manager.acquire(101), b = f.manager.acquire(102)
  f.manager.store(a, "persisted before refusal", "server"); f.disk.denied = true
  assert.equal(f.manager.store(b, "memory-only failed draft", "server"), false)
  const snapshot = f.manager.inspect(); assert.equal(snapshot.storageOK, false); assert.equal(snapshot.count, 2)
  assert.equal(JSON.parse(f.manager.exportText(f.manager.context())).notes.length, 2)
  assert.equal(f.manager.end().ok, false); assert.equal(f.manager.isCurrentLease(a), false)
  f.disk.denied = false; f.newAuth()
  const fresh = f.manager.acquire(101); assert.equal(f.manager.read(fresh), null)
  assert.equal(f.manager.inspect().count, 0); assert.equal(f.disk.clearCalls, 0); f.manager.dispose()
})
await test("legacy draft migration is limited to an existing session; fresh login never adopts old legacy records", () => {
  const f = fixture(), key = draftKey(credentials.server, 1, 101)
  storeDraft(f.disk, key, "legacy unsynced", "server")
  assert.equal(f.manager.read(f.manager.acquire(101)).note, "legacy unsynced")
  f.manager.end(); storeDraft(f.disk, key, "physically old legacy", "server"); f.newAuth()
  assert.equal(f.manager.read(f.manager.acquire(101)), null); f.manager.dispose()
})
await test("data reset invalidates leases before reset and resumes only after verified identity", () => {
  const f = fixture(), old = f.manager.acquire(101); f.manager.store(old, "retired by data reset", "server")
  f.manager.end(); f.data.set({ currentUser: null, identityAuthSessionKey: "", sessionRevision: 8 }); f.manager.resumeData()
  assert.equal(f.manager.acquire(101), null); assert.equal(f.manager.isCurrentLease(old), false)
  f.data.set({ ...f.data.get(), currentUser: { id: 1 }, identityAuthSessionKey: authKey(f.auth.get()) })
  assert.notEqual(f.manager.acquire(101), null); f.manager.dispose()
})

await test("legacy first refresh without verified identity reports incomplete cleanup and never purges other owners",()=>{
 const f=fixture({owner:null}),a=draftKey(credentials.server,1,101),b=draftKey(credentials.server,1,102),other=draftKey(credentials.server,2,101)
 for(const key of [a,b,other])storeDraft(f.disk,key,"legacy closed secret","server")
 const before=[...f.disk.map],snapshot=f.manager.inspect()
 assert.equal(snapshot.scopeKnown,false);assert.equal(snapshot.storageOK,false);assert.equal(snapshot.reason,"unverified_owner");assert.equal(f.manager.exportText(f.manager.context()),null)
 const result=f.manager.end();assert.equal(result.ok,false);assert.equal(result.scopeKnown,false);assert.equal(result.removed,0);assert.deepEqual([...f.disk.map],before)
 f.newAuth();assert.equal(f.manager.read(f.manager.acquire(101)),null);assert.equal(f.disk.clearCalls,0);f.manager.dispose()
})
await test("cancelled legacy logout can safely migrate after identity verifies and only delete that owner scope",()=>{
 const f=fixture({owner:null}),a=draftKey(credentials.server,1,101),other=draftKey(credentials.server,2,101)
 storeDraft(f.disk,a,"legacy own draft","server");storeDraft(f.disk,other,"other owner draft","server")
 const stamp=f.manager.requestStamp();f.data.set({...f.data.get(),currentUser:{id:1},identityAuthSessionKey:authKey(f.auth.get())})
 assert.equal(f.manager.requestStamp(),stamp);assert.equal(f.manager.inspect().scopeKnown,true);assert.equal(f.manager.read(f.manager.acquire(101)).note,"legacy own draft")
 assert.equal(f.manager.end().ok,true);assert.equal(f.disk.getItem(a),null);assert.notEqual(f.disk.getItem(other),null);f.manager.dispose()
})

await test("silent storage deletion refusal is verified and reported without reviving retired writers",()=>{
 const reports=[],f=fixture({onRetire:value=>reports.push(value)}),old=f.manager.acquire(101)
 f.manager.store(old,"retained plaintext","server");f.disk.removeItem=()=>{}
 const result=f.manager.end();assert.equal(result.ok,false);assert.equal(reports.length,1);assert.equal(reports[0].ok,false);assert.equal(f.manager.isCurrentLease(old),false)
 assert.equal(readDraft(f.disk,old.key).note,"retained plaintext");f.manager.dispose()
})
