// Real generated route graph and React Router; business modules are no-network
// fixtures. This proves pending/settled DOM, not browser paint or authentication.
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { createRequire, Module } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const web = join(root, 'upstream/reactflux')
const webRequire = createRequire(join(web, 'package.json'))
const { build } = createRequire(webRequire.resolve('vite'))('esbuild')
const { JSDOM } = createRequire(join(root, 'runtime/history-test-tools/package.json'))('jsdom')
const generated = spawnSync(process.env.PYTHON || 'python3', ['-B', '-c', [
  'import json,sys',
  'from pathlib import Path',
  'sys.path.insert(0,"src")',
  'import install_agent_status as installer',
  'fixture=json.loads(Path("tests/fixtures/reader-startup-pinned-sources.json").read_text())',
  'base=fixture["files"]["src/routes.jsx"]["text"]',
  'print(json.dumps({"current":installer._routes_apply(base),"prior":installer._routes_apply_prior_fallback(base)}))',
].join('\n')], { cwd: root, encoding: 'utf8', timeout: 10000,
  env: { ...process.env, PYTHONDONTWRITEBYTECODE: '1' } })
assert.equal(generated.status, 0, generated.stderr || 'Startup route generation failed')
const routes = JSON.parse(generated.stdout)

const dom = new JSDOM('<!doctype html><div id="root"></div>', {
  url: 'http://synthetic.test/inbox/all', pretendToBeVisual: true,
})
Object.assign(globalThis, {
  window: dom.window, document: dom.window.document,
  HTMLElement: dom.window.HTMLElement, Node: dom.window.Node,
  IS_REACT_ACT_ENVIRONMENT: true,
})
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
const React = webRequire('react')
const { createRoot } = webRequire('react-dom/client')
const { createMemoryRouter } = webRequire('react-router')
const { RouterProvider } = webRequire('react-router/dom')
const warnings = []
const originalWarn = console.warn
console.warn = (...args) => { warnings.push(args.join(' ')) }

async function compileRoutes(source) {
  // Capture the exact generated route objects without creating a browser router.
  // The test then supplies those same objects to the real memory router.
  const anchor = 'import { createBrowserRouter } from "react-router"\n'
  assert.equal(source.split(anchor).length, 2)
  const captured = source.replace(anchor,
    'const createBrowserRouter = (routes, options) => ({ routes, options });\n')
  const result = await build({
    stdin: { contents: captured, resolveDir: join(web, 'src'), sourcefile: 'routes.jsx', loader: 'jsx' },
    bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
    define: { 'import.meta.env.BASE_URL': '"/inbox/"' },
    plugins: [{ name: 'startup-route-fixtures', setup(builder) {
      builder.onResolve({ filter: /^(react(?:\/.*)?|react-dom(?:\/.*)?|react-router(?:\/.*)?)$/ },
        ({ path }) => ({ path: webRequire.resolve(path), external: true }))
      builder.onResolve({ filter: /^\.\/(pages|components)\// },
        ({ path }) => ({ path, namespace: 'fixture' }))
      builder.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path }) => {
        const common = 'import React from "react"; import {Outlet} from "react-router"; ' +
          'const Page=()=>React.createElement("p",{"data-startup-ready":"content"},"内容已加载");'
        if (path.endsWith('/DeferredComponent'))
          return { contents: common + 'export default ()=>Page;', loader: 'js' }
        if (path.endsWith('/ContentPages'))
          return { contents: common + 'export const loadContentPages=()=>Promise.resolve(); ' +
            'export default {all:Page,today:Page,starred:Page,history:Page,category:Page,feed:Page};', loader: 'js' }
        if (path.endsWith('/Login'))
          return { contents: common + 'export default ()=>React.createElement("p",{"data-startup-ready":"login"},"登录已加载");', loader: 'js' }
        if (path.endsWith('/ErrorPage'))
          return { contents: common + 'export default ()=>React.createElement("p",{role:"alert"},"加载失败");', loader: 'js' }
        const failure = path.endsWith('/AuthenticatedApp')
          ? 'if(globalThis.__readerStartupRejectAuthenticated)throw new Error("synthetic authenticated chunk failure");'
          : ''
        return { contents: common + failure + 'export default ()=>React.createElement(Outlet);', loader: 'js' }
      })
    }}],
  })
  return result.outputFiles[0].text
}

async function bounded(promise) {
  let timer
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error('Startup router test deadline')), 5000)
    })])
  } finally { clearTimeout(timer) }
}

async function observe(bundle, source, mode, path, hold, reject = false) {
  const compiled = new Module(join(web, '__memory_startup_test__.cjs'))
  compiled.filename = join(web, '__memory_startup_test__.cjs')
  compiled.paths = Module._nodeModulePaths(web)
  compiled._compile(bundle, compiled.filename)
  const config = compiled.exports.default
  const parent = config.routes.find(route => route.lazy && route.children)
  const target = hold === 'login' ? config.routes.find(route => route.path === '/login')
    : hold === 'authenticated' ? parent.children.find(route => route.path === '/') : parent
  assert.equal(typeof target.lazy, 'function')
  const originalLazy = target.lazy
  let release, entered
  const gate = new Promise(resolve => { release = resolve })
  const started = new Promise(resolve => { entered = resolve })
  target.lazy = async (...args) => { entered(); await gate; return originalLazy(...args) }
  globalThis.__readerStartupRejectAuthenticated = reject
  const router = createMemoryRouter(config.routes, {
    basename: config.options.basename, initialEntries: [path],
  })
  const settled = new Promise(resolve => {
    if (router.state.initialized) return resolve()
    const unsubscribe = router.subscribe(state => {
      if (state.initialized) { unsubscribe(); resolve() }
    })
  })
  const element = document.querySelector('#root')
  const reactRoot = createRoot(element)
  const warningStart = warnings.length
  try {
    await React.act(async () => {
      reactRoot.render(React.createElement(RouterProvider, { router }))
      await bounded(started)
    })
    const pending = {
      initialized: router.state.initialized, children: element.childElementCount,
      text: element.textContent.trim(), statusCount: element.querySelectorAll('[role="status"]').length,
    }
    assert.equal(pending.initialized, false)
    assert.equal(element.querySelector('[data-startup-ready]'), null)
    if (mode === 'current') {
      assert.equal(pending.statusCount, 1)
      const status = element.querySelector('[role="status"]')
      assert.equal(status.getAttribute('aria-busy'), 'true')
      assert.equal(status.getAttribute('aria-live'), 'polite')
      assert.equal(status.textContent.trim(), '正在加载阅读器…')
    } else {
      assert.equal(pending.children, 0, 'Prior route graph is the real empty-root negative control')
      assert.equal(pending.text, '')
      assert.equal(pending.statusCount, 0)
    }
    await React.act(async () => { release(); await bounded(settled) })
    assert.equal(router.state.initialized, true)
    assert.equal(element.querySelector('[role="status"]'), null, 'Initial fallback must leave after settling')
    if (reject) {
      assert.equal(element.querySelector('[role="alert"]')?.textContent, '加载失败')
    } else {
      const expected = hold === 'login' ? 'login' : 'content'
      assert.equal(element.querySelector('[data-startup-ready]')?.getAttribute('data-startup-ready'), expected)
    }
    console.log(JSON.stringify({
      kind: 'actual_generated_routes_real_router_synthetic_modules', mode, path, hold, reject,
      sourceSHA: createHash('sha256').update(source).digest('hex'),
      pending, settled: { children: element.childElementCount, text: element.textContent.trim() },
      hydrationWarnings: warnings.slice(warningStart).filter(value => value.includes('HydrateFallback')).length,
    }))
  } finally {
    release()
    await React.act(async () => reactRoot.unmount())
    router.dispose()
    globalThis.__readerStartupRejectAuthenticated = false
  }
}

try {
  const cases = [
    ['/inbox/all', 'protect'], ['/inbox/all/entry/123', 'protect'],
    ['/inbox/all', 'authenticated'], ['/inbox/all/entry/123', 'authenticated'],
    ['/inbox/login', 'login'],
  ]
  for (const mode of ['prior', 'current']) {
    const bundle = await compileRoutes(routes[mode])
    for (const [path, hold] of cases) await observe(bundle, routes[mode], mode, path, hold)
    if (mode === 'current') await observe(bundle, routes[mode], mode, '/inbox/all', 'authenticated', true)
  }
  console.log('PASS: synchronous startup status covers pending auth/login routes and leaves on success or error')
} finally {
  console.warn = originalWarn
  delete globalThis.__readerStartupRejectAuthenticated
  dom.window.close()
}
