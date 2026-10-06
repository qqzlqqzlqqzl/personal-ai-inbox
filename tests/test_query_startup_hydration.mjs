import { retainTestDirectory } from './retain_test_directory.mjs'
// Real generated AiToolbar, list hook and stores. Server hydration is held
// explicitly; this checks query ownership, not browser layout or painted frames.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp, writeFile, readFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
if (!process.argv[2]) {
  for (const [minimum, order] of [["6", "settings-first"], ["8", "settings-first"], ["6", "entries-first"], ["8", "entries-first"]]) {
    const child = spawnSync(process.execPath, [fileURLToPath(import.meta.url), minimum, order], {
      encoding: "utf8", timeout: 40000, env: process.env,
    });
    process.stdout.write(child.stdout || "");
    process.stderr.write(child.stderr || "");
    assert.equal(child.status, 0, `startup minimum ${minimum}, ${order} failed`);
  }
  console.log("PASS: mismatched startup request stays unowned; aligned fixture accepts its first response");
  process.exit(0);
}
assert.ok(["6", "8"].includes(process.argv[2]));
const responseOrder=process.argv[3]||"settings-first";
assert.ok(["settings-first","entries-first"].includes(responseOrder));
const web = new URL("../upstream/reactflux/", import.meta.url).pathname;
const sourceWeb = process.env.READER_UX_SOURCE || web;
const require = createRequire(import.meta.url);
const webRequire = createRequire(web + "package.json");
const { build } = createRequire(webRequire.resolve("vite"))("esbuild");
const { JSDOM } = createRequire(
  new URL("../runtime/history-test-tools/package.json", import.meta.url),
)("jsdom");
const dom = new JSDOM('<!doctype html><main id="fixture"></main>', {
  url: "http://synthetic.test/inbox/all", pretendToBeVisual:true,
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
export {aiState,getAiQuery} from '@/store/aiState';
export {default as AiToolbar} from '@/components/Ai/AiToolbar';
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
        b.onResolve({ filter: /(?:SidebarTrigger|AiPanel|NavigationPalette)(?:\.jsx)?$/ }, () => ({
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
app.aiState.set({...app.aiState.get(),mode:"recommended",auxiliary:"none",minimum:8,hydrated:false});

const serverMinimum=Number(process.argv[2]||6);
let releaseSettings;
const apiCalls=[];
globalThis.sortApi=(method,path)=>{
 apiCalls.push([method,path]);
 if(method==='GET'&&path==='/v1/ai/settings')return new Promise(resolve=>{releaseSettings=resolve});
 if(method==='GET'&&path==='/v1/ai/status')return Promise.resolve({counts:{},kaggle:{enabled:false}});
 throw Error('unexpected synthetic API');
};
const pending=[];
const fetchEntries=()=>new Promise(resolve=>pending.push({resolve,query:app.getAiQuery()}));
const response={total:1965,entries:[{id:101,hash:'101',title:'Synthetic',content:'<p>Isolated</p>',
 url:'https://example.test/101',feed_id:7,feed:{id:7,title:'Fixture',category:{id:1,title:'Fixture'}},
 enclosures:[],status:'read',published_at:'2026-10-04T01:00:00Z'}]};
app.contentState.setKey('infoFrom','all');app.contentState.setKey('infoId','');
const root=createRoot(document.querySelector('#fixture'));
function View(){app.useArticleList('all','',fetchEntries);return React.createElement(app.AiToolbar,{source:'all'})}
const read=()=>({minimum:app.aiState.get().minimum,ready:app.contentState.get().isArticleListReady,
 count:app.dynamicCountState.get(),total:app.contentState.get().total,entryRequests:pending.length});
await React.act(async()=>root.render(React.createElement(View)));
assert.equal(pending.length,1);assert.equal(pending[0].query.ai_min,8);
const stages={before_settings:read()};
if(responseOrder==='entries-first'){
 await React.act(async()=>pending[0].resolve(response));
 stages.fast_response=read();
 assert.equal(stages.fast_response.count,1965,'the valid fast initial response is owned before settings arrive');
}
await React.act(async()=>releaseSettings({minimum_score:serverMinimum}));
stages.after_settings=read();
if(responseOrder==='settings-first')await React.act(async()=>pending[0].resolve(response));
stages.after_first_response=read();
if(serverMinimum!==8){
 assert.equal(pending.length,2);assert.equal(pending[1].query.ai_min,serverMinimum);
 assert.equal(stages.after_first_response.count,null);assert.equal(stages.after_first_response.ready,false);
 await React.act(async()=>pending[1].resolve(response));stages.after_current_response=read();
 assert.equal(stages.after_current_response.count,1965);
}else{
 assert.equal(pending.length,1);assert.equal(stages.after_first_response.count,1965);
}
assert.equal(apiCalls.filter(([m])=>m!=='GET').length,0);
console.log(JSON.stringify({kind:'actual_F_components_synthetic_response_order_not_browser',serverMinimum,responseOrder,stages,
 requests:pending.map(p=>p.query),apiCalls},null,2));
await React.act(async()=>root.unmount());dom.window.close();
await retainTestDirectory(directory);
