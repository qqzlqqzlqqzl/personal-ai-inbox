import { retainTestDirectory } from './retain_test_directory.mjs'
// Real generated request hook and stores in React + jsdom. Responses are held
// explicitly; this checks logical ownership, not browser layout or painted frames.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp, writeFile, readFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
const web = new URL("../upstream/reactflux/", import.meta.url).pathname;
const sourceWeb = process.env.READER_UX_SOURCE || web;
const require = createRequire(import.meta.url);
const webRequire = createRequire(web + "package.json");
const { build } = createRequire(webRequire.resolve("vite"))("esbuild");
const { JSDOM } = createRequire(
  new URL("../runtime/history-test-tools/package.json", import.meta.url),
)("jsdom");
const dom = new JSDOM('<!doctype html><main id="fixture"></main>', {
  url: "http://synthetic.test/inbox/today",
});
Object.assign(globalThis, {
  window: dom.window,
  document: dom.window.document,
  localStorage: dom.window.localStorage,
  sessionStorage: dom.window.sessionStorage,
  getComputedStyle: dom.window.getComputedStyle,
  IS_REACT_ACT_ENVIRONMENT: true,
});
Object.defineProperty(globalThis, "navigator", {
  value: dom.window.navigator,
  configurable: true,
});
for (const name of [
  "HTMLElement",
  "Element",
  "Node",
  "SVGElement",
  "MutationObserver",
  "HTMLIFrameElement",
  "HTMLInputElement",
  "HTMLButtonElement",
  "ShadowRoot",
  "Document",
  "DOMParser",
])
  globalThis[name] = dom.window[name];
globalThis.matchMedia = dom.window.matchMedia = () => ({
  matches: false,
  addListener() {},
  removeListener() {},
  addEventListener() {},
  removeEventListener() {},
});
globalThis.requestAnimationFrame = (cb) => setTimeout(cb, 0);
globalThis.cancelAnimationFrame = clearTimeout;
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
const errors = [];
window.addEventListener("error", (e) => {
  errors.push(e.message);
  e.preventDefault();
});
const React = webRequire("react"),
  { createRoot } = webRequire("react-dom/client");
const directory = await mkdtemp(join(tmpdir(), "reader-sidebar-count-"));
const output = join(directory, "sidebar-count.cjs");
await build({
  stdin: {
    contents: `export {SidebarMenuItems,CategoryTitle,FeedMenuItem} from '@/components/Sidebar/Sidebar';
export {aiState} from '@/store/aiState';
export {contentState,dynamicCountState,articleListResultReadyState,resetContent} from '@/store/contentState';
export {default as useArticleList} from '@/hooks/useArticleList';
export {useStore} from '@nanostores/react';
export {settingsState,updateSettings} from '@/store/settingsState';
export {setAuth,resetAuth} from '@/store/authState';
export {commitIdentityData,dataState,resetData} from '@/store/dataState';
export {startSidebarScopeCounts,sidebarScopeCountsState,validateScopeCounts} from '@/store/sidebarScopeCountsState';
export {MemoryRouter} from 'react-router';
export {polyglotState} from '@/hooks/useLanguage';`,
    resolveDir: web,
  },
  outfile: output,
  bundle: true,
  platform: "node",
  format: "cjs",
  jsx: "automatic",
  alias: { "@": sourceWeb + "src" },
  nodePaths: [web + "node_modules"],
  define: { "import.meta.env.BASE_URL": '"/inbox/"' },
  plugins: [
    {
      name: "fixtures",
      setup(b) {
        // Only expose the actual generated private components; do not replace
        // their count projection, store subscriptions or rendered count nodes.
        b.onLoad({filter:/components\/Sidebar\/Sidebar\.jsx$/},async({path})=>({
          contents:(await readFile(path,'utf8'))+'\nexport {SidebarMenuItems,CategoryTitle,FeedMenuItem};',loader:'jsx'}));
        b.onResolve(
          {
            filter:
              /^(react(?:\/.*)?|react-dom(?:\/.*)?|@arco-design\/web-react(?:\/.*)?)$/,
          },
          ({ path }) => ({ path: webRequire.resolve(path), external: true }),
        );
        b.onResolve({ filter: /(?:^|\/)ofetch(?:\.js)?$/ }, () => ({
          path: "api",
          namespace: "fixture",
        }));
        b.onResolve({ filter: /SidebarTrigger(?:\.jsx)?$/ }, () => ({
          path: "sidebar",
          namespace: "fixture",
        }));
        b.onResolve({ filter: /\.css$/ }, () => ({
          path: "css",
          namespace: "fixture",
        }));
        b.onLoad({ filter: /.*/, namespace: "fixture" }, ({ path }) => ({
          contents:
            path === "api"
              ? `export default {get:(...args)=>globalThis.sortApi('GET',...args),put:(...args)=>globalThis.sortApi('PUT',...args),post:(...args)=>globalThis.sortApi('POST',...args)}`
              : path === "sidebar"
                ? "export default ()=>null"
                : "",
          loader: "js",
        }));
      },
    },
  ],
});
const app=require(output),checks=[],apiCalls=[];
const Polyglot=webRequire('node-polyglot');
app.polyglotState.set({polyglot:new Polyglot({phrases:JSON.parse(await readFile(web+'src/locales/zh-CN.json','utf8')),locale:'zh-CN'})});
const equal=(actual,expected,label)=>{assert.deepEqual(actual,expected,label);checks.push(label)};
globalThis.sortApi=(...args)=>{apiCalls.push(args);throw Error('Unexpected API: count presentation must not fetch')};
const category={id:1,title:'Synthetic category',hide_globally:false};
const feed={id:7,title:'Synthetic feed',feed_url:'https://example.test/feed',site_url:'https://example.test',category,icon:{feed_id:7,icon_id:0},unreadCount:8088};
app.setAuth({server:'http://synthetic.test/mf',token:'fixture',username:'',password:''});
app.commitIdentityData({id:1,timezone:'Asia/Shanghai'});
app.dataState.setKey('categoriesData',[category]);app.dataState.setKey('feedsData',[feed]);app.dataState.setKey('unreadInfo',{7:8088});app.dataState.setKey('unreadTodayCount',23);
app.updateSettings({showStatus:'unread',language:'zh-CN'});
app.aiState.set({...app.aiState.get(),mode:'recommended',auxiliary:'none',minimum:8,hydrated:true});
const root=createRoot(document.querySelector('#fixture'));
const render=async(scope,total)=>React.act(async()=>{
 app.contentState.setKey('infoFrom',scope);app.contentState.setKey('infoId',null);
 root.render(React.createElement(app.MemoryRouter,{key:scope,initialEntries:['/'+scope]},React.createElement(app.SidebarMenuItems,{infoFrom:scope,activeScopeCount:total})));
});
const visibleCount=node=>node?.querySelector('.arco-ellipsis-content .arco-ellipsis-text')?.textContent??'';
const allCount=()=>visibleCount(document.querySelectorAll('.custom-menu-item .item-count')[0]);
const todayCount=()=>visibleCount(document.querySelectorAll('.custom-menu-item .item-count')[1]);
let stopBatch;
try {
 await render('all',1965);equal(allCount(),'1965','active All uses the verified AI total supplied by the original owner gate');
 await render('today',0);
 console.log(JSON.stringify({case:'all_to_empty_today_same_AI_unread_lens',renderedItems:[...document.querySelectorAll('.custom-menu-item')].map(x=>x.textContent),allCount:allCount(),todayCount:todayCount(),apiCalls:apiCalls.length}));
 equal(allCount(),'','inactive All never falls back to 8088 native unread under the same AI lens');
 equal(todayCount(),'0','Today zero remains local and is not borrowed by All');
 equal(apiCalls.length,0,'All to Today count rendering makes zero API requests');
 const setLens=async({mode='all',auxiliary='none',hydrated=true,status='unread',search='',date=null}={})=>React.act(async()=>{
  app.aiState.set({...app.aiState.get(),mode,auxiliary,hydrated});app.updateSettings({showStatus:status});
  app.contentState.setKey('filterString',search);app.contentState.setKey('filterDate',date);
 });
 await setLens();equal(allCount(),'8088','unfiltered raw-unread inactive All retains its native unread count');
 for(const [name,lens] of [['AI recommended',{mode:'recommended'}],['AI notes',{mode:'recommended',auxiliary:'notes'}],['raw notes',{auxiliary:'notes'}],['pending',{auxiliary:'pending'}],['all statuses',{status:'all'}],['starred status',{status:'starred'}],['search',{search:'cache'}],['date',{date:'2026-10-05'}],['unhydrated',{hydrated:false}],['malformed hydration',{hydrated:'yes'}],['unknown mode',{mode:'unknown'}],['unknown auxiliary',{auxiliary:'unknown'}]]) {
  await setLens(lens);equal(allCount(),'',name+': inactive All has no proven matching total');
 }
 await setLens({mode:'recommended'});await render('all',null);equal(allCount(),'','pending/error active total is not replaced by native unread');
 await render('all',0);equal(allCount(),'0','valid active zero is shown and never falls back');
 await render('all',17);equal(allCount(),'17','positive active owned total remains visible');
 const entityRender=async(kind,scope,total)=>React.act(async()=>root.render(React.createElement(app.MemoryRouter,{key:kind+scope,initialEntries:['/today']},React.createElement(kind==='category'?app.CategoryTitle:app.FeedMenuItem,{
  category,feed,activeScope:scope,activeScopeId:kind==='category'?1:7,activeScopeCount:total,path:'/today',homePageReady:false,homeTarget:{type:'view',id:'today'},
 }))));
 for(const kind of ['category','feed']) {
  const count=()=>visibleCount(document.querySelector(kind==='category'?'.unread-count':'.item-count'));
  await setLens();await entityRender(kind,'today',0);equal(count(),'8088',kind+': native unread is available only in raw-unread view');
  await setLens({mode:'recommended'});equal(count(),'',kind+': AI lens hides unmatched native unread');
  await entityRender(kind,kind,5);equal(count(),'5',kind+': active owned result remains visible');
  await entityRender(kind,kind,null);equal(count(),'',kind+': active pending does not fall back');
  await entityRender(kind,kind,0);equal(count(),'0',kind+': active zero is shown and does not fall back');
 }
 equal(apiCalls.length,0,'menu/category/feed projections and lens changes add zero API requests');
 // Compose the real generated request hook, owner stores and sidebar menu.
 // The fetch boundary counts the existing list calls and permits late completion.
 const listCalls=[],pending=[];
 const fetchEntries=(...args)=>new Promise((resolve,reject)=>{const request={scope:app.contentState.get().infoFrom,args,resolve,reject};listCalls.push(request);pending.push(request)});
 const response=(total,id=101)=>({total,entries:total?[{id,hash:String(id),title:'Synthetic '+id,content:'<p>Isolated body</p>',url:'https://example.test/'+id,feed_id:7,feed,enclosures:[],status:'unread',published_at:'2026-10-05T01:00:00Z'}]:[]});
 function OwnerView({scope}) {
  app.useArticleList(scope,null,fetchEntries);
  const count=app.useStore(app.dynamicCountState),ready=app.useStore(app.articleListResultReadyState);
  return React.createElement(app.SidebarMenuItems,{infoFrom:scope,activeScopeCount:ready?count:null});
 }
 const navigate=async(scope)=>React.act(async()=>{
  app.contentState.setKey('infoFrom',scope);app.contentState.setKey('infoId',null);
  root.render(React.createElement(app.MemoryRouter,{key:'live-'+scope,initialEntries:['/'+scope]},React.createElement(OwnerView,{scope})));
 });
 const release=async(request,value)=>React.act(async()=>request.resolve(value));
 await setLens({mode:'recommended'});await navigate('all');equal(listCalls.length,1,'active All starts only its existing list request');
 await release(pending.shift(),response(1965));equal(allCount(),'1965','real owner hook publishes active AI All total');
 await navigate('today');equal(listCalls.length,2,'All to Today starts one existing list request, no sidebar queries');
 equal(allCount(),'','pending Today does not expose raw All');
 await release(pending.shift(),response(0));equal(app.dynamicCountState.get(),0,'real Today owns its valid zero');equal(allCount(),'','settled Today zero is not borrowed by inactive All');
 await navigate('all');const oldAll=pending.shift();await navigate('today');const today=pending.shift();
 await release(today,response(2,102));await release(oldAll,response(9999,103));
 equal(todayCount(),'2','late All cannot replace the settled Today owner');equal(allCount(),'','late All is not cached into inactive count');
 equal(listCalls.length,4,'rapid two-route change adds exactly two existing list calls');
 await React.act(async()=>app.aiState.setKey('minimum',9));const oldLens=pending.shift();
 await React.act(async()=>app.contentState.setKey('filterString','updated filter'));const newLens=pending.shift();
 await release(oldLens,response(777,104));equal(todayCount(),'','old lens cannot satisfy pending search');
 await release(newLens,response(3,105));equal(todayCount(),'3','new search publishes only its own active count');equal(allCount(),'','search never exposes native inactive count');
 await navigate('all');const failed=pending.shift(),originalError=console.error;console.error=()=>{};
 try {await React.act(async()=>failed.reject(Error('synthetic fetch failure')))} finally {console.error=originalError}
 equal(app.contentState.get().articleListError,true,'failed list remains an error');equal(allCount(),'','failed current owner does not show 8088 or false zero');
 await navigate('today');const oldSession=pending.shift();await React.act(async()=>app.dataState.setKey('sessionRevision',app.dataState.get().sessionRevision+1));
 await release(oldSession,response(888,106));equal(todayCount(),'','old session cannot publish sidebar count');
 const beforeLogout=listCalls.length;
 await React.act(async()=>{app.resetAuth();app.resetData();app.resetContent()});
 equal(app.dynamicCountState.get(),null,'logout clears current count ownership');equal(allCount(),'','logout does not leak previous raw total');
 equal(listCalls.length,beforeLogout,'logout does not spawn speculative scope count requests');
 for(const late of pending.splice(0))await release(late,response(99999,999));
 equal(app.dynamicCountState.get(),null,'responses completing after logout cannot republish a count');
 equal(app.contentState.get().entries,[],'responses completing after logout cannot republish rows');
 equal(apiCalls.length,0,'no direct API requests or mutations from count display');
 // The provider's batch bootstrap is independent of navigation and list totals.
 const batches=[];
 globalThis.sortApi=(method,path,options)=>{
  apiCalls.push([method,path,options]);
  assert.equal(method,'GET');assert.ok(path.startsWith('/v1/ai/scope-counts?'));
  assert.equal(options.retry,0);assert.equal(options.timeout,15000);
  return new Promise((resolve,reject)=>batches.push({resolve,reject,path,options}));
 };
 const counts={all:1965,today:0,starred:2,history:7,category:{'1':5},feed:{'7':0}};
 await React.act(async()=>{
  app.setAuth({server:'http://synthetic.test/mf',token:'fixture',username:'',password:''});
  app.resetData();app.resetContent();
  app.aiState.set({...app.aiState.get(),mode:'recommended',minimum:8,auxiliary:'none',hydrated:false});
  app.updateSettings({showStatus:'unread'});
  stopBatch=app.startSidebarScopeCounts();
 });
 await render('today',null);
 equal(batches.length,0,'cold first open waits for identity and AI settings');
 await React.act(async()=>app.commitIdentityData({id:1,timezone:'Asia/Shanghai'}));
 equal(batches.length,0,'identity alone does not use unhydrated AI preferences');
 await React.act(async()=>{
  app.aiState.setKey('hydrated',true);
  app.contentState.setKey('articleListRevision',app.contentState.get().articleListRevision+1);
 });
 equal(batches.length,1,'first ready AI lens makes one request for all scopes');
 const initialQuery=new URL('http://synthetic.test'+batches[0].path).searchParams;
 equal(initialQuery.get('ai_min'),'8','batch uses the same AI minimum');
 equal(initialQuery.get('status'),'unread','batch uses the same status');
 assert.ok(Number(initialQuery.get('today_after'))>0);
 assert.equal(initialQuery.has('published_after'),false,'Today cutoff does not filter every other scope');
 await release(batches[0],{scope_counts:counts});
 equal(allCount(),'1965','first open shows inactive All without clicking it');
 equal(todayCount(),'0','first open shows known Today zero without borrowing All');
 await entityRender('category','today',null);
 equal(visibleCount(document.querySelector('.unread-count')),'5','first open shows unvisited category');
 await entityRender('feed','today',null);
 equal(visibleCount(document.querySelector('.item-count')),'0','first open shows unvisited feed zero');
 await render('all',null);equal(allCount(),'1965','navigation uses the verified batch');
 equal(batches.length,1,'navigation and projections add no batch requests');
 await React.act(async()=>app.aiState.setKey('minimum',6));const oldBatch=batches[1];
 await React.act(async()=>app.aiState.setKey('minimum',7));const currentBatch=batches[2];
 equal(oldBatch.options.signal.aborted,true,'a newer lens aborts the old batch');
 await release(oldBatch,{scope_counts:{...counts,all:9999}});
 equal(allCount(),'','a late old lens remains unknown');
 await release(currentBatch,{scope_counts:{...counts,all:17}});
 equal(allCount(),'17','only the current lens batch publishes');
 await React.act(async()=>app.contentState.setKey('filterDate','2026-10-05'));
 const dateBatch=batches[3],dateQuery=new URL('http://synthetic.test'+dateBatch.path).searchParams;
 assert.ok(Number(dateQuery.get('date_before'))>Number(dateQuery.get('date_after')));
 equal(dateQuery.get('date_field'),app.settingsState.get().orderBy,'date order matches list settings');
 await React.act(async()=>dateBatch.reject(Error('synthetic failed batch')));
 equal(allCount(),'','a failed batch does not publish a false zero or native count');
 assert.throws(()=>app.validateScopeCounts({scope_counts:{...counts,today:false}}),TypeError);
 assert.throws(()=>app.validateScopeCounts({scope_counts:{...counts,feed:{'7':true}}}),TypeError);
 assert.throws(()=>app.validateScopeCounts({scope_counts:{...counts,category:null}}),TypeError);
 await React.act(async()=>app.contentState.setKey('filterDate',null));const oldAccountBatch=batches[4];
 await React.act(async()=>{app.resetAuth();app.resetData()});
 await release(oldAccountBatch,{scope_counts:{...counts,all:9999}});
 equal(app.sidebarScopeCountsState.get(),null,'logout rejects the old account batch');
 equal(batches.length,5,'logout starts no extra request');
 equal(apiCalls.filter(([method])=>method!=='GET').length,0,'batch bootstrap makes zero writes');
 equal(errors,[],'no runtime window errors');
 console.log(JSON.stringify({listRequests:listCalls.map(({scope,args})=>({scope,args})),extraApiCalls:apiCalls.length}));

 console.log(JSON.stringify({type:'actual generated Sidebar components with React/jsdom, no layout',checks},null,2));
} finally {await React.act(async()=>{stopBatch?.();root.unmount()});dom.window.close();await retainTestDirectory(directory)}
