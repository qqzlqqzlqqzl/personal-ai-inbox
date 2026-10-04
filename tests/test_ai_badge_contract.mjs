// Frozen contract: backend 9b2aa8c1f3a16633151dbb8d41dd1e7d229cacd4,
// docs/READER_CONTENT_QUALITY.md. No backend source is merged into this test.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {mkdtemp} from 'node:fs/promises'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import {retainTestDirectory} from './retain_test_directory.mjs'
const web=new URL('../upstream/reactflux/',import.meta.url).pathname
const require=createRequire(import.meta.url),deps=createRequire(web+'package.json')
const {build}=createRequire(deps.resolve('vite'))('esbuild')
const React=deps('react'),{renderToStaticMarkup}=deps('react-dom/server')
const directory=await mkdtemp(join(tmpdir(),'reader-badge-contract-')),output=join(directory,'badge.cjs')
try {
 await build({entryPoints:[new URL('../patches/AiBadge.jsx',import.meta.url).pathname],outfile:output,
  bundle:true,platform:'node',format:'cjs',jsx:'automatic',nodePaths:[web+'node_modules'],plugins:[{
   name:'synthetic-css-only',setup(b){
    b.onResolve({filter:/^react(?:\/.*)?$/},({path})=>({path:deps.resolve(path),external:true}))
    b.onResolve({filter:/\.css$/},()=>({path:'css',namespace:'fixture'}))
    b.onLoad({filter:/.*/,namespace:'fixture'},()=>({contents:'',loader:'js'}))
   }}]})
 const {default:Badge,qualityLabels}=require(output),checks=[]
 const quality=(values)=>({policy_version:'reader-content-quality-v1',recommendation_eligible:null,
  reason_codes:[],access:'unknown',information:'unknown',...values})
 const render=(ai)=>renderToStaticMarkup(React.createElement(Badge,{entry:{ai},detailed:true}))
 const done={state:'done',score:8.5,technical_score:8,business_score:7,reason:'Synthetic historical analysis',tags:[]}
 let text=render({...done,content_quality:quality({})})
 assert.match(text,/内容资格待核实/);assert.doesNotMatch(text,/付费|低|暂不推荐/);checks.push('legacy/null is neither paid nor low quality nor excluded')
 text=render({...done,content_quality:quality({recommendation_eligible:false,reason_codes:['publisher_nonfree_pending_review']})})
 assert.match(text,/发布方标注非免费，访问条件待核实/);assert.match(text,/暂不推荐/);assert.match(text,/AI 评分/)
 assert.doesNotMatch(text,/需付费|弹窗|价格/);checks.push('publisher declaration stays pending review, score preserved')
 text=render({...done,content_quality:quality({recommendation_eligible:false,access:'paid_fulltext'})})
 assert.match(text,/原站全文需付费/);assert.doesNotMatch(text,/价格|元|美元/);checks.push('explicit paid gate has no invented price')
 text=render({...done,content_quality:quality({recommendation_eligible:false,access:'paid_fulltext',reason_codes:['conflicting_access_evidence']})})
 assert.match(text,/访问条件存在冲突/);assert.doesNotMatch(text,/需付费/);checks.push('conflicting evidence is not a permanent paid label')
 text=render({...done,content_quality:quality({access:'login_required'})})
 assert.match(text,/需要登录/);assert.doesNotMatch(text,/付费|暂不推荐/);checks.push('free login remains distinct')
 text=render({state:'content_excluded',content_quality:quality({recommendation_eligible:false,information:'low_information'})})
 assert.match(text,/未评分/);assert.match(text,/有效信息不足/);assert.doesNotMatch(text,/等待后台处理|\/10/);checks.push('new excluded state is not an endless pending job or invented score')
 text=render({state:'pending',processing:{reason_code:'paused',reason_codes:['paused','submission_unknown','ledger_unavailable'],stale:true,
  observed_at:1791100000,next_retry_at:null,message:'UNAPPROVED PRIVATE MODEL NAME',private_digest:'SECRET-DIGEST'}})
 assert.match(text,/后台处理已暂停/);assert.match(text,/提交结果未确认/);assert.match(text,/不代表本篇已提交/)
 assert.match(text,/台账暂不可读取/);assert.match(text,/已过期/);assert.doesNotMatch(text,/UNAPPROVED|SECRET-DIGEST/);checks.push('pause does not hide unresolved submission; private extras never render')
 text=render({state:'pending',processing:{reason_code:'quota_reserved',reason_codes:['quota_reserved']}})
 assert.match(text,/安全保留线/);assert.doesNotMatch(text,/耗尽|额度为零/);checks.push('quota reserve is not claimed exhausted')
 text=render({state:'UNKNOWN-UNTRUSTED',processing:{reason_code:'UNTRUSTED',reason_codes:['NOT-APPROVED'],observed_at:'PRIVATE-TEXT',message:'PRIVATE-MESSAGE'}})
 assert.match(text,/处理状态待确认/);assert.match(text,/后台状态尚未确认/);assert.doesNotMatch(text,/UNTRUSTED|APPROVED|PRIVATE/);checks.push('unknown enums and arbitrary message/time text are suppressed')
 assert.deepEqual(qualityLabels(quality({policy_version:'future',access:'paid_fulltext',recommendation_eligible:false})),['内容资格待核实'])
 checks.push('unknown policy cannot assert paid access or exclusion')
 console.log(JSON.stringify({type:'actual React SSR; no browser rendering',checks},null,2))
} finally {await retainTestDirectory(directory)}
