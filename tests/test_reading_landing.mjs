import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'

const source = (await readFile(new URL('../patches/reading-landing.js', import.meta.url), 'utf8')).replace(/^import .*$/gm, '')
const prefix = `
const store = initial => ({ value: initial, get() { return this.value }, set(value) { this.value = value } })
const savedStore = store({ entries: {} })
const persistentJSON = () => savedStore
export const aiState = store({ mode: 'recommended', auxiliary: 'none', minimum: 8, sort: 'score', direction: 'desc' })
export const contentState = store({ infoFrom: 'all', infoId: null, filterDate: null, filterString: '' })
const getHomeIdentityKey = identity => identity ? identity.server + ':' + identity.id : null
const settingsState = store({showStatus: 'unread', orderBy: 'published_at', orderDirection: 'desc'})
const updateSettings = values => settingsState.set({...settingsState.get(), ...values})
const setFilterDate = value => contentState.set({ ...contentState.get(), filterDate: value })
const setFilterString = value => contentState.set({ ...contentState.get(), filterString: value })
`
const app = await import('data:text/javascript;base64,' + Buffer.from(prefix + source).toString('base64'))
test('root restoration preserves the last scope, lens and filters per account without saving detail routes', () => {
  const first = { server: 'https://reader.test', id: 1 }, second = { ...first, id: 2 }
  assert.equal(app.lastReading(first), null)
  app.contentState.set({ infoFrom: 'all', infoId: null, filterDate: '2026-10-22|week', filterString: 'motor' })
  app.rememberReading(first, '/all/entry/7')
  app.contentState.set({ infoFrom: 'today', infoId: null, filterDate: null, filterString: '' })
  app.aiState.set({ ...app.aiState.get(), mode: 'all' })
  app.rememberReading(second, '/today')
  app.restoreReading(first)
  assert.equal(app.aiState.get().mode, 'recommended')
  assert.equal(app.contentState.get().filterDate, '2026-10-22|week')
  assert.equal(app.contentState.get().filterString, 'motor')
  assert.deepEqual(app.lastReading(first).target, { type: 'view', id: 'all' })
  assert.deepEqual(app.lastReading(second).target, { type: 'view', id: 'today' })
  app.rememberReading(first, '/feed/3')
  assert.deepEqual(app.lastReading(first).target, { type: 'view', id: 'all' }, 'uncommitted old scope cannot overwrite the new scope')
  app.rememberReading(first, '/')
  assert.deepEqual(app.lastReading(first).target, { type: 'view', id: 'all' })
})
