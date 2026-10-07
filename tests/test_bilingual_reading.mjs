import assert from 'node:assert/strict'
import {readFile} from 'node:fs/promises'
import test from 'node:test'

// Actual pure lifecycle and selector, with a fake API. No services or packages.
const source = await readFile(new URL('../patches/BilingualReading.jsx', import.meta.url), 'utf8')
const plain = source.slice(0, source.indexOf('export function useBilingualTranslation')).replace(/^import .*$/gm, '')
const {READING_MODES, TRANSLATION_POLL_MS, getBilingualReading, isTranslationEligible,
  shouldPollTranslation, startBilingualTranslation} = await import(`data:text/javascript;base64,${Buffer.from(plain).toString('base64')}`)
const signed = '/mf/proxy/' + 'A'.repeat(43) + '=/aHR0cHM6Ly9leGFtcGxlLm9yZy9pbWFnZS5qcGc='
const content = `<h2 id="intro">Original</h2><p>This is the English article.</p><img src="${signed}"><pre><code>sample()</code></pre>`
const bilingual = `<h2 id="intro">简介</h2><p><span class="reader-translation-target">中文段落。</span><span class="reader-translation-original">This is the English article.</span></p><img src="${signed}"><pre><code>sample()</code></pre>`
const chinese = `<h2 id="intro">简介</h2><p>中文段落。</p><img src="${signed}"><pre><code>sample()</code></pre>`
const entry = {id: 7, content, ai: {state: 'done', score: 8}}
const translation = {status: 'done', source_hash: 'hash-a', model: 'gpt-4o-mini', blocks_total: 2, blocks_done: 2,
  bilingual_html: bilingual, chinese_html: chinese}
const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve() }

function fixture({eligible = true, source} = {}) {
  const timers = new Map(), requests = [], received = []
  let current = true, errors = 0, id = 0
  const request = method => signal => new Promise((resolve, reject) => requests.push({method, signal, resolve, reject}))
  const stop = startBilingualTranslation({entryId: 7, eligible, source,
    start: request('POST'), request: request('GET'), isCurrent: () => current,
    onTranslation: value => received.push(value), onError: () => errors++,
    setTimer: (callback, delay) => { assert.equal(delay, TRANSLATION_POLL_MS); timers.set(++id, callback); return id },
    clearTimer: timer => timers.delete(timer),
  })
  return {timers, requests, received, stop, get errors() { return errors }, leave: () => { current = false },
    async reply(index, value, entryId = 7) { requests[index].resolve({entry_id: entryId, translation: value}); await flush() },
    async tick() {
      assert.equal(timers.size, 1)
      const [id, callback] = timers.entries().next().value
      timers.delete(id)
      void callback()
      await flush()
    },
  }
}

test('three modes accept the API envelope translation without a legacy language field', () => {
  assert.deepEqual(READING_MODES, [['bilingual', '中英对照'], ['chinese', '中文'], ['original', '原文']])
  const item = {...entry, translation}
  assert.deepEqual(getBilingualReading(item), {mode: 'bilingual', html: bilingual, message: ''})
  assert.equal(getBilingualReading(item, 'chinese').html, chinese)
  assert.equal(getBilingualReading(item, 'original').html, content)
  assert.equal(getBilingualReading(item, 'invalid').mode, 'bilingual')
  assert.equal(getBilingualReading(null).html, '')
})

test('original fallback and partial HTML preserve all image URLs, signatures and code', () => {
  for (const status of ['pending', 'partial', 'error', 'disabled', 'budget_paused', 'notconfigured']) {
    const item = {...entry, translation: {status}}
    assert.equal(getBilingualReading(item).html, content)
    assert.equal(getBilingualReading(item, 'original').message, '')
  }
  for (const mode of ['bilingual', 'chinese', 'original']) {
    const result = getBilingualReading({...entry, translation: {...translation, status: 'partial'}}, mode)
    assert.equal((result.html.match(/<img /g) || []).length, 1)
    assert.ok(result.html.includes(`src="${signed}"`))
    assert.equal((result.html.match(/<pre>/g) || []).length, 1)
    if (mode !== 'original') assert.equal(result.message, '部分段落已译')
  }
  assert.ok(bilingual.indexOf('中文段落') < bilingual.indexOf('This is the English article.'))
  const before = JSON.stringify(entry)
  getBilingualReading({...entry, translation}, 'chinese')
  assert.equal(JSON.stringify(entry), before)
})

test('score/state/full-body guards reject preloaded placeholders and native or explicit non-English content', () => {
  assert.equal(isTranslationEligible(entry), true)
  for (const item of [null, {...entry, id: undefined}, {...entry, content_deferred: true}, {...entry, content: ''},
    {...entry, ai: {state: 'pending', score: 9}}, {...entry, ai: {state: 'done', score: 7.99}},
    {...entry, ai: {state: 'done', score: 'bad'}}, {...entry, language: 'fr'},
    {...entry, content: '<p>这是一篇完整的中文文章，不需要再次翻译。</p>'}]) {
    assert.equal(isTranslationEligible(item), false)
  }
  assert.equal(isTranslationEligible({...entry, language: 'en-US'}), true)
  assert.equal(getBilingualReading({...entry, translation: {status: 'skipped'}}).message, '')
})

test('ineligible and already complete mounted views issue no request', () => {
  for (const options of [{eligible: false}, ...['done', 'ready', 'native'].map(status => ({source: {...translation, status}}))]) {
    const f = fixture(options)
    assert.equal(f.requests.length, 0)
    assert.equal(f.timers.size, 0)
    f.stop()
  }
})

test('one demand POST obtains the missing source hash; only GETs poll partial through done', async () => {
  const f = fixture()
  assert.deepEqual(f.requests.map(r => r.method), ['POST'])
  assert.equal(f.timers.size, 0)
  await f.reply(0, {status: 'pending', source_hash: 'hash-a'})
  await f.tick()
  assert.deepEqual(f.requests.map(r => r.method), ['POST', 'GET'])
  assert.equal(f.timers.size, 0, 'pending GETs never overlap')
  await f.reply(1, {...translation, status: 'partial', blocks_done: 1})
  assert.equal(f.received.at(-1).bilingual_html, bilingual)
  await f.tick()
  f.requests[2].reject(new Error('transient read failure'))
  await flush()
  assert.equal(f.received.length, 2)
  assert.equal(f.errors, 0)
  await f.tick()
  await f.reply(3, translation)
  assert.equal(f.timers.size, 0)
  assert.deepEqual(f.requests.map(r => r.method), ['POST', 'GET', 'GET', 'GET'])
  f.stop()
})

test('terminal POST statuses never poll, including skipped/native with no source', async () => {
  for (const status of ['done', 'native', 'skipped', 'error', 'budget_paused', 'notconfigured']) {
    assert.equal(shouldPollTranslation(status), false)
    const f = fixture()
    await f.reply(0, {status})
    assert.equal(f.received[0].status, status)
    assert.equal(f.timers.size, 0)
    f.stop()
  }
})

test('repeated mounts stay separate and cached POST results stop without restarting work', async () => {
  for (let i = 0; i < 3; i++) {
    const f = fixture()
    await f.reply(0, translation)
    assert.equal(f.requests.length, 1)
    assert.equal(f.received[0], translation)
    assert.equal(f.timers.size, 0)
    f.stop()
  }
})

test('POST failure is visible without an automatic paid retry or polling an unbound source', async () => {
  const f = fixture()
  f.requests[0].reject(new Error('POST response lost'))
  await flush()
  assert.equal(f.errors, 1)
  assert.equal(f.requests.length, 1)
  assert.equal(f.timers.size, 0)
})

test('wrong entry, missing envelope/hash and changed source stop without rendering stale HTML', async () => {
  for (const invalid of ['entry', 'hash', 'missing-hash', 'envelope']) {
    const f = fixture()
    await f.reply(0, {status: 'pending', source_hash: 'hash-a'})
    await f.tick()
    if (invalid === 'entry') await f.reply(1, translation, 8)
    else if (invalid === 'hash') await f.reply(1, {...translation, source_hash: 'hash-b'})
    else if (invalid === 'missing-hash') await f.reply(1, {status: 'partial', bilingual_html: bilingual})
    else { f.requests[1].resolve(translation); await flush() }
    assert.equal(f.received.length, 1)
    assert.equal(f.errors, 1)
    assert.equal(f.timers.size, 0)
  }
})

test('navigation and unmount cancel both POST and GET; late ignored-abort responses cannot update', async () => {
  for (const phase of ['POST', 'GET']) for (const cancel of ['stop', 'leave']) {
    const f = fixture()
    let index = 0
    if (phase === 'GET') { await f.reply(0, {status: 'pending', source_hash: 'hash-a'}); await f.tick(); index = 1 }
    const before = f.received.length
    f[cancel]()
    if (cancel === 'stop') assert.equal(f.requests[index].signal.aborted, true)
    await f.reply(index, translation)
    assert.equal(f.requests[index].signal.aborted, true)
    assert.equal(f.received.length, before)
    assert.equal(f.timers.size, 0)
  }
})
