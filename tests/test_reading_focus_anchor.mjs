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
 // No selected semantic block is a normal case: image wrappers, unstructured
 // divs and gaps between blocks must retain the body's actual offset, not zero.
 for(const scenario of [
  {name:'image-only div',markup:'<div class="image-wrapper"><img alt="synthetic"></div>',start:640,point:400},
  {name:'textless custom div',markup:'<div><span></span></div>',start:640,point:400},
  {name:'gap between paragraphs',markup:'<p id="above">A</p><p id="below">B</p>',start:640,point:400},
  {name:'document end without matched blocks',markup:'<div>End of unstructured content</div>',start:1800,point:1700},
 ]) {
  document.querySelector('#fixture').innerHTML=`<article class="article-content"><div id="scroller"><main id="mount"></main><div class="article-body">${scenario.markup}</div></div></article>`;
  const article=document.querySelector('article'),scroll=document.querySelector('#scroller'),body=document.querySelector('.article-body');
  scroll.scrollTop=scenario.start;scroll.getBoundingClientRect=()=>({top:0,bottom:600,height:600});
  Object.defineProperties(scroll,{scrollHeight:{get:()=>article.classList.contains('review-reading-focus')?2182:2400},clientHeight:{value:600}});
  const top=()=>(article.classList.contains('review-reading-focus')?120:360)-scroll.scrollTop;
  body.getBoundingClientRect=()=>({top:top(),bottom:top()+1900,height:1900});
  const above=document.querySelector('#above'),below=document.querySelector('#below');
  if(above)above.getBoundingClientRect=()=>({top:top(),bottom:top()+100,height:100});
  if(below)below.getBoundingClientRect=()=>({top:top()+1400,bottom:top()+1500,height:100});
  const root=createRoot(document.querySelector('#mount')),ref={current:{getScrollElement:()=>scroll}};
  await React.act(async()=>root.render(React.createElement(app.ReadingControls,{scrollContainerRef:ref})));
  const bar=document.querySelector('.review-reading-bar'),height=()=>bar.querySelector('[role=status]')?74:52;
  bar.getBoundingClientRect=()=>({top:0,bottom:height(),height:height()});
  const button=bar.querySelector('button[aria-pressed]'),offset=()=>top()+scenario.point-height(),before=offset();
  await React.act(async()=>button.dispatchEvent(new window.MouseEvent('click',{bubbles:true})));
  equal(offset(),before,scenario.name+': entering focus preserves actual fallback offset');
  await React.act(async()=>button.dispatchEvent(new window.MouseEvent('click',{bubbles:true})));
  equal(offset(),before,scenario.name+': exit preserves offset and remains within scroll limits');
  await React.act(async()=>root.unmount());
 }
 // Real React event ownership only. Native details activation and browser
 // article navigation are covered separately in reading_focus_browser.py.
 document.querySelector('#fixture').innerHTML='<article class="article-content"><div id="mount"></div><div class="article-body"><p>Keyboard fixture</p></div></article>';
 const keyboardRoot=createRoot(document.querySelector('#mount'));
 await React.act(async()=>keyboardRoot.render(React.createElement(app.ReadingControls,{})));
 const details=document.querySelector('.review-reading-controls'),summary=details.querySelector('summary');
 const escaped=[];
 const observeEscape=event=>{if(event.key==='Escape')escaped.push(event)};
 document.addEventListener('keydown',observeEscape);
 try {
  for(const [name,target] of [['summary',summary],['font slider',details.querySelector('input')],['reset button',details.querySelector('button')]]) {
   details.open=true;target.focus();const previous=escaped.length;
   const event=new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true});
   await React.act(async()=>target.dispatchEvent(event));
   equal(details.open,false,name+': Escape closes only reading layout popup');
   equal(document.activeElement,summary,name+': Escape returns focus to popup opener');
   equal(escaped.length,previous,name+': Escape cannot reach article-level document shortcut');
   equal(event.defaultPrevented,true,name+': handled Escape cancels native action');
  }
  details.open=true;const slider=details.querySelector('input');slider.focus();
  await React.act(async()=>slider.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',isComposing:true,bubbles:true,cancelable:true})));
  equal(details.open,true,'IME Escape is not consumed by layout popup');
  equal(document.activeElement,slider,'IME keeps input focus');
  await React.act(async()=>slider.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',keyCode:229,bubbles:true,cancelable:true})));
  equal(details.open,true,'legacy IME keyCode 229 is not consumed by layout popup');
  equal(document.activeElement,slider,'legacy IME keeps input focus');
  await React.act(async()=>slider.dispatchEvent(new window.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true})));
  equal(details.open,true,'slider navigation does not dismiss layout popup');
  details.open=false;summary.focus();const previous=escaped.length;
  await React.act(async()=>summary.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true})));
  equal(escaped.length,previous+1,'closed popup allows the next Escape to reach article shortcut');
 } finally {
  document.removeEventListener('keydown',observeEscape);
  await React.act(async()=>keyboardRoot.unmount());
 }
 const css=await readFile(sourceWeb+'src/components/Ai/ReviewWorkflows.css','utf8');
 assert.match(css,/\.review-reading-bar>button,\.review-reading-controls>summary\{[^}]*box-sizing:border-box[^}]*font:inherit[^}]*line-height:1.4/);
 assert.match(css,/\.review-reading-bar\{[^}]*position:sticky;top:0/);
 checks.push('same summary/button font, line height, border box; sticky in owned scroller (static CSS only)');
 console.log(JSON.stringify({type:'React/jsdom with synthetic geometry; real layout NOT RUN',checks},null,2));
} finally {dom.window.close();await retainTestDirectory(directory)}
