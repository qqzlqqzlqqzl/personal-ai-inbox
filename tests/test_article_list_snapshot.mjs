import assert from "node:assert/strict"
import { registerHooks } from "node:module"
import test from "node:test"

import {
  articleSnapshotQuery, createArticleSnapshotStore, isShowingArticleSnapshot,
  MAX_SNAPSHOT_BYTES, MAX_SNAPSHOT_ENTRIES, SNAPSHOT_MAX_AGE, snapshotOwnerKey, snapshotSwapAnchor,
} from "../frontend-review/after/src/utils/article-list-snapshot.js"
import { preloadReaderIdentity, takeReaderIdentity } from "../frontend-review/after/src/utils/reader-startup-identity.js"
import { createHomeIdentity } from "../upstream/reactflux/src/utils/home-page.js"

const owner = (server, id) => snapshotOwnerKey(createHomeIdentity(server, id))
const main = owner("https://reader.example/mf", 7)
const alternate = owner("https://reader.example/mf", 8)
const setupQuery = {
  content: { filterString: "", filterDate: null },
  settings: { showStatus: "unread", orderBy: "created_at", orderDirection: "desc",
    pageSize: 100, showHiddenFeeds: false, removeDuplicates: "none" },
  info: { from: "all", id: "" },
  ai: { ai_view: "recommended", ai_sort: "fresh", direction: "desc", limit: 24 },
  calendar: { day: "2026-11-28", zone: "Asia/Singapore" },
}
const defaultQuery = articleSnapshotQuery(setupQuery)
const queryFor = (search) => articleSnapshotQuery({ ...setupQuery, content: { filterString: search } })
const page = { entries: [{ id: 41, title: "Article", feed: { id: 5, title: "Feed" } }],
  total: 120, offset: 24, hasMore: true, aiRevision: "a".repeat(64) }

function makeMemoryStore() {
  const rows = new Map()
  return {
    rows,
    read: ownerKey => rows.get(ownerKey),
    update(ownerKey, update) {
      const result = update(rows.get(ownerKey))
      if (result) rows.set(ownerKey, result)
      else rows.delete(ownerKey)
    },
  }
}

test("keys use canonical servers, verified ids and only query-relevant fields", () => {
  assert.equal(main, owner("https://READER.example:443/mf///?token=secret#hash", 7))
  assert.notEqual(main, owner("https://reader.example/other", 7))
  assert.notEqual(main, alternate)
  assert.equal(owner("https://reader.example/mf", null), null)
  assert.equal(defaultQuery.isDefault, true)
  assert.equal(queryFor("motors").isDefault, false)
  assert.equal(defaultQuery.signature, articleSnapshotQuery({
    ...setupQuery, token: "secret",
    content: { ...setupQuery.content, articleListRevision: 3 },
    settings: { ...setupQuery.settings, password: "secret" },
    ai: { ...setupQuery.ai, clientSecret: "secret" },
    calendar: { ...setupQuery.calendar, key: "includes live auth material" },
  }).signature)
  assert.doesNotMatch(defaultQuery.signature, /secret|auth material/)
  for (const [part, changes] of [
    ["info", { from: "feed", id: "1" }], ["settings", { showStatus: "all" }],
    ["content", { filterString: "motors" }], ["content", { filterDate: "2026-11-20" }],
    ["settings", { orderDirection: "asc" }], ["settings", { showHiddenFeeds: true }],
    ["ai", { ai_min: 7 }], ["ai", { has_note: true }], ["ai", { ai_sort: "score" }],
    ["calendar", { day: "2026-11-29" }], ["calendar", { zone: "UTC" }],
  ]) {
    const changed = articleSnapshotQuery({ ...setupQuery, [part]: { ...setupQuery[part], ...changes } })
    assert.notEqual(changed.signature, defaultQuery.signature, part)
  }
})

test("stores the default and latest committed first pages for each owner", async () => {
  const storage = makeMemoryStore()
  const store = createArticleSnapshotStore(storage)
  const cam = queryFor("camera")
  const motor = queryFor("motor")
  await store.save(main, defaultQuery, page)
  await store.save(main, cam, page)
  await store.save(main, motor, page)
  await store.save(alternate, cam, { ...page, total: 1 })
  assert.equal(await store.read(main, cam.signature), null)
  assert.equal((await store.read(main, motor.signature)).total, 120)
  const cached = await store.read(main, defaultQuery.signature)
  assert.deepEqual([cached.total, cached.offset, cached.hasMore, cached.aiRevision], [120, 24, true, "a".repeat(64)])
  assert.equal((await store.read(alternate, cam.signature)).total, 1)
  assert.equal(await store.read(alternate, defaultQuery.signature), null)
  assert.equal(await store.read(null, cam.signature), null)
  storage.rows.set(alternate, storage.rows.get(main))
  assert.equal(await store.read(alternate, defaultQuery.signature), null, "confirm the stored owner as well as the lookup key")
  await store.save(main, defaultQuery, page)
  assert.equal(await store.read(main, motor.signature), null)
})

test("expiry is measured from the live commit and storage errors are silent", async () => {
  let now = 1
  const store = createArticleSnapshotStore(makeMemoryStore(), () => now)
  await store.save(main, defaultQuery, page)
  now = SNAPSHOT_MAX_AGE
  assert.equal((await store.read(main, defaultQuery.signature)).savedAt, 1)
  now += 1
  assert.equal(await store.read(main, defaultQuery.signature), null)
  const fail = () => { throw new Error("storage denied") }
  const broken = createArticleSnapshotStore({ read: fail, update: fail })
  assert.equal(await broken.read(main, defaultQuery.signature), null)
  await broken.save(main, defaultQuery, page)
  await broken.removeOwner(main)
})

test("bounds row count and encoded size while retaining the fields needed by cards", async () => {
  const store = createArticleSnapshotStore(makeMemoryStore())
  const entry = { id: 1, title: "Page", author: "Author", url: "https://article.example/",
    published_at: "2026-11-28", status: "unread", starred: true, content: "<p>Full text ".repeat(20000),
    coverSource: "/cover", coverWidth: 800, feed: { id: 2, title: "Feed", icon: { icon_id: 8 } },
    card: { title: "Card", summary: "Excerpt", body: "Unneeded" },
    enclosures: [{ id: 3, url: "/image", mime_type: "image/png" }],
    ai: { state: "done", score: 8, summary: "Summary", evidence: "Unneeded", processing: { claim_held: false } },
  }
  await store.save(main, defaultQuery, { ...page,
    entries: Array.from({ length: 90 }, (_, index) => ({ ...entry, id: index + 1 })) })
  const snapshot = await store.read(main, defaultQuery.signature)
  assert.equal(snapshot.entries.length, MAX_SNAPSHOT_ENTRIES)
  assert.equal(snapshot.entries[0].content, undefined)
  assert.equal(snapshot.entries[0].feed.icon.icon_id, 8)
  assert.deepEqual(snapshot.entries[0].card, { title: "Card", summary: "Excerpt" })
  assert.equal(snapshot.entries[0].ai.evidence, undefined)
  assert.equal(snapshot.entries[0].ai.score, 8)
  assert.equal(snapshot.entries[0].enclosures[0].url, "/image")
  assert.equal(snapshot.entries[0].coverWidth, 800)
  const large = { ...entry, title: "字".repeat(10000), url: "值".repeat(10000),
    enclosures: Array.from({ length: 5 }, () => ({ url: "字".repeat(10000) })) }
  await store.save(main, defaultQuery, { ...page, entries: Array(60).fill(large) })
  const bounded = await store.read(main, defaultQuery.signature)
  assert.ok(bounded.entries.length < 60)
  assert.ok(Buffer.byteLength(JSON.stringify(bounded)) <= MAX_SNAPSHOT_BYTES)
})

test("cached rows gate actions in both phases and live swaps preserve signed offsets", () => {
  const refreshing = { status: "refreshing" }
  const offline = { status: "offline" }
  assert.equal(isShowingArticleSnapshot({ articleListCache: refreshing, isArticleListReady: false }), true)
  assert.equal(isShowingArticleSnapshot({ articleListCache: offline, isArticleListReady: false }), true)
  assert.equal(isShowingArticleSnapshot({ articleListCache: null, isArticleListReady: true }), false)
  const entries = [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }, { id: 5 }]
  assert.equal(snapshotSwapAnchor(refreshing, false, null, null, entries, 1), null)
  assert.equal(snapshotSwapAnchor(refreshing, false, offline, null, entries, 1), null)
  assert.deepEqual(snapshotSwapAnchor(offline, true, null, { entryId: 4, offset: -31 }, entries, 2),
    { index: 1, offset: 31 })
  assert.deepEqual(snapshotSwapAnchor(refreshing, true, null, { entryId: 3, offset: 7 }, entries, 1),
    { index: 2, offset: -7 })
  assert.deepEqual(snapshotSwapAnchor(refreshing, true, null, { entryId: 7, offset: 40 }, entries, 1),
    { index: 0, offset: 0 })
})

test("logout removes both slots for the outgoing owner and invalidates outstanding reads and writes", async () => {
  const storage = makeMemoryStore()
  const store = createArticleSnapshotStore(storage)
  const motor = queryFor("motor")
  await store.save(main, defaultQuery, page)
  await store.save(main, motor, page)
  await store.save(alternate, defaultQuery, page)
  let finishRead
  const pendingRow = storage.rows.get(main)
  storage.read = (key) => key === main
    ? new Promise(resolve => { finishRead = () => resolve(pendingRow) })
    : storage.rows.get(key)
  const pendingRead = store.read(main, motor.signature)
  const update = storage.update
  let finishSave
  storage.update = (key, fn) => new Promise(resolve => {
    finishSave = () => { update.call(storage, key, fn); resolve() }
  })
  const pendingSave = store.save(main, motor, page)
  storage.update = update
  await store.removeOwner(main)
  finishRead()
  finishSave()
  await pendingSave
  assert.equal(await pendingRead, null)
  assert.equal(storage.rows.has(main), false)
  assert.equal((await store.read(alternate, defaultQuery.signature)).total, page.total)
})

test("startup hands the in-flight identity request only to the matching auth session", async () => {
  const oldRequest = Promise.reject(new Error("version gate stayed closed"))
  const discard = preloadReaderIdentity("old", 1, oldRequest)
  assert.equal(takeReaderIdentity("old", 2), null)
  discard()
  assert.equal(takeReaderIdentity("old", 1), null)
  const request = Promise.resolve({ id: 7 })
  preloadReaderIdentity("current", 2, request)
  discard()
  assert.equal(takeReaderIdentity("other", 2), null)
  assert.equal(takeReaderIdentity("current", 2), request)
  assert.deepEqual(await request, { id: 7 })
  assert.equal(takeReaderIdentity("current", 2), null)
})

test("actual logout and current-session 401 wait for scoped deletion before resetting auth", async () => {
  const storage = makeMemoryStore()
  const store = createArticleSnapshotStore(storage)
  await store.save(alternate, defaultQuery, page)
  const initialAuth = { server: "https://reader.example/mf", token: "synthetic" }
  const authKey = auth => JSON.stringify([auth.server, auth.token])
  const fixture = {
    auth: initialAuth, revision: 1, stamp: 1, user: { id: 7 }, authKey, events: [],
  }
  let deletion = Promise.withResolvers()
  fixture.removeOwner = async (key) => {
    await deletion.promise
    await store.removeOwner(key)
    fixture.events.push("delete owner")
  }
  globalThis.articleSnapshotSessionFixture = fixture
  const stub = {
    "@/store/authState": `export const authState={get:()=>fixture.auth}
      export const resetAuth=()=>{fixture.events.push("reset auth");fixture.auth={}}
      export const setAuth=auth=>{fixture.auth=auth}`,
    "@/store/contentState": `export const resetContent=()=>fixture.events.push("reset content")`,
    "@/store/dataState": `export const dataState={get:()=>({currentUser:fixture.user,identityAuthSessionKey:fixture.user?fixture.authKey(fixture.auth):""})}
      export const getDataSessionRevision=()=>fixture.revision
      export const resetData=()=>{fixture.revision++;fixture.user=null}
      export const commitIdentityData=()=>{}
      export const setVerifiedServer=()=>{}`,
    "@/store/feedIconsState": `export const resetFeedIcons=()=>{}`,
    "@/utils/article-list-snapshot": `export const articleSnapshots={removeOwner:fixture.removeOwner}
      export {snapshotOwnerKey} from ${JSON.stringify(new URL("../frontend-review/after/src/utils/article-list-snapshot.js", import.meta.url))}`,
    "@/utils/auth": `export const getAuthSessionKey=fixture.authKey;export default auth=>Boolean(auth.token)`,
    "@/utils/home-page": `export {createHomeIdentity} from ${JSON.stringify(new URL("../upstream/reactflux/src/utils/home-page.js", import.meta.url))}`,
    "@/utils/note-session": `export const getNoteRequestStamp=()=>fixture.stamp
      export const createNoteSessionAuth=auth=>auth
      export const noteSession={end(expected){if(expected&&expected!==fixture.stamp) return {retired:false};fixture.stamp++;return {retired:true}}}`,
    "@/routes": `export default {state:{location:{pathname:"/all",search:"",hash:""}},navigate:()=>fixture.events.push("navigate")}`,
    ofetch: `export const ofetch={create(hooks){fixture.hooks=hooks;return ()=>{}}}`,
  }
  const sessionUrl = new URL("../frontend-review/after/src/utils/session.js", import.meta.url)
  const hook = registerHooks({
    resolve(specifier, context, nextResolve) {
      if (specifier === "@/utils/session") return { url: sessionUrl.href, shortCircuit: true }
      if (!Object.hasOwn(stub, specifier)) return nextResolve(specifier, context)
      const source = "const fixture = globalThis.articleSnapshotSessionFixture;\n" + stub[specifier]
      return { url: "data:text/javascript," + encodeURIComponent(source), shortCircuit: true }
    },
  })
  try {
    const { clearSession } = await import(sessionUrl)
    await import("../frontend-review/after/src/apis/ofetch.js")
    await store.save(main, defaultQuery, page)
    const logout = clearSession({ expected: fixture.stamp })
    assert.equal(fixture.auth, initialAuth)
    assert.deepEqual(fixture.events, [])
    deletion.resolve()
    assert.equal((await logout).retired, true)
    assert.deepEqual(fixture.events, ["delete owner", "reset auth", "reset content"])
    assert.equal(await store.read(main, defaultQuery.signature), null)

    fixture.auth = { ...initialAuth, readerNoteSession: { version: 1, server: initialAuth.server, owner: "7" } }
    fixture.events = []
    await store.save(main, defaultQuery, page)
    deletion = Promise.withResolvers()
    const response = { status: 401, statusText: "Unauthorized", _data: {} }
    const deniedWhileUnsupported = { startupCompatibility: Promise.resolve(false) }
    fixture.hooks.onRequest({ options: deniedWhileUnsupported })
    await assert.rejects(fixture.hooks.onResponseError({ response, options: deniedWhileUnsupported }))
    assert.deepEqual(fixture.events, [])
    const options = { startupCompatibility: Promise.resolve(true) }
    fixture.hooks.onRequest({ options })
    const unauthorized = fixture.hooks.onResponseError({ response, options })
    assert.ok(fixture.auth.token)
    deletion.resolve()
    await assert.rejects(unauthorized)
    assert.deepEqual(fixture.events, ["delete owner", "reset auth", "reset content", "navigate"])
    assert.equal(await store.read(main, defaultQuery.signature), null)
    assert.equal((await store.read(alternate, defaultQuery.signature)).total, page.total)
  } finally {
    hook.deregister()
    delete globalThis.articleSnapshotSessionFixture
  }
})
