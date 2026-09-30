// Lifecycle QA without live services. Requires the pinned reader's dependencies
// plus jsdom: npm install --prefix runtime/history-test-tools --ignore-scripts jsdom@26.1.0
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {mkdtemp, rm} from 'node:fs/promises'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import test from 'node:test'

const webRequire = createRequire(new URL('../upstream/reactflux/package.json', import.meta.url))
const {build} = createRequire(webRequire.resolve('vite'))('esbuild')
const {JSDOM} = createRequire(new URL('../runtime/history-test-tools/package.json', import.meta.url))('jsdom')
const React = webRequire('react')
const {createRoot} = webRequire('react-dom/client')
const {act} = React
const directory = await mkdtemp(join(tmpdir(), 'inbox-history-component-'))
const output = join(directory, 'component.cjs')
await build({
  entryPoints: [new URL('../patches/SourceHistory.jsx', import.meta.url).pathname], outfile: output,
  bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic',
  plugins: [{name:'isolated-dependencies', setup(builder) {
    builder.onResolve({filter:/^react(?:\/.*)?$/}, ({path}) => ({path:webRequire.resolve(path), external:true}))
    builder.onResolve({filter:/^@\/apis\/ofetch$/}, () => ({path:'mock-api', namespace:'fixture'}))
    builder.onLoad({filter:/.*/, namespace:'fixture'}, () => ({contents:'export default {get: (...args) => globalThis.historyApi(...args)}', loader:'js'}))
  }}],
})
const SourceHistory = createRequire(import.meta.url)(output).default

const fixture = {stored:{state:'ok',count:12}, feed_window:{state:'ok',count:2,dated_count:0}}
await test('history expansion, repeated requests, error recovery and close/remount', async () => {
  const dom = new JSDOM('<!doctype html><div id="root"></div>', {url:'http://example.test/'})
  Object.assign(globalThis, {window:dom.window, document:dom.window.document, IS_REACT_ACT_ENVIRONMENT:true})
  const requests = []
  globalThis.historyApi = (url, options) => new Promise((resolve,reject) => requests.push({url,options,resolve,reject}))
  const root = createRoot(document.querySelector('#root'))
  const render = () => React.createElement(SourceHistory, {feedId:7})
  await act(async () => root.render(render()))
  assert.equal(requests.length, 0, 'no request until expansion')
  const toggle = () => {
    const details=document.querySelector('details')
    details.open=true
    details.dispatchEvent(new dom.window.Event('toggle'))
  }
  await act(async () => { toggle(); toggle() })
  assert.equal(requests.length,1)
  assert.equal(requests[0].url, '/v1/ai/feeds/7/history')
  assert.equal(document.querySelector('button').disabled,true)
  await act(async () => requests[0].resolve(fixture))
  assert.match(document.body.textContent,/已存储 12 条/)
  assert.match(document.body.textContent,/RSS\/Atom 本次暴露 2 条/)
  await act(async () => toggle())
  assert.equal(requests.length,1,'open state already has result')
  await act(async () => {
    document.querySelector('button').click()
    document.querySelector('button').dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true}))
  })
  assert.equal(requests.length,2,'in-flight refresh is de-duplicated')
  assert.doesNotMatch(document.body.textContent,/已存储 12 条/,'stale successful values cleared during refresh')
  await act(async () => requests[1].reject(new Error('fixture failure')))
  assert.match(document.body.textContent,/查询失败，请重试/)
  assert.equal(document.querySelector('button').disabled,false)
  await act(async () => document.querySelector('button').click())
  await act(async () => requests[2].resolve(fixture))
  assert.doesNotMatch(document.body.textContent,/查询失败，请重试/)
  await act(async () => document.querySelector('button').click())
  const pending=requests[3]
  await act(async () => root.render(null))
  assert.equal(pending.options.signal.aborted,true,'closing aborts pending network work')
  await act(async () => root.render(render()))
  await act(async () => pending.resolve(fixture))
  assert.doesNotMatch(document.body.textContent,/已存储 12 条/,'old response cannot fill new panel')
  await act(async () => toggle())
  await act(async () => requests[4].resolve(fixture))
  assert.match(document.body.textContent,/已存储 12 条/)
  await act(async () => root.unmount())
  dom.window.close()
})
await rm(directory,{recursive:true,force:true})
