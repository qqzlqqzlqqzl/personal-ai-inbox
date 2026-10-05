import assert from 'node:assert/strict'
import {filterCatalog,settingsDelta,validateSettings,normalizeDraft,subscriptionQueue,uniqueSources} from '../frontend-review/after/src/components/Ai/review-utils.js'
const config={base_url:'https://example.com/api',model:'test',prompt:'测试',daily_articles:80,daily_tokens:500000,max_chars:40000,minimum_score:6}
assert.deepEqual(validateSettings(config),{})
for(const field of ['daily_articles','daily_tokens','max_chars','minimum_score'])assert.ok(validateSettings({...config,[field]:''})[field])
assert.ok(validateSettings({...config,base_url:'http://example.com'})['base_url'])
assert.ok(validateSettings({...config,base_url:'https://user:pass@example.com'})['base_url'])
assert.ok(validateSettings({...config,prompt:' '}).prompt)
assert.deepEqual(settingsDelta(config,{...config,model:'new',api_key:'never-forward'}),{model:'new'})
assert.equal(normalizeDraft({...config,daily_articles:'81'}).daily_articles,81)
const sources=[{name:'Alpha',url:'https://a',category:'Tech',status:'ok',subscription_supported:true}, {name:'Beta',url:'https://b',category:'Art',status:'subscribed',subscribed:true},{name:'Broken',url:'https://c',category:'Tech',status:'blocked'}]
assert.equal(filterCatalog(sources,'alpha','Tech','addable').length,1)
assert.equal(filterCatalog(sources,'','Tech','subscribed').length,0)
assert.equal(filterCatalog(sources,'','','attention')[0].name,'Broken')
assert.equal(uniqueSources([...sources,sources[0]]).length,3)
const submitted=[],progress=[]
const result=await subscriptionQueue(sources,{
 getCategories:async()=>[{id:1,title:'Tech'},{id:2,title:'Art'}],getFeeds:async()=>[{feed_url:'https://b'}],createCategory:()=>{throw Error('unexpected')},
 subscribe:async(s)=>{submitted.push(s.url);if(s.url==='https://c')throw Object.assign(Error('fixture'),{status:502})},stopped:()=>false,progress:r=>progress.push(r.length)
})
assert.deepEqual(result.map(r=>r.state),['success','existing','failed']);assert.deepEqual(submitted,['https://a','https://c']);assert.deepEqual(progress,[1,2,3])
let stopped=false;const partial=await subscriptionQueue(sources,{getCategories:async()=>[{id:1,title:'Tech'}],getFeeds:async()=>[],createCategory:()=>{throw Error('must stop before second')},subscribe:async()=>{},stopped:()=>stopped,progress:()=>{stopped=true}})
assert.equal(partial.length,1)
let writes=0;const none=await subscriptionQueue(sources,{getCategories:async()=>[],getFeeds:async()=>[],createCategory:()=>writes++,subscribe:()=>writes++,stopped:()=>true,progress:()=>{}})
assert.equal(writes,0);assert.deepEqual(none,[])
console.log('R02/R03/R04 query, field-validation and subscription partial/cancel checks passed')
const conflict=()=>Object.assign(Error('fixture conflict'),{status:409})
let categoryReads=0,categorySubscriptions=0
const categoryRace=await subscriptionQueue([sources[0]],{getCategories:async()=>++categoryReads===1?[]:[{id:9,title:'Tech'}],getFeeds:async()=>[],createCategory:async()=>{throw conflict()},subscribe:async(s,c)=>{assert.equal(c.id,9);categorySubscriptions++},stopped:()=>false,progress:()=>{}})
assert.equal(categorySubscriptions,1);assert.equal(categoryRace[0].state,'success')
const missingCategory=await subscriptionQueue([sources[0]],{getCategories:async()=>[],getFeeds:async()=>[],createCategory:async()=>{throw conflict()},subscribe:async()=>assert.fail('No category must not submit a feed'),stopped:()=>false,progress:()=>{}})
assert.equal(missingCategory[0].state,'failed')
for(const visible of [false,true]){
 let reads=0
 const race=await subscriptionQueue([sources[0]],{getCategories:async()=>[{id:1,title:'Tech'}],getFeeds:async()=>++reads>1&&visible?[{feed_url:sources[0].url}]:[],createCategory:async()=>assert.fail('Unexpected category'),subscribe:async()=>{throw conflict()},stopped:()=>false,progress:()=>{}})
 assert.equal(race[0].state,visible?'existing':'failed')
}
let stopAfterCategory=false,feedPosts=0
const canceled=await subscriptionQueue([sources[0]],{getCategories:async()=>[],getFeeds:async()=>[],createCategory:async()=>{stopAfterCategory=true;return{id:1,title:'Tech'}},subscribe:async()=>feedPosts++,stopped:()=>stopAfterCategory,progress:()=>{}})
assert.equal(feedPosts,0);assert.deepEqual(canceled,[])
console.log('R03 category race and verified subscription-conflict checks passed')
