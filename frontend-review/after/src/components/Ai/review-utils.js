export const settingBounds={daily_articles:[1,1000],daily_tokens:[5000,10000000],max_chars:[1000,120000],minimum_score:[0,10]}
export const settingFields=['enabled','translation_enabled','base_url','model','prompt','daily_articles','daily_tokens','max_chars','minimum_score','json_mode']
export function settingsDelta(previous={},draft={}){return Object.fromEntries(settingFields.filter(k=>draft[k]!==previous[k]).map(k=>[k,draft[k]]))}
export function validateSettings(draft){
 const errors={};let url
 try{url=new URL(draft.base_url);if(url.protocol!=='https:'||url.username||url.password)throw new Error()}catch{errors.base_url='接口需为无内嵌凭据的 HTTPS 地址。'}
 for(const [key,max] of [['model',200],['prompt',12000],['base_url',500]])if(typeof draft[key]!=='string'||!draft[key].trim()||draft[key].length>max)errors[key]=`请输入1–${max}字符。`
 for(const [key,[min,max]] of Object.entries(settingBounds)){const value=String(draft[key]??'').trim(),number=Number(value);if(!value||!Number.isInteger(number)||number<min||number>max)errors[key]=`必须为 ${min}–${max} 之间的整数。`}
 return errors
}
export function normalizeDraft(draft){const result={...draft};for(const key of Object.keys(settingBounds))result[key]=Number(draft[key]);for(const key of ['base_url','model','prompt'])result[key]=String(draft[key]||'').trim();return result}
export function filterCatalog(sources,search='',category='',state='all'){
 const query=search.toLocaleLowerCase().trim()
 return sources.filter(s=>`${s.name||''} ${s.category||''}`.toLocaleLowerCase().includes(query)&&(!category||s.category===category)&&(state==='all'||state==='subscribed'&&s.subscribed||state==='addable'&&s.status==='ok'&&!s.subscribed&&s.analysis_supported!==false||state==='attention'&&(s.live_error||['error','blocked'].includes(s.status))))
}
export function uniqueSources(items){const seen=new Set();return items.filter(s=>{if(!s.url||seen.has(s.url))return false;seen.add(s.url);return true})}
export async function subscriptionQueue(items,{getCategories,getFeeds,createCategory,subscribe,stopped,progress}){
 const statusCode=e=>e?.status||e?.statusCode||e?.response?.status
 const categories=await getCategories();if(stopped())return []
 const existing=new Set((await getFeeds()).map(f=>f.feed_url));const results=[]
 for(const source of uniqueSources(items)){
  if(stopped())break
  if(existing.has(source.url)){results.push({source,state:'existing'});progress([...results]);continue}
  try{
   const title=source.category||'手动来源'
   let category=categories.find(c=>c.title===title)
   if(!category){
    if(stopped())break
    try{category=await createCategory(title);categories.push(category)}
    catch(e){
     if(statusCode(e)!==409)throw e
     const refreshed=await getCategories();category=refreshed.find(c=>c.title===title)
     if(!category)throw e
     categories.splice(0,categories.length,...refreshed)
    }
   }
   if(stopped())break
   try{await subscribe(source,category);existing.add(source.url);results.push({source,state:'success'})}
   catch(e){
    if(statusCode(e)!==409)throw e
    const feeds=await getFeeds()
    if(!feeds.some(f=>f.feed_url===source.url))throw e
    existing.add(source.url);results.push({source,state:'existing'})
   }
  }catch(e){const code=statusCode(e);results.push({source,state:'failed',error:code?`HTTP ${code}`:'请求结果未确认，重试前会核对已存在的订阅。'})}
  progress([...results])
 }
 return results
}
