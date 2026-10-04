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
const directory = await mkdtemp(join(tmpdir(), "reader-query-owner-"));
const output = join(directory, "query-owner.cjs");
await build({
  stdin: {
    contents: `export {default as useArticleList} from '@/hooks/useArticleList';
export {aiState} from '@/store/aiState';
export {contentState,dynamicCountState} from '@/store/contentState';
export {settingsState,updateSettings} from '@/store/settingsState';
export {sanitizeSettings,decodeSettings} from '@/utils/settings-schema';
export {setAuth} from '@/store/authState';
export {commitIdentityData,dataState} from '@/store/dataState';
export {useStore} from '@nanostores/react';`,
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
const app = require(output);
app.setAuth({server:"http://synthetic.test/mf",token:"fixture",username:"",password:""});
app.commitIdentityData({id:1,timezone:"Asia/Shanghai"});
app.updateSettings({showStatus:"all"});
app.aiState.set({...app.aiState.get(),mode:"recommended",auxiliary:"none",minimum:8,hydrated:true});
const pending=[];
const response = (total,id=101) => ({total,entries:total?[{id,hash:String(id),title:"Synthetic "+id,
 content:"<p>Synthetic article</p>",url:"https://example.test/"+id,feed_id:7,
 feed:{id:7,title:"fixture",category:{id:1,title:"fixture"}},enclosures:[],status:"read",
 published_at:"2026-10-01T08:00:00Z"}]:[]});
globalThis.sortApi=()=>{throw Error("Unexpected API; no mutation allowed")};
const fetchEntries=()=>new Promise((resolve,reject)=>pending.push({resolve,reject}));
const root=createRoot(document.querySelector("#fixture"));
function View({scope}) { app.useArticleList(scope,null,fetchEntries); return null; }
const navigate=async(scope)=>React.act(async()=>{
 app.contentState.setKey("infoFrom",scope);app.contentState.setKey("infoId",null);
 root.render(React.createElement(View,{scope}));
});
const release=async(request,result)=>React.act(async()=>request.resolve(result));
const checks=[];
const equal=(actual,expected,label)=>{assert.deepEqual(actual,expected,label);checks.push(label)};
try {
 await navigate("all");await release(pending.shift(),response(1965));
 const sidebar=await readFile(sourceWeb+"src/components/Sidebar/Sidebar.jsx","utf8");
 const begin=sidebar.indexOf('  const { infoFrom, infoId } = useStore(contentState');
 const end=sidebar.indexOf('  const {\n    identityError',begin);
 const project=new Function('useStore','contentState','dynamicCountState','articleListResultReadyState','currentPath',
   sidebar.slice(begin,end)+'; return {activeScope:typeof activeScope==="undefined"?infoFrom:activeScope,activeScopeId:typeof activeScopeId==="undefined"?infoId:activeScopeId,activeScopeCount}');
 const selected=(path,ready=true)=>project(s=>s.get(),app.contentState,app.dynamicCountState,{get:()=>ready},path);
 equal(selected('/all').activeScopeCount,1965,"active route uses settled owned result");
 equal(selected('/today').activeScopeCount,null,"router commit before store does not expose native or old AI total");
 equal(selected('/all',false).activeScopeCount,null,"not-ready route never falls back to a native count");
 equal(selected('/feed/7/entry/101').activeScopeId,"7","entry route preserves feed scope identity");

 equal(app.dynamicCountState.get(),1965,"settled all owns its total");
 // A route/store commit may precede the effect that clears readiness.
 await React.act(async()=>{app.contentState.setKey("infoFrom","today");
  equal(app.dynamicCountState.get(),null,"today must not adopt retained all total before layout effect");});
 await navigate("today");const oldToday=pending.shift();
 equal(app.dynamicCountState.get(),null,"today pending hides retained all total");
 await navigate("all");const newAll=pending.shift();
 await release(oldToday,response(12,102));
 equal(app.dynamicCountState.get(),null,"late today cannot satisfy reversed all query");
 await release(newAll,response(24,103));equal(app.dynamicCountState.get(),24,"new all result commits");
 // Start all refresh, then switch to today and resolve the old all last.
 await React.act(async()=>app.contentState.setKey("articleListRevision",app.contentState.get().articleListRevision+1));
 const lateAll=pending.shift();await navigate("today");const today=pending.shift();
 await release(today,response(2,104));await release(lateAll,response(999,105));
 equal(app.dynamicCountState.get(),2,"late all cannot overwrite settled today");
 equal(app.contentState.get().entries.map(e=>e.id),[104],"late all cannot replace today's rows");
 await navigate("history");await release(pending.shift(),response(0));
 equal(app.dynamicCountState.get(),0,"valid empty result is zero, not loading");
 await navigate("starred");const failure=pending.shift();
 const originalError=console.error;console.error=()=>{};
 try { await React.act(async()=>failure.reject(Error("synthetic transport failure"))); }
 finally { console.error=originalError; }
 equal(app.contentState.get().articleListError,true,"real error remains visible");
 equal(app.dynamicCountState.get(),null,"error must not claim an empty successful result");
 await navigate("today");await release(pending.shift(),response(3,106));
 await React.act(async()=>app.aiState.setKey("minimum",9));
 equal(app.dynamicCountState.get(),null,"changed AI lens cannot reuse prior owner");
 await release(pending.shift(),response(1,107));equal(app.dynamicCountState.get(),1,"new lens owns total");
 await React.act(async()=>app.contentState.setKey("filterString","synthetic search"));
 equal(app.dynamicCountState.get(),null,"search change invalidates count ownership");
 await release(pending.shift(),response(0));equal(app.dynamicCountState.get(),0,"search empty settles");
 await React.act(async()=>{
  app.aiState.set({...app.aiState.get(),mode:"all",auxiliary:"none"});
  app.updateSettings({showStatus:"unread"});
  app.contentState.setKey("filterString","");app.contentState.setKey("filterDate","2026-10-02");
  app.dataState.setKey("unreadTodayCount",88);
 });
 await release(pending.shift(),response(4,108));
 equal(app.dynamicCountState.get(),4,"raw date-filtered count uses the owned response, not whole-day native unread count");
 await React.act(async()=>app.dataState.setKey("sessionRevision",app.dataState.get().sessionRevision+1));
 equal(app.dynamicCountState.get(),null,"old data session cannot reuse a published total");
 for(const theme of ["light","dark"]){
  const correct=app.sanitizeSettings({themeMode:theme});
  equal(correct.themeMode,theme,"schema accepts "+theme);
  equal(app.decodeSettings(JSON.stringify(correct)).themeMode,theme,"persisted theme survives new document "+theme);
  equal(app.sanitizeSettings({theme}).themeMode,"system","legacy wrong theme key is rejected "+theme);
 }
 console.log(JSON.stringify({type:"synthetic React/jsdom, no layout",checks},null,2));
} finally { await React.act(async()=>root.unmount());dom.window.close();await retainTestDirectory(directory); }
