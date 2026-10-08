import { retainTestDirectory } from './retain_test_directory.mjs'
// Real generated sorter, stores, request hook and URL construction. API transport,
// translations, SidebarTrigger and surrounding navigation-provider values are
// fixtures. A simple output observes list state; jsdom has no layout. The hosted
// Chromium test separately checks the actual rendered article rows.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdtemp, writeFile } from "node:fs/promises";
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
export {contentState,invalidateArticleList} from '@/store/contentState';
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
  { id: 101, score: 9, technical: 6, published_at: '2026-10-01T08:00:00Z', note_updated_at: '2026-10-01T08:20:00Z' },
  { id: 102, score: 6, technical: 9, published_at: '2026-10-01T08:10:00Z', note_updated_at: '2026-10-01T08:00:00Z' },
  { id: 103, score: 8, technical: 7, published_at: '2026-10-01T08:20:00Z', note_updated_at: '2026-10-01T08:10:00Z' },
];
globalThis.sortApi = async (method, url) => {
  assert.equal(method, "GET", "sort never writes API state");
  const query = Object.fromEntries(
    new URL(url, "http://synthetic.test").searchParams,
  );
  requests.push({ url, query });
  const field = query.ai_sort || "score",
    key = {time: 'published_at', note_updated: 'note_updated_at'}[field] || field;
  const sortNumber = entry => typeof entry[key] === 'string' ? Date.parse(entry[key]) : entry[key];
  return {
    total: 3,
    entries: [...fixtures]
      .sort((a, b) => (sortNumber(a) - sortNumber(b)) * (query.direction === "asc" ? 1 : -1))
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
        ai: {state: 'done', score: e.score, has_note: true, note_updated_at: e.note_updated_at},
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
const observations = [];
const selectSort = async (value, expected, view = 'recommended', hasNote = false) => {
  const before = requests.length, err = errors.length;
  await React.act(async () => {
    const select = document.querySelector('select[aria-label="排序方式"]');
    select.value = value;
    select.dispatchEvent(new window.Event('change', {bubbles: true}));
  });
  await settle();
  observations.push({value, expected, view, hasNote,
    selector: document.querySelector('select[aria-label="排序方式"]').value,
    requested: requests.slice(before), displayed: document.querySelector('output').dataset.order,
    persisted: JSON.parse(localStorage.getItem('ai-view-state')), errors: errors.slice(err)});
};
try {
  await React.act(async () =>
    root.render(
      React.createElement(
        app.MemoryRouter,
        { initialEntries: ["/today"] },
        React.createElement(View),
      ),
    ),
  );
  await settle();
  assert.equal(document.querySelector("output").dataset.order, "101,103,102");
  for (const [value, expected] of [
    ["score_asc", "102,103,101"],
    ["technical_desc", "102,103,101"],
    ["published_at_asc", "101,102,103"],
  ]) {
    await selectSort(value, expected);
  }
  for (const mode of ['all', 'recommended']) {
    await React.act(async () => {
      app.aiState.set({...app.aiState.get(), mode, auxiliary: 'notes', sort: 'score', direction: 'desc'});
      app.invalidateArticleList();
    });
    await settle();
    const defaultField = mode === 'all' ? 'note_updated' : 'score';
    assert.equal(document.querySelector('select[aria-label="排序方式"]').value, defaultField + '_desc');
    assert.equal(requests.at(-1).query.ai_sort, defaultField, 'entering notes preserves its existing default');
    assert.equal(app.aiState.get().sort, 'score', 'display fallback does not overwrite the stored preference');
    for (const [value, expected] of [
      ['published_at_asc', '101,102,103'], ['published_at_desc', '103,102,101'],
      ['note_updated_asc', '102,103,101'], ['note_updated_desc', '101,103,102'],
    ]) await selectSort(value, expected, mode === 'all' ? 'notes' : 'recommended', true);
  }
  console.log(
    JSON.stringify(
      {
        runtime:
          "Real generated React component, stores, list hook, entries URL builder; synthetic API and jsdom",
        observations,
      },
      null,
      2,
    ),
  );
  await writeFile(
    new URL("../runtime/sort-component.json", import.meta.url),
    JSON.stringify({ observations, requests, errors }, null, 2),
  );
  assert.equal(errors.length, 0);
  for (const row of observations) {
    assert.equal(row.selector, row.value);
    assert.equal(row.requested.length, 1);
    assert.equal(row.displayed, row.expected);
    assert.equal(row.requested[0].query.ai_view, row.view);
    assert.equal(row.requested[0].query.has_note, row.hasNote ? 'true' : undefined);
    const field = row.value.slice(0, row.value.lastIndexOf("_"));
    assert.equal(
      row.requested[0].query.ai_sort,
      field === "published_at" ? "time" : field,
    );
    assert.equal(
      row.requested[0].query.direction,
      row.value.slice(row.value.lastIndexOf("_") + 1),
    );
    assert.ok(
      row.requested[0].query.published_after,
      "Today scope survives sort",
    );
    assert.equal(row.persisted.sort, field === "published_at" ? "time" : field);
    assert.equal(row.persisted.direction, row.requested[0].query.direction);
  }
} finally {
  await React.act(async () => root.unmount());
  dom.window.close();
  await retainTestDirectory(directory);
}
