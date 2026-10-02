// Actual provider/list/load-more/status lifecycle, frozen clock and synthetic transport.
import assert from "node:assert/strict";
import {createRequire} from "node:module";
import {mkdtemp,rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
const web=new URL("../upstream/reactflux/",import.meta.url).pathname;
const require=createRequire(web+"package.json");
const {build}=createRequire(require.resolve("vite"))("esbuild");
const {JSDOM}=createRequire(new URL("../runtime/history-test-tools/package.json",import.meta.url))("jsdom");
const dom=new JSDOM('<main id="fixture"></main>',{url:"https://synthetic.test/inbox/today"});
Object.assign(globalThis,{window:dom.window,document:dom.window.document,localStorage:dom.window.localStorage,sessionStorage:dom.window.sessionStorage,IS_REACT_ACT_ENVIRONMENT:true});
Object.defineProperty(globalThis,"navigator",{value:dom.window.navigator,configurable:true});
for(const name of ["HTMLElement","Element","Node","SVGElement","MutationObserver","HTMLIFrameElement","HTMLInputElement","HTMLButtonElement","ShadowRoot","Document","DOMParser"])globalThis[name]=dom.window[name];
globalThis.getComputedStyle=dom.window.getComputedStyle;
globalThis.matchMedia=dom.window.matchMedia=()=>({matches:false,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}});
globalThis.requestAnimationFrame=fn=>setTimeout(fn,0);globalThis.cancelAnimationFrame=clearTimeout;
globalThis.ResizeObserver=class{observe(){}unobserve(){}disconnect(){}};
const NativeDate=Date;let now=NativeDate.parse("2026-10-02T15:59:59Z");
globalThis.Date=class extends NativeDate{constructor(...args){super(...(args.length?args:[now]))}static now(){return now}};
const directory=await mkdtemp(join(tmpdir(),"calendar-lifecycle-"));
await build({stdin:{contents:`export {default as Provider} from '@/components/AppDataProvider'; export {default as useArticleList} from '@/hooks/useArticleList'; export {default as useLoadMore} from '@/hooks/useLoadMore'; export {getTodayEntries} from '@/apis/entries'; export * from '@/store/authState'; export * from '@/store/dataState'; export * from '@/store/contentState'; export * from '@/store/readingCalendarState'; export {updateEntriesStatusOptimistically} from '@/hooks/useEntryActions'; export {settingsState} from '@/store/settingsState'; export {aiState} from '@/store/aiState'; export {useStore} from '@nanostores/react';`,resolveDir:web},outfile:join(directory,"app.cjs"),bundle:true,platform:"node",format:"cjs",jsx:"automatic",alias:{"@":web+"src"},plugins:[{name:"transport",setup(b){
 b.onResolve({filter:/^(react(?:\/.*)?|react-dom(?:\/.*)?|@arco-design\/web-react(?:\/.*)?)$/},({path})=>({path:require.resolve(path),external:true}));
 b.onResolve({filter:/(?:^|\/)ofetch(?:\.js)?$/},()=>({path:"api",namespace:"fixture"}));
 b.onLoad({filter:/.*/,namespace:"fixture"},()=>({contents:"export default Object.fromEntries(['get','put','post'].map(method=>[method,(...args)=>globalThis.lifecycleApi(method,...args)]))",loader:"js"}));
}}]});
const React=require("react"),{createRoot}=require("react-dom/client"),app=require(join(directory,"app.cjs"));
let pendingMe,loadMore;
const pendingLists=[],calls=[];
const records=[
 [11,"2026-10-02T10:00:00Z",9,"unread",1],[12,"2026-10-02T11:00:00Z",9,"unread",1],
 [21,"2026-10-02T16:00:01Z",9,"unread",1],[22,"2026-10-02T17:00:00Z",9,"unread",1],
 [23,"2026-10-02T17:00:00Z",7,"unread",1],[24,"2026-10-02T17:00:00Z",9,"read",1],
 [41,"2026-10-02T18:00:00Z",9,"unread",2],
];
const resultFor=q=>{
 const query=q.searchParams,after=Number(query.get("published_after")),ai=query.has("ai_view");
 const user=app.authState.get().token==="other"?2:1;
 const selected=records.filter(([id,pub,score,status,owner])=>owner===user &&
  (ai?NativeDate.parse(pub)/1000>=after:NativeDate.parse(pub)/1000>after)&&
  (!query.get("status")||status===query.get("status"))&&(!ai||score>=Number(query.get("ai_min"))));
 const offset=Number(query.get("offset")),limit=Number(query.get("limit"));
 return {total:selected.length,entries:selected.slice(offset,offset+(ai?Math.min(limit,1):limit)).map(([id,pub,score,status])=>({...entry(id,pub),status,ai:{status:"done",score}}))};
};
globalThis.lifecycleApi=async(method,url,body)=>{
 calls.push({method,url,body});const q=new URL(url,"https://synthetic.test");
 if(method!=="get")return {};
 if(q.pathname.endsWith("/me"))return new Promise(resolve=>{pendingMe=resolve});
 if(q.pathname.endsWith("/feeds")||q.pathname.endsWith("/categories"))return [];
 if(q.pathname.endsWith("/counters"))return {unreads:{},reads:{}};
 if(q.pathname.endsWith("/entries")){
  const response=resultFor(q);
  if(!q.searchParams.has("ai_view"))return response;
  return new Promise(resolve=>pendingLists.push({resolve,query:q.searchParams,response}));
 }
 return {total:0,entry_ids:[]};
};
const auth={server:"https://synthetic.test/mf",token:"synthetic",username:"",password:""};
app.setAuth(auth);
app.settingsState.set({...app.settingsState.get(),pageSize:1,showStatus:"unread"});
app.aiState.set({...app.aiState.get(),mode:"recommended",minimum:8,auxiliary:"none"});
const getter=(status,_starred,params)=>app.getTodayEntries(status,params);
function View(){
 app.useArticleList("today",null,getter);
 loadMore=app.useLoadMore().handleLoadMore;
 const content=app.useStore(app.contentState);
 return React.createElement("output",null,content.entries.map(e=>e.id).join(","));
}
const root=createRoot(document.getElementById("fixture"));
const entry=(id,publication="2026-10-02T10:00:00Z")=>({id,hash:String(id),title:"fixture "+id,url:"https://example.test/"+id,content:"<p>fixture</p>",feed_id:7,feed:{id:7,title:"fixture",category:{id:1,title:"fixture"}},enclosures:[],status:"unread",published_at:publication});
const settle=async()=>{await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve))};
const respond=async(request)=>React.act(async()=>{request.resolve(request.response);await settle()});
try{
 await React.act(async()=>{root.render(React.createElement(app.Provider,null,React.createElement(View)));await settle()});
 assert.equal(pendingLists.length,0,"cold start gates the real list hook");
 await React.act(async()=>{pendingMe({id:1,timezone:"Asia/Shanghai"});await settle()});
 assert.equal(pendingLists.length,1);
 await respond(pendingLists.shift());
 assert.equal(app.contentState.get().total,4);assert.equal(app.dataState.get().unreadTodayCount,5);
 assert.equal(document.querySelector("output").textContent,"11");
 let oldMorePromise;
 await React.act(async()=>{oldMorePromise=loadMore(getter);await settle()});
 const oldMore=pendingLists.shift();assert.equal(oldMore.query.get("offset"),"1");
 await React.act(async()=>{now=NativeDate.parse("2026-10-02T16:00:00Z");window.dispatchEvent(new window.Event("focus"));await settle()});
 assert.equal(document.querySelector("output").textContent,"","resume clears old day rows before the fresh response");
 assert.equal(app.contentState.get().articleListOffset,0);
 const fresh=pendingLists.shift();assert.equal(fresh.query.get("published_after"),"1790956800");
 await React.act(async()=>{oldMore.resolve(oldMore.response);await oldMorePromise;await settle()});
 assert.equal(document.querySelector("output").textContent,"","late old-day load-more is rejected");
 await React.act(async()=>{await loadMore(getter);await settle()});
 assert.equal(pendingLists.length,0,"load-more is gated while the new initial list is pending");
 await respond(fresh);
 assert.equal(app.contentState.get().total,2);assert.equal(app.dataState.get().unreadTodayCount,3);
 assert.equal(fresh.query.get("status"),"unread");assert.equal(fresh.query.get("ai_min"),"8");
 assert.equal(document.querySelector("output").textContent,"21");assert.equal(app.contentState.get().articleListOffset,1);
 // Pending initial list from the old zone must not replace the new zone's list.
 await React.act(async()=>{app.invalidateArticleList();await settle()});const oldList=pendingLists.shift();
 await React.act(async()=>{app.commitIdentityData({id:1,timezone:"UTC"});await settle()});const utcList=pendingLists.shift();
 assert.equal(utcList.query.get("published_after"),"1790899200");
 await respond(utcList);await respond(oldList);
 assert.equal(document.querySelector("output").textContent,"11");
 await React.act(async()=>{app.invalidateArticleList();await settle()});const oldAccount=pendingLists.shift();
 await React.act(async()=>{app.setAuth({...auth,token:"other"});await settle()});
 assert.equal(document.querySelector("output").textContent,"");
 await respond(oldAccount);
 assert.equal(document.querySelector("output").textContent,"","old account response cannot publish");
 await React.act(async()=>{pendingMe({id:2,timezone:"Asia/Shanghai"});await settle()});
 await respond(pendingLists.shift());
 assert.equal(app.contentState.get().total,1);assert.equal(app.dataState.get().unreadTodayCount,1);
 assert.equal(document.querySelector("output").textContent,"41");
 // Observe actual optimistic raw Today badge changes, including read -> unread.
 await React.act(async()=>{app.setUnreadTodayCount(5);app.updateEntriesStatusOptimistically([entry(51,"2026-10-02T15:00:00Z")],"read");await settle()});
 assert.equal(app.dataState.get().unreadTodayCount,5,"yesterday within 24h leaves Today badge unchanged");
 await React.act(async()=>{app.updateEntriesStatusOptimistically([entry(52,"2026-10-02T17:00:00Z")],"read");await settle()});
 assert.equal(app.dataState.get().unreadTodayCount,4);
 await React.act(async()=>{app.updateEntriesStatusOptimistically([{...entry(52,"2026-10-02T17:00:00Z"),status:"read"}],"unread");await settle()});
 assert.equal(app.dataState.get().unreadTodayCount,5);
 console.log("PASS real provider gate, midnight/resume, stale initial/load-more/zone/account responses, optimistic Today badges");
}finally{
 await React.act(async()=>root.unmount());dom.window.close();globalThis.Date=NativeDate;await rm(directory,{recursive:true,force:true});
}
