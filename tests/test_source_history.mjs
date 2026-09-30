import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const source = await readFile(new URL('../patches/source-history.js', import.meta.url), 'utf8')
const { historyLines, historyDate } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))

test('empty storage and empty feed are explicit', () => {
  const lines = historyLines({ stored: {state:'ok', count:0}, feed_window: {state:'ok', count:0, dated_count:0} }).join('\n')
  assert.match(lines, /已存储 0 条/)
  assert.match(lines, /本次 feed 为空，不能据此判断站点历史为空/)
  assert.doesNotMatch(lines, /最旧 published_at/)
})

test('unknown and failed requests never render zero', () => {
  const lines = historyLines({ stored: {state:'unavailable'}, feed_window: {state:'unavailable'} }).join('\n')
  assert.match(lines, /条目数与时间范围未知/)
  assert.match(lines, /暴露历史未知/)
  assert.doesNotMatch(lines, /0 条/)
})

test('partial dates and updated fallback are disclosed', () => {
  const lines = historyLines({ stored: {state:'ok', count:99}, feed_window: {
    state:'ok', count:4, dated_count:3, undated_count:1, updated_fallback_count:1, span_days:3,
    oldest_at:'2026-01-01T00:00:00Z', newest_at:'2026-01-04T00:00:00Z', checked_at:'2026-01-05T00:00:00Z', cached:true,
  }}).join('\n')
  assert.match(lines, /已存储 99 条/)
  assert.match(lines, /RSS\/Atom 本次暴露 4 条/)
  assert.match(lines, /另有 1 条日期未知/)
  assert.match(lines, /范围采用其更新时间/)
  assert.match(lines, /复用 5 分钟内快照/)
})

test('all-undated entries do not imply zero-day coverage', () => {
  const lines = historyLines({feed_window:{state:'ok',count:3,dated_count:0}}).join('\n')
  assert.match(lines, /全部 3 条日期未知/)
  assert.doesNotMatch(lines, /跨度 0/)
})

test('UTC formatting never shifts dates silently to user timezone', () => {
  assert.equal(historyDate('2026-01-01T02:00:00+08:00'), '2025-12-31 18:00:00 UTC')
  assert.equal(historyDate(null), '未知')
  assert.equal(historyDate('not a date'), '未知')
})
