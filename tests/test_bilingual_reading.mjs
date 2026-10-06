import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

// Exercise the actual selector without a browser, model, or frontend packages.
// JSX component behavior is covered separately by the existing React fixture.
const source = await readFile(new URL('../patches/BilingualReading.jsx', import.meta.url), 'utf8')
const selector = source.slice(0, source.indexOf('export default function BilingualReading'))
  .replace(/^import .*$/gm, '')
const { getBilingualReading, READING_MODES, shouldPollTranslation, startBilingualPolling } = await import(`data:text/javascript;base64,${Buffer.from(selector).toString('base64')}`)
const content = '<h2 id="intro">Original</h2><p>English text.</p><img src="/photo.jpg"><pre><code>const x = 1</code></pre>'
const bilingual = '<h2 id="intro">简介</h2><p class="reader-translation-target">中文段落。</p><p class="reader-translation-original">English text.</p><img src="/photo.jpg"><pre><code>const x = 1</code></pre>'
const chinese = '<h2 id="intro">简介</h2><p class="reader-translation-target">中文段落。</p><img src="/photo.jpg"><pre><code>const x = 1</code></pre>'
const entry = { id: 7, content, translation: { language: 'zh-CN', status: 'done', bilingual_html: bilingual, chinese_html: chinese } }

test('defaults to Chinese-first bilingual HTML with three explicit modes', () => {
  assert.deepEqual(READING_MODES, [['bilingual', '双语'], ['chinese', '仅中文'], ['original', '原文']])
  assert.deepEqual(getBilingualReading(entry), { mode: 'bilingual', html: bilingual, message: '' })
  assert.equal(getBilingualReading(entry, 'chinese').html, chinese)
  assert.equal(getBilingualReading(entry, 'original').html, content)
  assert.equal(getBilingualReading(entry, 'unknown').mode, 'bilingual')
})

test('waiting and failed translation never replace or block the original body', () => {
  for (const status of ['pending', 'processing', 'error', 'notconfigured', 'disabled', 'budget_paused', 'waiting_model']) {
    const pending = { content, translation: { language: 'zh-CN', status } }
    for (const mode of ['bilingual', 'chinese', 'original']) {
      const result = getBilingualReading(pending, mode)
      assert.equal(result.html, content, `${status}/${mode}`)
      assert.equal(result.mode, mode)
      assert.doesNotMatch(result.message, /model|mini|token|预算|模型|接口|key/i)
      if (mode === 'original') assert.equal(result.message, '')
    }
  }
  assert.equal(getBilingualReading({ content }).html, content)
  assert.equal(getBilingualReading({ content, translation: { status: 'native' } }).message, '')
})

test('partial documents remain readable and preserve server image/code count exactly', () => {
  const partial = { ...entry, translation: { ...entry.translation, status: 'partial' } }
  for (const mode of ['bilingual', 'chinese']) {
    const result = getBilingualReading(partial, mode)
    assert.equal(result.message, '部分段落已译')
    assert.equal((result.html.match(/<img /g) || []).length, 1)
    assert.equal((result.html.match(/<pre>/g) || []).length, 1)
  }
  assert.ok(getBilingualReading(partial).html.indexOf('中文段落') < getBilingualReading(partial).html.indexOf('English text'))
})

test('empty, malformed and other-language translations safely fall back per mode', () => {
  for (const value of [undefined, null, '', '  ', 42, {}, []]) {
    const malformed = { content, translation: { language: 'zh-CN', status: 'done', bilingual_html: value, chinese_html: value } }
    assert.equal(getBilingualReading(malformed).html, content)
    assert.equal(getBilingualReading(malformed, 'chinese').html, content)
  }
  assert.equal(getBilingualReading({ ...entry, translation: { ...entry.translation, language: 'fr' } }).html, content)
  assert.equal(getBilingualReading({ ...entry, translation: { ...entry.translation, chinese_html: null } }, 'chinese').html, content)
  assert.equal(getBilingualReading(null).html, '')
})

test('repeated switching and navigation do not mutate entry content or retain prior HTML', () => {
  const before = JSON.stringify(entry)
  for (let i = 0; i < 4; i++) {
    for (const mode of ['chinese', 'original', 'bilingual']) getBilingualReading(entry, mode)
  }
  assert.equal(JSON.stringify(entry), before)
  assert.equal(getBilingualReading({ id: 8, content: '<p>Next article</p>' }).html, '<p>Next article</p>')
  assert.equal(getBilingualReading({ ...entry, translation: undefined }).html, content)
})

function pollingFixture(status = 'pending') {
  const timers = new Map(), requests = [], received = []
  let id = 0, current = true
  const stop = startBilingualPolling({
    status, isCurrent: () => current,
    request: signal => new Promise((resolve, reject) => requests.push({ signal, resolve, reject })),
    onTranslation: value => received.push(value),
    setTimer: (callback, delay) => { assert.equal(delay, 10000); timers.set(++id, callback); return id },
    clearTimer: timer => timers.delete(timer),
  })
  return { timers, requests, received, stop,
    leave: () => { current = false },
    async tick() {
      assert.equal(timers.size, 1)
      const [key, callback] = timers.entries().next().value
      timers.delete(key)
      const pending = callback()
      await Promise.resolve()
      return { settled: pending }
    },
  }
}

test('only pending/partial poll after ten seconds and stop on every terminal status', async () => {
  for (const status of ['done', 'native', 'notconfigured', 'error', 'budget_paused', 'disabled', undefined]) {
    assert.equal(shouldPollTranslation(status), false)
    const idle = pollingFixture(status ?? null)
    assert.equal(idle.timers.size, 0)
    idle.stop()
    const running = pollingFixture('partial')
    const tick = await running.tick()
    assert.equal(running.requests.length, 1)
    assert.equal(running.timers.size, 0, 'never overlap pending requests')
    running.requests[0].resolve({ status: status ?? 'done' })
    await tick.settled
    assert.equal(running.timers.size, 0)
    running.stop()
  }
})

test('partial responses reschedule, transient errors retain content, and eventual done stops', async () => {
  const fixture = pollingFixture()
  assert.equal(fixture.requests.length, 0, 'opening a detail does not wait for a request')
  let tick = await fixture.tick()
  fixture.requests[0].resolve({ status: 'partial', bilingual_html: bilingual })
  await tick.settled
  assert.equal(fixture.received.length, 1)
  tick = await fixture.tick()
  fixture.requests[1].reject(new Error('temporary network error'))
  await tick.settled
  assert.equal(fixture.received.length, 1)
  assert.equal(fixture.received[0].bilingual_html, bilingual)
  tick = await fixture.tick()
  fixture.requests[2].resolve({ status: 'done', bilingual_html: bilingual })
  await tick.settled
  assert.equal(fixture.received.length, 2)
  assert.equal(fixture.timers.size, 0)
  fixture.stop()
})

test('navigation and unmount cancel the request and reject a late response even if abort is ignored', async () => {
  for (const cancel of ['stop', 'leave']) {
    const fixture = pollingFixture()
    const tick = await fixture.tick()
    fixture[cancel]()
    if (cancel === 'stop') assert.equal(fixture.requests[0].signal.aborted, true)
    fixture.requests[0].resolve({ status: 'partial', bilingual_html: '<p>Old article</p>' })
    await tick.settled
    assert.equal(fixture.received.length, 0)
    assert.equal(fixture.timers.size, 0)
    assert.equal(fixture.requests[0].signal.aborted, true)
  }
})
