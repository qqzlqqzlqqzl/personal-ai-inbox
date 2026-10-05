// Actual React component rendered with synthetic API projections; no browser or network.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { mkdtemp, readFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'

const web = createRequire(new URL('../upstream/reactflux/package.json', import.meta.url))
const { build } = createRequire(web.resolve('vite'))('esbuild')
const React = web('react'), { renderToStaticMarkup } = web('react-dom/server')
const dir = await mkdtemp(join(tmpdir(), 'source-catalog-component-'))
const output = join(dir, 'metadata.cjs')
await build({ entryPoints: [new URL('../frontend-review/after/src/components/Ai/SourceCatalogMetadata.jsx', import.meta.url).pathname],
  outfile: output, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic',
  plugins: [{ name: 'actual-react', setup(b) {
    b.onResolve({filter: /^react(?:\/.*)?$/}, ({path}) => ({path: web.resolve(path), external: true}))
  }}] })
const Metadata = createRequire(import.meta.url)(output).default
const basic = {category: '产品与众筹', status: 'ok', subscribed: true, subscription_category: '产品灵感'}
const snapshot = {source_kind: 'newsletter_archive', coverage: 'selected_newsletters', project_links_available: false,
  state_available: true, freshness: 'stale', stale: true, last_success_at: 1791128400, last_attempt_at: 1791158340,
  last_error: {reason: 'upstream_error', http_status: 429}}
const render = x => renderToStaticMarkup(React.createElement(Metadata, {source: {...basic, ...x}}))

test('cached RSS 200 cannot hide error, staleness, archive coverage or current wrong category', () => {
  const html = render({vendor_snapshot: snapshot, note: '项目详情与直达链接未取得'})
  for (const text of ['订阅格式可解析', '上游读取失败', '快照已过期', '非项目列表', '项目详情与直达链接缺失', '产品与众筹', '当前订阅分类：产品灵感', '上次成功', '最近尝试']) assert.ok(html.includes(text), text)
  assert.ok(!html.includes('项目已接入'))
})
test('missing note does not invent a note; archive limitation still appears', () => {
  const html = render({vendor_snapshot: snapshot})
  assert.ok(!html.includes('ai-source-note')); assert.ok(html.includes('非项目列表'))
})
test('note is text, including markup; no HTML execution', () => {
  const html = render({note: '<img src=x onerror=alert(1)>'})
  assert.ok(html.includes('&lt;img')); assert.ok(!html.includes('<img')); assert.ok(!html.includes('ai-source-upstream'))
})
test('unavailable and unseeded never claim a fresh success', () => {
  assert.ok(render({vendor_snapshot: {...snapshot, state_available: false, last_success_at: null, last_attempt_at: null}}).includes('状态未知'))
  const html = render({vendor_snapshot: {...snapshot, freshness: 'never_collected', last_success_at: null, last_attempt_at: null}})
  assert.ok(html.includes('尚无成功采集')); assert.ok(!html.includes('上次成功'))
})
test('fresh timestamp does not erase a separate recent failure', () => {
  const html = render({vendor_snapshot: {...snapshot, freshness: 'fresh', stale: false}})
  assert.ok(html.includes('6 小时检查窗口')); assert.ok(html.includes('上游读取失败'))
})
test('invalid date and missing freshness fail visibly without React exception', () => {
  const html = render({vendor_snapshot: {...snapshot, freshness: 'other', last_success_at: Infinity, last_attempt_at: 'bad'}})
  assert.ok(html.includes('新鲜度未知')); assert.ok(!html.includes('<time'))
})
test('ordinary external source does not gain archive/project claims', () => {
  const html = render({subscription_category: '产品与众筹'})
  assert.ok(!html.includes('非项目列表')); assert.ok(!html.includes('当前订阅分类'))
})
test('formal AiPanel uses metadata helper while preserving source actions', async () => {
  const text = await readFile(new URL('../frontend-review/after/src/components/Ai/AiPanel.jsx', import.meta.url), 'utf8')
  assert.equal(text.split('<SourceCatalogMetadata source={s} />').length - 1, 1)
  for (const fragment of ['onClick={()=>add([s])}', 'href={s.url}', '<SourceHistory key={s.feed_id} feedId={s.feed_id} />']) assert.ok(text.includes(fragment))
})

test('invalid subscription category is not rendered as a React object',()=>{assert.ok(!render({subscription_category:{unexpected:true}}).includes('当前订阅分类'))})

const {canSubscribeSource,manualSubscriptionCandidate,filterCatalog}=await import('../frontend-review/after/src/components/Ai/review-utils.js')
test('explicit RSS subscription support is separate from analysis eligibility',()=>{
 const source={name:'Kicktraq',url:'https://www.kicktraq.com/categories/technology/latest.rss',status:'ok',analysis_supported:false,subscription_supported:true}
 assert.equal(canSubscribeSource(source),true);assert.equal(filterCatalog([source],'','','addable').length,1)
 for(const item of [{...source,subscription_supported:false},{...source,subscription_supported:undefined},{...source,subscription_supported:'true'},{...source,status:'unknown'},{...source,subscribed:true}])assert.equal(canSubscribeSource(item),false)
 assert.equal(canSubscribeSource({status:'ok',analysis_supported:false}),false)
})
test('manual form uses verified Kicktraq catalog identity and blocks unknown aliases',()=>{
 const source={url:'https://www.kicktraq.com/categories/technology/latest.rss',status:'ok',subscription_supported:false}
 assert.equal(manualSubscriptionCandidate(source.url,[source]),source)
 for(const url of [source.url,source.url.replace('https:','http:'),source.url+'?redirect=other','https://www.kicktraq.com/projects/example/',source.url.replace('www.kicktraq.com','www.kicktraq.com.'),source.url.replace('www.kicktraq.com','%77ww.kicktraq.com')])assert.equal(manualSubscriptionCandidate(url,[]),null)
 assert.equal(canSubscribeSource(manualSubscriptionCandidate('https://example.test/explicit-user-feed',[])),true)
})
test('Kicktraq summary and unavailable policy are visible without project fulltext claims',()=>{
 const html=render({provider:'Kicktraq',rss_summary_only:true,summary_policy_ready:false})
 assert.ok(html.includes('第三方项目 RSS 摘要'));assert.ok(html.includes('未提供 Kickstarter 直达链接'));assert.ok(html.includes('暂不可新增订阅'));assert.ok(html.includes('未接入项目全文评分'))
})
const badgeOutput=join(dir,'badge.cjs')
await build({entryPoints:[new URL('../patches/AiBadge.jsx',import.meta.url).pathname],outfile:badgeOutput,bundle:true,platform:'node',format:'cjs',jsx:'automatic',plugins:[{name:'badge-react-css',setup(b){b.onResolve({filter:/^react(?:\/.*)?$/},({path})=>({path:web.resolve(path),external:true}));b.onResolve({filter:/\.css$/},()=>({path:'empty',namespace:'css'}));b.onLoad({filter:/.*/,namespace:'css'},()=>({contents:'',loader:'js'}))}}]})
const Badge=createRequire(import.meta.url)(badgeOutput).default
const badge=ai=>renderToStaticMarkup(React.createElement(Badge,{entry:{ai},detailed:true}))
test('fixed processing reason never calls a summary a paper',()=>{
 const html=badge({state:'requires_fulltext_adapter',error:'rss_summary_only:kicktraq-rss-preview-only-v1',processing:{reason_code:'rss_summary_only',reason_codes:['submission_unknown']}})
 assert.ok(html.includes('Kicktraq RSS 摘要'));assert.ok(html.includes('未评分'));assert.ok(!html.includes('论文'));assert.ok(!html.includes('rss_summary_only:'));assert.ok(html.includes('提交结果未确认'))
})
test('fixed unverified identity code gets its own bounded label',()=>{
 const html=badge({state:'requires_source_review',error:'rss_feed_identity_unverified:kicktraq-rss-preview-only-v1',processing:{reason_code:'rss_feed_identity_unverified'}})
 assert.ok(html.includes('订阅来源身份待核实'));assert.ok(!html.includes('论文'));assert.ok(!html.includes('低价值'))
})
test('paper and unknown policy semantics are retained without domain guessing',()=>{
 assert.ok(badge({state:'requires_fulltext_adapter',error:'original paper error'}).includes('需要论文全文适配'))
 for(const ai of [{state:'requires_fulltext_adapter',error:'rss_summary_only:kicktraq-rss-preview-only-v2'},{state:'fetch_error',error:'rss_summary_only:kicktraq-rss-preview-only-v1'},{state:'requires_fulltext_adapter',error:'Kicktraq URL or title'}])assert.ok(!badge(ai).includes('Kicktraq RSS 摘要'))
 for(const state of ['__proto__','constructor','toString','hasOwnProperty'])assert.ok(badge({state}).includes('处理状态待确认'))
})

test('inherited subscription capability is never an approval',()=>{assert.equal(canSubscribeSource(Object.create({status:'ok',subscription_supported:true})),false);assert.equal(canSubscribeSource(null),false)})
