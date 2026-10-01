import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {kaggleLaneStatus, kaggleQuotaText, kaggleStatus} from '../frontend-review/after/src/components/Ai/kaggle-status.js'
const now=1790861700
const quota=(remaining=8.68)=>({state:'ok',gpu:{remaining_hours:remaining,total_hours:30,used_hours:30-remaining}})
const lane=(values={})=>({state:'empty',service:{ActiveState:'inactive'},quota:quota(),quota_gate:{allowed:true,state:'available'},...values})
const snapshot=values=>({enabled:true,lanes:values})
let checks=0
function check(name,run){run();checks++;console.log('PASS',name)}
check('Enabled idle is waiting, never continuous production',()=>{
 const value=kaggleStatus(snapshot({primary:lane()}),now)
 assert.equal(value.running,0);assert.match(value.text,/已启用 · 0 批运行 · 等待调度/)
 assert.doesNotMatch(value.text,/持续增量处理|正常/)
})
check('Production-shaped five-lane cooldown keeps quota separate',()=>{
 const lanes=Object.fromEntries(['primary','secondary','third','fourth','fifth'].map((key,index)=>[key,lane({state:'cooldown',
  recovery:{code:index%2?'provider_unavailable':'inaccessible',retry_at:now+600},
  service:{ActiveState:'active'},quota:quota([0,0,8.68,9.84,2.64][index]),
  quota_gate:{allowed:index>1,state:index>1?'available':'quota_reserved'}})]))
 const value=kaggleStatus(snapshot(lanes),now)
 assert.equal(value.running,0);assert.equal(value.cooldown,5);assert.match(value.text,/0 批运行 · 5 条冷却/)
 for(const key of ['third','fourth','fifth']){
  const current=kaggleLaneStatus(lanes[key],now)
  assert.equal(current.kind,'cooldown');assert.match(kaggleQuotaText(lanes[key],true,current),/额度条件满足 · 冷却后仍需恢复调度/)
  assert.doesNotMatch(current.detail,/账号失效/)
 }
})
check('At or below one hour remains closed even with inconsistent allowed flag',()=>{
 for(const remaining of [0,0.26,1]){
  const value=lane({quota:quota(remaining)})
  assert.match(kaggleQuotaText(value,true),/≤1h 停用新批次/)
 }
 assert.match(kaggleQuotaText(lane({quota:quota(1.01)}),true),/额度条件满足/)
})
check('Missing or unknown state never becomes idle or running',()=>{
 for(const value of [null,{},lane({state:undefined}),lane({state:'processing',service:{ActiveState:'active'}}),lane({state:'future_unrecognized_state'})]){
  assert.equal(kaggleLaneStatus(value,now).kind,'unknown')
  assert.equal(kaggleStatus(snapshot({primary:value}),now).running,0)
 }
 for(const value of [undefined,{}, {enabled:true}, {enabled:true,lanes:{}}])assert.match(kaggleStatus(value,now).text,/未确认/)
 assert.match(kaggleQuotaText(lane({state:undefined}),true),/调度状态未确认/)
})
check('Explicit running batch counts independently of local service and quota',()=>{
 for(const ActiveState of ['active','inactive']){
  const value=lane({state:'processing',service:{ActiveState},outstanding:{state:'running',remote_status:'RUNNING'},quota:quota(0.26),quota_gate:{allowed:false,state:'quota_reserved'}})
  assert.equal(kaggleLaneStatus(value,now).kind,'running');assert.equal(kaggleStatus(snapshot({primary:value}),now).running,1)
  assert.match(kaggleQuotaText(value,true),/≤1h 停用新批次/)
 }
 const value=lane({outstanding:{state:'running',remote_status:'COMPLETE'}})
 assert.equal(kaggleLaneStatus(value,now).kind,'recovery')
 const unconfirmed=lane({outstanding:{state:'running'}})
 assert.equal(kaggleLaneStatus(unconfirmed,now).kind,'unknown');assert.equal(kaggleStatus(snapshot({primary:unconfirmed}),now).running,0)
 const failed=lane({outstanding:{state:'terminal',remote_status:'ERROR'}})
 assert.match(kaggleLaneStatus(failed,now).text,/已结束 · 待核对/);assert.doesNotMatch(kaggleLaneStatus(failed,now).text,/等待导入/)
})
check('Unknown submission and cooldown override local active and stale running claims',()=>{
 const value=lane({state:'processing',service:{ActiveState:'active'},outstanding:{state:'submit_unknown'},recovery:{code:'inaccessible',retry_at:now+600}})
 const current=kaggleLaneStatus(value,now)
 assert.equal(current.kind,'submission_unknown');assert.match(current.text,/提交结果未确认/);assert.match(current.detail,/冷却/)
 assert.equal(kaggleStatus(snapshot({primary:value}),now).running,0)
 assert.match(kaggleQuotaText(value,true,current),/先核对已有提交结果/)
 const cooling=lane({outstanding:{state:'running',remote_status:'RUNNING'},recovery:{code:'provider_unavailable',retry_at:now+600}})
 assert.equal(kaggleLaneStatus(cooling,now).kind,'cooldown')
 const scheduler=lane({outstanding:{state:'running'}})
 assert.equal(kaggleLaneStatus(scheduler,now,{retry_at:now+300}).kind,'cooldown')
})
check('Submitted, prepared and local recovery do not count as running',()=>{
 for(const state of ['submitted','submitting','prepared','terminal','downloaded','provider_error']){
  const value=lane({service:{ActiveState:'active'},outstanding:{state}})
  assert.notEqual(kaggleLaneStatus(value,now).kind,'running')
  assert.equal(kaggleStatus(snapshot({primary:value}),now).running,0)
 }
})
check('Disabled dispatch and already-running work are distinct',()=>{
 const value=kaggleStatus({enabled:false,lanes:{primary:lane({outstanding:{state:'running',remote_status:'RUNNING'}})}},now)
 assert.match(value.text,/调度已暂停 · 1 批运行/)
 assert.match(kaggleQuotaText(lane(),false),/调度已暂停/)
})
check('Unknown, null, invalid or stale quota remains closed',()=>{
 for(const remaining of [null,undefined,'',false,'NaN','Infinity'])assert.match(kaggleQuotaText(lane({quota:{...quota(),gpu:{remaining_hours:remaining}}}),true),/额度未知或过期/)
 for(const value of [lane({quota:null}),lane({quota:{...quota(),stale:true}}),lane({quota:{...quota(),state:'error'}}),lane({quota_gate:{allowed:false,state:'quota_unknown'}})])assert.match(kaggleQuotaText(value,true),/额度未知或过期/)
})
check('Both rendered surfaces use the shared facts and retain snapshot failure disclosure',()=>{
 const toolbar=readFileSync(new URL('../frontend-review/after/src/components/Ai/AiToolbar.jsx',import.meta.url),'utf8')
 const panel=readFileSync(new URL('../frontend-review/after/src/components/Ai/AiPanel.jsx',import.meta.url),'utf8')
 assert.match(toolbar,/kaggleStatus\(progress.kaggle\).text/);assert.match(toolbar,/状态暂不可读 · 显示上次快照/)
 assert.match(panel,/kaggleStatus\(status\?\.kaggle\)/);assert.match(panel,/kaggleLaneStatus\(lane/);assert.match(panel,/kaggleQuotaText\(lane/)
 assert.doesNotMatch(toolbar,/Kaggle 持续增量处理/);assert.doesNotMatch(panel,/可提交新批次/)
 assert.match(panel,/本轮完成/);assert.doesNotMatch(panel,/activeLanes|const laneState/)
})
console.log(`Kaggle status clarity: ${checks} grouped presentation checks passed`)
