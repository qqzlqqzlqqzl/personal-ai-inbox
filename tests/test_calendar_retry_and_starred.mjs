// Frozen-clock regression against generated reader modules. No live APIs or data.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
const web = new URL("../upstream/reactflux/", import.meta.url).pathname;
const require = createRequire(web + "package.json");
const { build } = createRequire(require.resolve("vite"))("esbuild");
const dir = await mkdtemp(join(tmpdir(), "reader-calendar-"));
await build({stdin:{contents:`export * from '@/utils/date'; export * from '@/utils/reading-calendar'; export * from '@/store/readingCalendarState'; export * from '@/store/authState'; export * from '@/store/dataState'; export * from '@/store/contentState'; export * from '@/store/aiState'; export * from '@/apis/entries'; export {default as coordinator} from '@/data/app-data-coordinator'; export {default as requestKey} from '@/utils/article-list-request-key'; export {settingsState} from '@/store/settingsState';`,resolveDir:web},outfile:join(dir,"test.cjs"),bundle:true,platform:"node",format:"cjs",alias:{"@":web+"src"},plugins:[{name:"fixtures",setup(b){
  b.onResolve({filter:/^@\/hooks\/useLanguage$/},()=>({path:"language",namespace:"fixture"}));
  b.onResolve({filter:/(?:^|\/)ofetch(?:\.js)?$/},()=>({path:"api",namespace:"fixture"}));
  b.onLoad({filter:/.*/,namespace:"fixture"},({path})=>({contents:path==="language"?"export const polyglotState={get:()=>({polyglot:{t:x=>x}})}":"export default Object.fromEntries(['get','put','post'].map(method=>[method,(...args)=>globalThis.calendarApi(method,...args)]))",loader:"js"}));
}}]});
const NativeDate = Date;
let now = NativeDate.parse("2026-10-02T15:59:59Z");
globalThis.Date = class extends NativeDate {constructor(...args){super(...(args.length?args:[now]))} static now(){return now}};
try {
 const app=require(join(dir,"test.cjs"));
 const auth={server:"https://synthetic.test/mf",token:"synthetic",username:"",password:""};
 const reset=()=>{now=NativeDate.parse("2026-10-02T15:59:59Z");app.setAuth(auth);app.resetData();app.commitIdentityData({id:1,timezone:"Asia/Shanghai"})};
 const writes=[];let idsFetched=0,release;
 const baseline=async(method,url,body)=>{
  if(method!=="get"){writes.push(body);return {}}
  if(url.includes("/entries/ids?")){idsFetched++;return {total:2,entry_ids:[41,42]}}
  return {total:0,entries:[]};
 };
 const selection=process.argv[2];
 if(!selection||selection==="retry"){
  reset();const coordinator=app.coordinator();
  globalThis.calendarApi=async()=>{throw new Error("synthetic identity refresh failure")};
  await assert.rejects(coordinator.actions.refreshIdentity({force:true}),/synthetic/);
  assert.equal(app.getReadingCalendarSnapshot().ready,false);
  globalThis.calendarApi=async()=>new Promise(resolve=>{release=resolve});
  const retry=coordinator.actions.refreshIdentity({force:true});await new Promise(resolve=>setImmediate(resolve));
  assert.equal(app.getReadingCalendarSnapshot().ready,false,"pending retry must not authorize the failed previous identity snapshot");
  await assert.rejects(app.markStarredEntriesAsRead());
  assert.equal(writes.length,0);
  release({id:1,timezone:"Asia/Shanghai"});await retry;
  assert.equal(app.getReadingCalendarSnapshot().ready,true);coordinator.dispose();
  console.log("PASS failed identity -> pending retry stays gated -> successful me reauthorizes");
 }
 if(!selection||selection==="collection"){
  // The mocked transport isolates calendar cancellation. Actual ofetch already rejects stale accounts.
  for(const [name,change] of [["midnight",()=>{now=NativeDate.parse("2026-10-02T16:00:00Z")}],["zone",()=>app.commitIdentityData({id:1,timezone:"UTC"})],["account",()=>app.setAuth({...auth,token:"other"})]]){
   reset();writes.length=0;idsFetched=0;
   globalThis.calendarApi=async(method,url,body)=>{const value=await baseline(method,url,body);if(url.includes("/entries/ids?"))change();return value};
   await assert.rejects(app.markStarredEntriesAsRead(),/已改变/,name+" during ID collection must cancel before PUT");
   assert.equal(writes.length,0,name+" sends no write");assert.equal(idsFetched,1);
  }
  reset();writes.length=0;idsFetched=0;
  globalThis.calendarApi=async(method,url,body)=>{
   if(url.includes("/entries/ids?")){idsFetched++;now=NativeDate.parse("2026-10-02T16:00:00Z");return {total:2001,entry_ids:Array.from({length:1000},(_,i)=>i+1)}}
   return baseline(method,url,body);
  };
  await assert.rejects(app.markStarredEntriesAsRead(),/已改变/);
  assert.equal(idsFetched,1,"calendar change stops further ID pages");assert.equal(writes.length,0);
  reset();writes.length=0;idsFetched=0;
  globalThis.calendarApi=async(method,url,body)=>{
   if(url.includes("/entries/ids?")){idsFetched++;return {total:2001,entry_ids:Array.from({length:2001},(_,i)=>i+1)}}
   if(method==="put"){writes.push(body);now=NativeDate.parse("2026-10-02T16:00:00Z");return {}}
   return baseline(method,url,body);
  };
  await assert.rejects(app.markStarredEntriesAsRead(),/已改变/);
  assert.equal(writes.length,1,"cross-midnight stops remaining write batches");assert.equal(writes[0].entry_ids.length,1000);
  reset();writes.length=0;globalThis.calendarApi=baseline;
  assert.equal(await app.markStarredEntriesAsRead(),2);assert.deepEqual(writes[0],{entry_ids:[41,42],status:"read"});
  app.commitIdentityData({id:1,timezone:"Invalid/Zone"});idsFetched=0;
  await assert.rejects(app.markStarredEntriesAsRead());assert.equal(idsFetched,0,"invalid zone cannot start ID collection");
  console.log("PASS starred collection/write cancellation for day, zone, account; successful frozen IDs; invalid identity");
 }
}finally{globalThis.Date=NativeDate;await rm(dir,{recursive:true,force:true})}
