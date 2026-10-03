// Real generated sorter, stores, request hook and URL construction. API transport,
// translations, SidebarTrigger and surrounding navigation-provider values are
// fixtures. A simple output observes list state; jsdom has no layout. The hosted
// Chromium test separately checks the actual rendered article rows.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
const web = new URL("../upstream/reactflux/", import.meta.url).pathname;
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
const directory = await mkdtemp(join(tmpdir(), "reader-sort-"));
const output = join(directory, "sorter.cjs");
await build({
  stdin: {
    contents: `export {default as SearchAndSortBar} from '@/components/Article/SearchAndSortBar';
export {default as useArticleList} from '@/hooks/useArticleList';
export {ContentContext} from '@/components/Content/ContentContext';
export {aiState,getAiQuery} from '@/store/aiState';
export {contentState,dynamicCountState} from '@/store/contentState';
export {settingsState,updateSettings} from '@/store/settingsState';
export {polyglotState} from '@/hooks/useLanguage';
export {getTodayEntries} from '@/apis/entries';
export {setAuth} from '@/store/authState';
export {commitIdentityData} from '@/store/dataState';
export {MemoryRouter} from 'react-router';
export {useStore} from '@nanostores/react';`,
    resolveDir: web,
  },
  outfile: output,
  bundle: true,
  platform: "node",
  format: "cjs",
  jsx: "automatic",
  alias: { "@": web + "src" },
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
app.polyglotState.set({ polyglot: { t: (key) => key } });
app.contentState.setKey("infoFrom", "today");
const requests = [];
const fixtures = [
  { id: 101, score: 9, technical: 6 },
  { id: 102, score: 6, technical: 9 },
  { id: 103, score: 8, technical: 7 },
];
globalThis.sortApi = async (method, url) => {
  assert.equal(method, "GET", "sort never writes API state");
  const query = Object.fromEntries(
    new URL(url, "http://synthetic.test").searchParams,
  );
  requests.push({ url, query });
  const field = query.ai_sort || "score",
    key = field === "technical" ? "technical" : "score";
  return {
    total: fixtures.filter(e=>e.score>=Number(query.ai_min||0)).length,
    entries: [...fixtures].filter(e=>e.score>=Number(query.ai_min||0))
      .sort((a, b) => (a[key] - b[key]) * (query.direction === "asc" ? 1 : -1))
      .map((e) => ({
        ...e,
        hash: String(e.id),
        title: "sort " + e.id,
        content: "<p>Synthetic entry</p>",
        url: "https://example.test/" + e.id,
        feed_id: 7,
        feed: {
          id: 7,
          title: "fixture",
          category: { id: 1, title: "fixture" },
        },
        enclosures: [],
        status: "read",
        published_at: "2026-10-01T08:00:00Z",
      })),
  };
};
function View() {
  app.useArticleList("today", null, app.getTodayEntries);
  const content = app.useStore(app.contentState);
  return React.createElement(
    app.ContentContext.Provider,
    {
      value: {
        closeActiveContent: () =>
          app.contentState.setKey("activeContent", null),
        entryListRef: { current: null },
      },
    },
    React.createElement(app.SearchAndSortBar),
    React.createElement(
      "output",
      { "data-order": content.entries.map((e) => e.id).join(",") },
      content.entries.map((e) => e.title).join(","),
    ),
  );
}
const root = createRoot(document.querySelector("#fixture"));
const settle = async () =>
  React.act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 100));
  });

try{
 await React.act(async()=>root.render(React.createElement(app.MemoryRouter,{initialEntries:['/today']},React.createElement(View))));await settle();assert.equal(app.contentState.get().total,3);const countBefore=requests.length,old=app.aiState.get(),next={...old,minimum:9};
 await React.act(async()=>{localStorage.setItem('ai-view-state',JSON.stringify(next));window.dispatchEvent(new window.StorageEvent('storage',{key:'ai-view-state',oldValue:JSON.stringify(old),newValue:JSON.stringify(next),storageArea:localStorage,url:window.location.href}));});await settle();
 const result={case:'storage_event_threshold_and_counts',minimum:app.aiState.get().minimum,newRequests:requests.slice(countBefore),ids:app.contentState.get().entries.map(e=>e.id),listTotal:app.contentState.get().total,activeCount:app.dynamicCountState.get(),expectedIds:[101],expectedTotal:1};console.log(JSON.stringify(result,null,2));await writeFile(new URL('../runtime/storage-event-result.json',import.meta.url),JSON.stringify(result,null,2));assert.equal(result.minimum,9);assert.equal(result.newRequests.length,1);assert.deepEqual(result.ids,[101]);assert.equal(result.listTotal,1);assert.equal(result.activeCount,1)
}finally{await React.act(async()=>root.unmount());dom.window.close();await rm(directory,{recursive:true,force:true})}
