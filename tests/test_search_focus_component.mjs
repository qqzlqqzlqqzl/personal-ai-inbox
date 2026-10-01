// Shared modal compatibility: real React, Arco Modal and FocusLock; synthetic DOM.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdtemp, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import test from 'node:test'

const webRequire = createRequire(new URL('../upstream/reactflux/package.json', import.meta.url))
const { build } = createRequire(webRequire.resolve('vite'))('esbuild')
const { JSDOM } = createRequire(new URL('../runtime/history-test-tools/package.json', import.meta.url))('jsdom')
const dom = new JSDOM('<!doctype html><main id="fixture"></main>', { url: 'http://synthetic.test/' })
Object.assign(globalThis, { window: dom.window, document: dom.window.document,
  getComputedStyle: dom.window.getComputedStyle, IS_REACT_ACT_ENVIRONMENT: true })
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
for (const name of ['HTMLElement', 'Element', 'Node', 'SVGElement', 'MutationObserver',
  'HTMLIFrameElement', 'HTMLInputElement', 'HTMLButtonElement', 'ShadowRoot', 'Document']) {
  globalThis[name] = dom.window[name]
}
dom.window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {},
  addEventListener() {}, removeEventListener() {} })
globalThis.requestAnimationFrame = callback => setTimeout(callback, 0)
globalThis.cancelAnimationFrame = clearTimeout
globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} }
// jsdom has no layout. Keep focusability geometry explicit without changing focus APIs.
const rect = { x: 0, y: 0, top: 0, left: 0, right: 100, bottom: 20, width: 100, height: 20 }
dom.window.HTMLElement.prototype.getBoundingClientRect = () => rect
dom.window.HTMLElement.prototype.getClientRects = function () {
  for (let element = this; element; element = element.parentElement) {
    if (element.style.display === 'none') return []
  }
  return [rect]
}
const React = webRequire('react'), { createRoot } = webRequire('react-dom/client')
const directory = await mkdtemp(join(tmpdir(), 'reader-search-focus-'))
const output = join(directory, 'modal.cjs')
await build({ entryPoints: [new URL('../frontend-review/after/src/components/ui/AccessibleModal.jsx', import.meta.url).pathname],
  outfile: output, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic',
  plugins: [{ name: 'isolated-ui', setup(builder) {
    builder.onResolve({ filter: /^(react(?:\/.*)?|@arco-design\/web-react(?:\/.*)?)$/ }, ({ path }) => ({ path: webRequire.resolve(path), external: true }))
    builder.onResolve({ filter: /\.css$/ }, () => ({ path: 'styles', namespace: 'fixture' }))
    builder.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: '', loader: 'js' }))
  } }] })
const AccessibleModal = createRequire(import.meta.url)(output).default
const pause = async () => { await React.act(async () => { await new Promise(resolve => setTimeout(resolve, 450)) }) }

async function fixture(explicit = true) {
  const container = document.createElement('div'), opener = document.createElement('button'), fallback = document.createElement('button')
  opener.textContent = 'Open search'; fallback.textContent = 'Fallback'; fallback.className = 'fixture-fallback'
  document.querySelector('#fixture').append(opener, fallback, container)
  const root = createRoot(container), returnFocusRef = { current: null }
  let setVisible
  function View() {
    const [visible, set] = React.useState(false); setVisible = set
    return React.createElement(AccessibleModal, {
      title: 'Search', visible, onCancel: () => set(false), closeLabel: 'Close search',
      fallbackFocusSelector: '.fixture-fallback', ...(explicit ? { returnFocusRef } : {}),
    }, React.createElement('input', { 'aria-label': 'Query' }))
  }
  await React.act(async () => root.render(React.createElement(View)))
  return { opener, fallback,
    async open() { opener.focus(); returnFocusRef.current = document.activeElement; await React.act(async () => setVisible(true)); await pause() },
    async close() { await React.act(async () => setVisible(false)); await pause() },
    async cleanup() { await React.act(async () => root.unmount()); opener.remove(); fallback.remove(); container.remove() },
  }
}

try {
  await test('explicit opener survives at least three retained-portal open/close cycles', async () => {
    const view = await fixture()
    try {
      for (let cycle = 0; cycle < 4; cycle++) {
        await view.open(); assert.notEqual(document.activeElement, view.opener)
        await view.close(); assert.equal(document.activeElement, view.opener, 'cycle ' + cycle)
      }
    } finally { await view.cleanup() }
  })
  await test('removed explicit opener restores to the caller-provided fallback', async () => {
    const view = await fixture()
    try { await view.open(); view.opener.remove(); await view.close(); assert.equal(document.activeElement, view.fallback) }
    finally { await view.cleanup() }
  })
  await test('existing modal without the new prop retains its original first-open focus behavior', async () => {
    const view = await fixture(false)
    try { await view.open(); await view.close(); assert.equal(document.activeElement, view.opener) }
    finally { await view.cleanup() }
  })
} finally { dom.window.close(); await rm(directory, { recursive: true, force: true }) }
