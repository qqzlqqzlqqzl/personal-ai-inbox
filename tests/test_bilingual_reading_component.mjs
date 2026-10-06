// Real React controls and the native HTML parser; uses existing CI dependencies.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdtemp } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import test from 'node:test'
import { retainTestDirectory } from './retain_test_directory.mjs'

const web = createRequire(new URL('../upstream/reactflux/package.json', import.meta.url))
const { build } = createRequire(web.resolve('vite'))('esbuild')
const { JSDOM } = createRequire(new URL('../runtime/history-test-tools/package.json', import.meta.url))('jsdom')
const dom = new JSDOM('<!doctype html><main id="root"></main>', { url: 'https://reader.test/' })
Object.assign(globalThis, { window: dom.window, document: dom.window.document, IS_REACT_ACT_ENVIRONMENT: true })
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
const React = web('react'), { act } = React, { createRoot } = web('react-dom/client')
const parseModule = web('html-react-parser'), parse = parseModule.default ?? parseModule
const directory = await mkdtemp(join(tmpdir(), 'reader-bilingual-component-'))
const output = join(directory, 'bilingual.cjs')
await build({
  entryPoints: [new URL('../patches/BilingualReading.jsx', import.meta.url).pathname],
  outfile: output, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic',
  plugins: [{ name: 'existing-reader-dependencies', setup(builder) {
    builder.onResolve({ filter: /^react(?:\/.*)?$/ }, ({ path }) => ({ path: web.resolve(path), external: true }))
    builder.onResolve({ filter: /^@\/apis\/ofetch$/ }, () => ({ path: 'api', namespace: 'fixture' }))
    builder.onResolve({ filter: /\.css$/ }, () => ({ path: 'styles', namespace: 'fixture' }))
    builder.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path }) => ({
      contents: path === 'api' ? 'export default { get: (...args) => globalThis.bilingualRequest(...args) }' : '', loader: 'js',
    }))
  } }],
})
const { default: BilingualReading, getBilingualReading, useBilingualTranslation } = createRequire(import.meta.url)(output)
const timers = new Map(), requests = []
const originalSetTimeout = globalThis.setTimeout, originalClearTimeout = globalThis.clearTimeout
globalThis.setTimeout = (callback, delay, ...args) => {
  if (delay !== 10000) return originalSetTimeout(callback, delay, ...args)
  const id = Symbol('translation timer')
  timers.set(id, callback)
  return id
}
globalThis.clearTimeout = id => timers.has(id) ? timers.delete(id) : originalClearTimeout(id)
globalThis.bilingualRequest = (path, options) => new Promise((resolve, reject) => requests.push({ path, options, resolve, reject }))
const tick = async () => {
  assert.equal(timers.size, 1)
  const [id, callback] = timers.entries().next().value
  timers.delete(id)
  await act(async () => { void callback() })
  return requests.at(-1)
}
const original = '<h2 id="intro">Original heading</h2><p>English paragraph.</p><img src="/picture.png"><pre><code>sample()</code></pre>'
const translation = {
  status: 'done', language: 'zh-CN',
  bilingual_html: '<h2 id="intro">简介</h2><p class="reader-translation-target">中文段落。</p><p class="reader-translation-original">English paragraph.</p><img src="/picture.png"><pre><code>sample()</code></pre>',
  chinese_html: '<h2 id="intro">简介</h2><p class="reader-translation-target">中文段落。</p><img src="/picture.png"><pre><code>sample()</code></pre>',
}
let setEntry
function View() {
  const [entry, update] = React.useState({ id: 101, content: original, translation })
  const [mode, setMode] = React.useState('bilingual')
  setEntry = update
  const latest = useBilingualTranslation(entry)
  const reading = getBilingualReading({ ...entry, translation: latest }, mode)
  return React.createElement('article', null,
    React.createElement(BilingualReading, { mode: reading.mode, message: reading.message, onModeChange: setMode }),
    React.createElement('div', { className: 'article-body' }, parse(reading.html)),
    React.createElement('textarea', { 'aria-label': '笔记', defaultValue: 'Keep this note' }),
  )
}
const root = createRoot(document.querySelector('#root'))
const button = label => [...document.querySelectorAll('button')].find(node => node.textContent === label)
const body = () => document.querySelector('.article-body')
const changeMode = async label => {
  button(label).focus()
  await act(async () => button(label).click())
  assert.equal(button(label).getAttribute('aria-pressed'), 'true')
  assert.equal(document.activeElement, button(label))
  assert.equal(document.querySelectorAll('button[aria-pressed="true"]').length, 1)
}

try {
  await act(async () => root.render(React.createElement(View)))
  await test('three accessible controls default to Chinese-first bilingual and keep one image/code block', async () => {
    assert.equal(document.querySelector('[role="group"]').getAttribute('aria-label'), '正文语言')
    assert.equal(button('双语').getAttribute('aria-pressed'), 'true')
    assert.ok(body().textContent.indexOf('中文段落') < body().textContent.indexOf('English paragraph'))
    for (let cycle = 0; cycle < 3; cycle++) {
      for (const label of ['仅中文', '原文', '双语']) {
        await changeMode(label)
        assert.equal(body().querySelectorAll('img').length, 1)
        assert.equal(body().querySelectorAll('pre code').length, 1)
        assert.ok(body().querySelector('#intro'))
      }
    }
    await changeMode('仅中文')
    assert.doesNotMatch(body().textContent, /English paragraph/)
    assert.equal(document.querySelector('textarea').value, 'Keep this note')
  })

  await test('pending/partial/error updates keep controls, original fallback and notes usable', async () => {
    for (const status of ['pending', 'error', 'disabled', 'budget_paused']) {
      await act(async () => setEntry({ id: 101, content: original, translation: { status, language: 'zh-CN' } }))
      assert.match(body().textContent, /English paragraph/)
      assert.equal(body().querySelectorAll('img').length, 1)
      assert.equal(document.querySelector('[aria-busy="true"]'), null)
      assert.equal(document.querySelector('button:disabled'), null)
      assert.equal(document.querySelector('textarea').disabled, false)
      assert.doesNotMatch(document.querySelector('[role="status"]').textContent, /模型|预算|mini|token/i)
      await changeMode('原文')
      assert.equal(document.querySelector('[role="status"]'), null)
      await changeMode('双语')
    }
    await act(async () => setEntry({ id: 101, content: original, translation: { ...translation, status: 'partial' } }))
    assert.equal(document.querySelector('[role="status"]').textContent, '部分段落已译')
    assert.match(body().textContent, /中文段落/)
    assert.equal(document.querySelector('textarea').value, 'Keep this note')
  })

  await test('navigation to an untranslated article cannot retain previous translated HTML', async () => {
    await act(async () => setEntry({ id: 202, content: '<h2>Next article</h2><p>Next body</p>' }))
    assert.match(body().textContent, /Next body/)
    assert.doesNotMatch(body().textContent, /中文段落|English paragraph/)
    await changeMode('仅中文')
    assert.match(body().textContent, /Next body/)
  })

  await test('lightweight authenticated polling cancels old article, applies matching hash and stops on done', async () => {
    await act(async () => setEntry({ id: 101, content: original, translation: { status: 'pending', source_hash: 'hash-a' } }))
    const oldRequest = await tick()
    assert.equal(oldRequest.path, '/v1/ai/translation/101')
    assert.equal(oldRequest.options.retry, 0)
    assert.equal(oldRequest.options.timeout, 8000)
    await act(async () => setEntry({ id: 202, content: '<p>Next body</p>', translation: { status: 'pending', source_hash: 'hash-b' } }))
    assert.equal(oldRequest.options.signal.aborted, true)
    await act(async () => oldRequest.resolve({ ...translation, source_hash: 'hash-a' }))
    assert.equal(body().textContent, 'Next body')
    const currentRequest = await tick()
    assert.equal(currentRequest.path, '/v1/ai/translation/202')
    await act(async () => currentRequest.resolve({ ...translation, source_hash: 'hash-b' }))
    assert.match(body().textContent, /中文段落/)
    assert.equal(timers.size, 0)
  })

  await test('a changed server source hash cannot replace this article body', async () => {
    await act(async () => setEntry({ id: 202, content: '<p>Current version</p>', translation: { status: 'partial', source_hash: 'current-hash' } }))
    const request = await tick()
    await act(async () => request.resolve({ ...translation, source_hash: 'another-version' }))
    assert.equal(body().textContent, 'Current version')
    assert.equal(timers.size, 0)
  })
} finally {
  await act(async () => root.unmount())
  globalThis.setTimeout = originalSetTimeout
  globalThis.clearTimeout = originalClearTimeout
  delete globalThis.bilingualRequest
  dom.window.close()
  await retainTestDirectory(directory)
}
