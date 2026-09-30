import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = await readFile(new URL('../patches/bulk-read-label.js', import.meta.url), 'utf8')
const { bulkReadLabel } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))
for (const [scope, label] of Object.entries({ today: '今天', feed: '当前订阅源', category: '当前分类', starred: '收藏' })) {
  assert.equal(bulkReadLabel(scope), `标记${label}的全部文章为已读`)
  assert.equal(bulkReadLabel(scope, true), `标记${label}筛选出的文章为已读`)
}
assert.equal(bulkReadLabel('all'), '标记全部文章为已读')
assert.equal(bulkReadLabel('all', true), '标记当前筛选结果为已读')
console.log('Bulk read scope labels: 10 assertions passed')
