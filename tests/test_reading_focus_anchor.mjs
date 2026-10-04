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
const directory = await mkdtemp(join(tmpdir(), "reader-reading-focus-"));
const output = join(directory, "reading-focus.cjs");
await build({
  stdin: {
    contents: `export {default as ReadingControls} from '@/components/Ai/ReadingControls';
export {settingsState,updateSettings} from '@/store/settingsState';`,
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
const app=require(output),checks=[];
app.updateSettings({fontSize:1.2,articleWidth:80,articleLineHeight:1.8});
const equal=(actual,expected,label)=>{assert.equal(actual,expected,label);checks.push(label)};
try {
 for(const kind of ['native-mobile','simplebar-desktop']) {
  document.querySelector('#fixture').innerHTML='<article class="article-content"><section id="outer"><div id="scroller"><main id="mount"></main><header class="article-header"><div class="article-meta">Synthetic metadata</div></header><div class="article-body"><p>A</p><p>B</p><p>C</p><p>D</p><p>E</p></div></div></section></article>';
  const article=document.querySelector('article'),scroll=document.querySelector('#scroller'),outer=document.querySelector('#outer');
  outer.scrollTop=777;scroll.scrollTop=640;
  scroll.getBoundingClientRect=()=>({top:0,bottom:600,height:600});
  Object.defineProperties(scroll,{scrollHeight:{value:2000},clientHeight:{value:600}});
  const paragraphs=[...document.querySelectorAll('.article-body p')];
  const top=i=>(article.classList.contains('review-reading-focus')?120:360)+i*200-scroll.scrollTop;
  paragraphs.forEach((p,i)=>p.getBoundingClientRect=()=>({top:top(i),bottom:top(i)+120,height:120}));
  document.querySelector('.article-body').getBoundingClientRect=()=>({top:top(0),bottom:top(4)+120,height:920});
  const ref={current:{getScrollElement:()=>scroll}},root=createRoot(document.querySelector('#mount'));
  await React.act(async()=>root.render(React.createElement(app.ReadingControls,{scrollContainerRef:ref,maxWidth:'80ch'})));
  const bar=document.querySelector('.review-reading-bar');
  const barHeight=()=>bar.querySelector('[role=status]')?74:52;
  bar.getBoundingClientRect=()=>({top:0,bottom:barHeight(),height:barHeight()});
  const button=bar.querySelector('button[aria-pressed]');
  const offset=()=>paragraphs[2].getBoundingClientRect().top-barHeight();
  let before=offset();button.focus();
  await React.act(async()=>button.dispatchEvent(new window.MouseEvent('click',{bubbles:true})));
  equal(button.getAttribute('aria-pressed'),'true',kind+': focus mode enters');
  equal(offset(),before,kind+': preserves visible body anchor below changed toolbar');
  equal(document.activeElement,button,kind+': explicit toggle retains focus');
  equal(outer.scrollTop,777,kind+': never scrolls the outer article shell');
  scroll.scrollTop+=100;before=offset();
  await React.act(async()=>button.dispatchEvent(new window.MouseEvent('click',{bubbles:true})));
  equal(button.getAttribute('aria-pressed'),'false',kind+': exit remains the same button');
  equal(offset(),before,kind+': exit keeps progressed reading anchor');
  equal(document.activeElement,button,kind+': exit retains focus');
  equal(article.classList.contains('review-reading-focus'),false,kind+': metadata mode restores');
  await React.act(async()=>button.dispatchEvent(new window.MouseEvent('click',{bubbles:true})));
  await React.act(async()=>root.unmount());
  equal(article.classList.contains('review-reading-focus'),false,kind+': article unmount clears focus class');
 }
 const css=await readFile(sourceWeb+'src/components/Ai/ReviewWorkflows.css','utf8');
 assert.match(css,/\.review-reading-bar>button,\.review-reading-controls>summary\{[^}]*box-sizing:border-box[^}]*font:inherit[^}]*line-height:1.4/);
 assert.match(css,/\.review-reading-bar\{[^}]*position:sticky;top:0/);
 checks.push('same summary/button font, line height, border box; sticky in owned scroller (static CSS only)');
 console.log(JSON.stringify({type:'React/jsdom with synthetic geometry; real layout NOT RUN',checks},null,2));
} finally {dom.window.close();await retainTestDirectory(directory)}
