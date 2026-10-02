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
let now = NativeDate.parse("2026-10-02T01:37:00Z");
globalThis.Date = class extends NativeDate {constructor(...args){super(...(args.length?args:[now]))} static now(){return now}};
try {
  const app = require(join(dir,"test.cjs"));
  const auth={server:"https://synthetic.test/mf",token:"synthetic",username:"",password:""};
  app.setAuth(auth); app.commitIdentityData({id:1,timezone:"Asia/Shanghai"});
  app.aiState.set({...app.aiState.get(),mode:"recommended",minimum:8,auxiliary:"none"});
  const calls=[],writes=[];
  const records=[
    {id:1,published_at:"2026-10-01T15:00:00Z",score:9,status:"unread"},
    {id:2,published_at:"2026-10-01T17:00:00Z",score:9,status:"unread"},
    {id:3,published_at:"2026-10-01T16:00:00Z",score:9,status:"unread"},
    {id:4,published_at:"2026-10-01T16:00:00.000001Z",score:9,status:"unread"},
    {id:5,published_at:"2026-10-01T17:00:00Z",score:7,status:"unread"},
    {id:7,published_at:"2026-10-01T17:00:00Z",score:7,status:"unread"},
    {id:6,published_at:"2026-10-01T17:00:00Z",score:9,status:"read"},
  ];
  globalThis.calendarApi=async(method,url,body)=>{
    const q=new URL(url,"https://synthetic.test");calls.push(q);
    if(method!=="get"){writes.push(body);return {}}
    if(q.pathname.endsWith("/me"))return {id:1,timezone:"Asia/Shanghai"};
    if(q.pathname.endsWith("/feeds")||q.pathname.endsWith("/categories"))return [];
    if(q.pathname.endsWith("/counters"))return {reads:{},unreads:{}};
    if(!q.pathname.endsWith("/entries"))return {total:0,entry_ids:[]};
    const after=Number(q.searchParams.get("published_after"));
    const ai=q.searchParams.has("ai_view");
    let selected=records.filter(e=>{
      const seconds=NativeDate.parse(e.published_at.replace(/\.\d+/,""))/1000;
      const fraction=e.id===4;
      return (seconds>after||(seconds===after&&(ai||fraction)))&&
        (!q.searchParams.get("status")||e.status===q.searchParams.get("status"))&&
        (!ai||e.score>=Number(q.searchParams.get("ai_min")));
    });
    return {total:selected.length,entries:selected.slice(0,Number(q.searchParams.get("limit")))};
  };
  for(const browserZone of ["America/Los_Angeles","UTC","Asia/Shanghai"]){
    process.env.TZ=browserZone;
    assert.equal(app.getTimestamp(app.getStartOfToday()),NativeDate.parse("2026-10-01T16:00:00Z")/1000,"same account cutoff in "+browserZone);
    const response=await app.getTodayEntries("unread",{search:"fixture",limit:24});
    assert.deepEqual(response.entries.map(e=>e.id),[2,3,4]);assert.equal(response.total,3);
    const query=calls.at(-1).searchParams;
    for(const [key,value] of Object.entries({ai_min:"8",status:"unread",search:"fixture",limit:"24"}))assert.equal(query.get(key),value);
    const summary=await app.getEntryCountSummary();assert.equal(summary.unreadTodayCount,4);
    // Raw summary includes the below-score entry 5 and excludes exact midnight 3.
    const date=calls.findLast(q=>q.searchParams.has("published_after")).searchParams;
    assert.equal(date.has("ai_view"),false);
    assert.equal(app.getCalendarStartTimestamp("2026-10-02"),1790870400);
    assert.equal(app.getDayEndTimestamp("2026-10-02"),1790956799);
    assert.equal(app.getTimestamp("2026-10-01T15:00:00Z"),1790866800,"publication instant unchanged");
  }
  console.log("PASS identical cutoff, IDs, totals, score/unread/search and raw counters across three browser zones");
  assert.equal(app.checkIsInToday("2026-10-01T15:00:00Z"),false,"yesterday within 24h is not Today");
  assert.equal(app.checkIsInToday("2026-10-01T16:00:00Z"),false,"raw predicate is strict");
  assert.equal(app.checkIsInToday("2026-10-01T16:00:00.000001Z"),true,"microsecond after raw lower bound");
  assert.equal(app.matchesToday("2026-10-01T16:00:00Z",1790870400,true),true,"AI is inclusive");
  assert.equal(app.checkIsInToday("2026-10-03T00:00:00Z"),true,"existing Today lower-bound-only contract retained");
  for(const [day,hours] of [["2026-03-08",23],["2026-11-01",25]]){
    app.commitIdentityData({id:1,timezone:"America/Los_Angeles"});
    const following=new NativeDate(NativeDate.parse(day+"T00:00:00Z")+86400000).toISOString().slice(0,10);
    const start=app.getCalendarStartTimestamp(day),next=app.getCalendarStartTimestamp(following);
    assert.equal(next-start,hours*3600);
    assert.equal(app.getDayEndTimestamp(day),next-1,"retained whole-second endOf-day boundary");
  }
  app.commitIdentityData({id:1,timezone:"UTC"});
  assert.equal(app.getTimestamp(app.getStartOfToday()),1790899200,"UTC must not default to Shanghai");
  app.commitIdentityData({id:1});
  assert.equal(app.getTimestamp(app.getStartOfToday()),1790870400,"authenticated missing zone uses explicit deployment default");
  app.commitIdentityData({id:1,timezone:"Invalid/Zone"});
  assert.throws(()=>app.getStartOfToday());
  await assert.rejects(app.markEntriesAsReadInBatches(async()=>({entries:[{id:1}]})));
  assert.equal(writes.length,0);
  app.commitIdentityData({id:1,timezone:"Asia/Shanghai"});
  const snapshot=app.requireReadingCalendar();
  app.setAuth({...auth,token:"second-account"});
  assert.throws(()=>app.requireReadingCalendar(),"old identity cannot authorize new account");
  assert.throws(()=>app.assertReadingCalendarCurrent(snapshot));
  app.setAuth(auth);app.commitIdentityData({id:1,timezone:"Asia/Shanghai"});
  for(const [instant,cutoff] of [["2026-10-02T15:59:59.999Z",1790870400],["2026-10-02T16:00:00Z",1790956800],["2026-10-02T16:00:00.001Z",1790956800]]){
    now=NativeDate.parse(instant);assert.equal(app.requireReadingCalendar().cutoff,cutoff);
  }
  now=NativeDate.parse("2026-10-02T15:59:59Z");
  let fetches=0;
  const previousTransport=globalThis.calendarApi;
  globalThis.calendarApi=async(method,...args)=>{if(method==="put"){writes.push(args[1]);now=NativeDate.parse("2026-10-02T16:00:00Z");return {}}return previousTransport(method,...args)};
  await assert.rejects(app.markEntriesAsReadInBatches(async()=>{fetches++;return {entries:[{id:2}]}}),/已改变/);
  assert.equal(fetches,1);assert.deepEqual(writes.at(-1).entry_ids,[2]);
  now=NativeDate.parse("2026-10-02T01:37Z");
  const beforeWrites=writes.length;
  await assert.rejects(app.markEntriesAsReadInBatches(async()=>{app.setAuth({...auth,token:"other"});return {entries:[{id:4}]}}),/已改变/);
  assert.equal(writes.length,beforeWrites,"account switch during fetch sends no write");
  app.setAuth(auth);app.resetData();
  let releaseIdentity;
  globalThis.calendarApi=async(method,url,...args)=>url==="/v1/me"?new Promise(resolve=>{releaseIdentity=resolve}):previousTransport(method,url,...args);
  const coordinator=app.coordinator();const beforeCalls=calls.length;
  const bootstrap=coordinator.bootstrap();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(calls.slice(beforeCalls).some(q=>q.searchParams.has("published_after")),false,"late identity gates date counts");
  await assert.rejects(app.getTodayEntries("unread"));
  releaseIdentity({id:1,timezone:"Asia/Shanghai"});await bootstrap;
  assert.equal(app.dataState.get().unreadTodayCount,4);
  coordinator.dispose();
  console.log("PASS strict/inclusive/fractional bounds, YYYY-MM-DD, 23/25h DST, UTC, invalid/late identity, Shanghai midnight and batch cancellation");
} finally {globalThis.Date=NativeDate;await rm(dir,{recursive:true,force:true})}
