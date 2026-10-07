// Direct real React + native HTML parser tests with fake HTTP, using CI's
// existing dependencies. No full reader harness, browser launch or model calls.
import assert from 'node:assert/strict'
import Module, {createRequire} from 'node:module'
import {join} from 'node:path'
import {fileURLToPath} from 'node:url'
import test from 'node:test'

const local = process.env.READER_COMPONENT_TOOLS
const web = createRequire(local ? join(local, 'package.json') : new URL('../upstream/reactflux/package.json', import.meta.url))
const {build} = local ? web('esbuild') : createRequire(web.resolve('vite'))('esbuild')
const {JSDOM} = local ? web('jsdom') : createRequire(new URL('../runtime/history-test-tools/package.json', import.meta.url))('jsdom')
const dom = new JSDOM('<!doctype html><main id="root"></main>', {url: 'https://reader.test/inbox/'})
Object.assign(globalThis, {window: dom.window, document: dom.window.document, IS_REACT_ACT_ENVIRONMENT: true})
Object.defineProperty(globalThis, 'navigator', {value: dom.window.navigator, configurable: true})
const React = web('react'), {act} = React, {createRoot} = web('react-dom/client')
const parser = web('html-react-parser'), parse = parser.default ?? parser
const fixtures = {
  '@/apis/ofetch': 'export default {get: (...args) => globalThis.bilingualRequest("GET", ...args), post: (...args) => globalThis.bilingualRequest("POST", ...args)}',
  '@nanostores/react': 'export const useStore = store => store.value',
  '@/store/settingsState': 'export const articleDetailSettingsState = {value:{fontSize:1,articleLineHeight:1.8,articleWidth:70}}; export const getDefaultSettings = () => articleDetailSettingsState.value; export const updateSettings = () => {}',
  '@/hooks/useScreenWidth': 'export default () => ({isBelowMedium:false})',
}
async function compile(relative) {
  const file = fileURLToPath(new URL(relative, import.meta.url))
  const result = await build({entryPoints: [file], write: false, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic',
    plugins: [{name: 'reader-fake-http', setup(builder) {
      builder.onResolve({filter: /^react(?:\/.*)?$/}, ({path}) => ({path: web.resolve(path), external: true}))
      builder.onResolve({filter: /^(@\/|@nanostores\/)/}, ({path}) => path in fixtures ? {path, namespace: 'fixture'} : undefined)
      builder.onResolve({filter: /\.css$/}, () => ({path: 'styles', namespace: 'fixture'}))
      builder.onLoad({filter: /.*/, namespace: 'fixture'}, ({path}) => ({contents: fixtures[path] || '', loader: 'js'}))
    }}]})
  const module = new Module(file)
  module._compile(result.outputFiles[0].text, file)
  return module.exports
}

const requests = [], timers = new Map()
globalThis.bilingualRequest = (method, path, bodyOrOptions, options) => new Promise((resolve, reject) => {
  requests.push({method, path, body: method === 'POST' ? bodyOrOptions : undefined,
    options: method === 'POST' ? options : bodyOrOptions, resolve, reject})
})
const {default: BilingualReading, getBilingualReading, useBilingualTranslation, TRANSLATION_POLL_MS} = await compile('../patches/BilingualReading.jsx')
const {default: ReadingControls} = await compile('../frontend-review/after/src/components/Ai/ReadingControls.jsx')
assert.equal(requests.length, 0, 'loading/preloading the view module cannot demand translation')
const originalSetTimeout = globalThis.setTimeout, originalClearTimeout = globalThis.clearTimeout
globalThis.setTimeout = (callback, delay, ...args) => {
  if (delay !== TRANSLATION_POLL_MS) return originalSetTimeout(callback, delay, ...args)
  const id = Symbol('translation timer'); timers.set(id, callback); return id
}
globalThis.clearTimeout = id => timers.has(id) ? timers.delete(id) : originalClearTimeout(id)
const signed = '/mf/proxy/' + 'A'.repeat(43) + '=/aHR0cHM6Ly9leGFtcGxlLm9yZy9pbWFnZS5qcGc='
const content = `<h2 id="intro">Original heading</h2><p>This is the English article.</p><img src="${signed}"><pre><code>sample()</code></pre>`
const translation = {status: 'done', source_hash: 'hash-a', blocks_total: 2, blocks_done: 2,
  bilingual_html: `<p><span class="reader-translation-target">中文段落。</span><span class="reader-translation-original">This is the English article.</span></p><img src="${signed}"><pre><code>sample()</code></pre>`,
  chinese_html: `<p>中文段落。</p><img src="${signed}"><pre><code>sample()</code></pre>`}
const entry = {id: 101, content, ai: {state: 'done', score: 8}}
function View({entry}) {
  const [mode, setMode] = React.useState('bilingual')
  const latest = useBilingualTranslation(entry)
  const reading = getBilingualReading({...entry, translation: latest}, mode)
  return React.createElement('article', {className: 'article-content'},
    React.createElement(ReadingControls, {scrollContainerRef: {current: null}},
      React.createElement(BilingualReading, {mode: reading.mode, message: reading.message, onModeChange: setMode})),
    React.createElement('div', {className: 'article-body'}, parse(reading.html)),
    React.createElement('textarea', {'aria-label': '笔记', defaultValue: 'Keep this note'}))
}
const root = createRoot(document.querySelector('#root'))
const render = value => act(async () => root.render(value ? React.createElement(View, {entry: value}) : null))
const body = () => document.querySelector('.article-body')
const popup = () => document.querySelector('.reader-bilingual-controls')
const button = label => [...document.querySelectorAll('.reader-bilingual-options button')].find(node => node.textContent === label)
const reply = (request, value, id = 101) => act(async () => request.resolve({entry_id: id, translation: value}))
async function tick() {
  assert.equal(timers.size, 1)
  const [id, callback] = timers.entries().next().value
  timers.delete(id)
  await act(async () => { void callback() })
  return requests.at(-1)
}

try {
  await test('one native toolbar has layout, language and focus icons; three modes and Escape preserve reading state', async () => {
    await render({...entry, translation})
    assert.equal(requests.length, 0, 'done detail cache is reused with no POST')
    assert.equal(document.querySelectorAll('.review-reading-bar').length, 1)
    assert.ok(document.querySelector('summary[aria-label="阅读排版"]'))
    assert.ok(document.querySelector('button[aria-label="专注正文"]'))
    assert.equal(popup().querySelector('summary').getAttribute('aria-label'), '正文语言：中英对照')
    assert.ok(body().textContent.indexOf('中文段落') < body().textContent.indexOf('This is the English article.'))
    for (const label of ['中文', '原文', '中英对照', '原文', '中文']) {
      popup().open = true
      await act(async () => button(label).click())
      assert.equal(popup().open, false)
      assert.equal(document.activeElement, popup().querySelector('summary'))
      assert.equal(button(label).getAttribute('aria-pressed'), 'true')
      assert.equal(body().querySelectorAll('img').length, 1)
      assert.equal(body().querySelector('img').getAttribute('src'), signed)
      assert.equal(body().querySelectorAll('pre code').length, 1)
    }
    assert.doesNotMatch(body().textContent, /This is the English article/)
    assert.equal(document.querySelector('textarea').value, 'Keep this note')
    let escaped = 0
    const onEscape = () => escaped++
    document.addEventListener('keydown', onEscape)
    popup().open = true
    await act(async () => popup().querySelector('summary').dispatchEvent(new dom.window.KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true})))
    document.removeEventListener('keydown', onEscape)
    assert.equal(popup().open, false)
    assert.equal(escaped, 0, 'language Escape does not close the article')
    assert.equal(requests.length, 0, 'mode switches never cause model requests')
    await render(null)
  })

  await test('mount shows the original immediately, demand response binds hash, GET adds partial paragraphs and done stops', async () => {
    await render(entry)
    const post = requests.at(-1)
    assert.equal(post.method, 'POST')
    assert.equal(post.path, '/v1/ai/translation/101')
    assert.deepEqual(post.body, {})
    assert.equal(post.options.retry, 0)
    assert.equal(post.options.timeout, 8000)
    assert.match(body().textContent, /This is the English article/)
    assert.equal(document.querySelector('[aria-busy="true"]'), null)
    assert.equal(document.querySelector('textarea').disabled, false)
    await reply(post, {status: 'pending', source_hash: 'hash-a'})
    const get = await tick()
    assert.equal(get.method, 'GET')
    assert.equal(get.path, post.path)
    await reply(get, {...translation, status: 'partial', blocks_done: 1})
    assert.match(body().textContent, /中文段落/)
    assert.equal(document.querySelector('.reader-bilingual-status').textContent, '部分段落已译')
    assert.equal(body().querySelector('img').getAttribute('src'), signed)
    await reply(await tick(), translation)
    assert.equal(timers.size, 0)
    assert.equal(document.querySelector('.reader-bilingual-status'), null)
    await render(null)
  })

  await test('ineligible and deferred mounted entries never POST', async () => {
    const count = requests.length
    for (const item of [{...entry, content_deferred: true}, {...entry, ai: {state: 'done', score: 7}},
      {...entry, ai: {state: 'pending', score: 9}}, {...entry, content: '<p>中文正文不需要翻译。</p>'}]) await render(item)
    assert.equal(requests.length, count)
    await render(null)
  })

  await test('navigation/unmount aborts POST and polling; old or changed-source HTML never reaches the new view', async () => {
    await render(entry)
    const oldPost = requests.at(-1)
    const next = {...entry, id: 202, content: '<p>This is the next English article.</p>'}
    await render(next)
    assert.equal(oldPost.options.signal.aborted, true)
    const newPost = requests.at(-1)
    await reply(oldPost, translation)
    assert.equal(body().textContent, 'This is the next English article.')
    await reply(newPost, {status: 'pending', source_hash: 'hash-b'}, 202)
    const get = await tick()
    await reply(get, translation, 202)
    assert.equal(body().textContent, 'This is the next English article.')
    assert.equal(timers.size, 0, 'wrong source hash stops polling')
    await render(null)
    await render(next)
    const pending = requests.at(-1)
    await reply(pending, {status: 'pending', source_hash: 'hash-b'}, 202)
    const pendingGet = await tick()
    await render(null)
    assert.equal(pendingGet.options.signal.aborted, true)
    await reply(pendingGet, {...translation, source_hash: 'hash-b'}, 202)
    assert.equal(document.querySelector('.article-body'), null)
    assert.equal(timers.size, 0)
  })
} finally {
  await act(async () => root.unmount())
  globalThis.setTimeout = originalSetTimeout
  globalThis.clearTimeout = originalClearTimeout
  delete globalThis.bilingualRequest
  dom.window.close()
}
