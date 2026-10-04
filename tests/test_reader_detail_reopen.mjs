// Actual prepared Content and ContentContext, with real React/nanostores.
// The fixed 879/9a counterpart is the preserved negative control.
// The router commit seam is controlled; leaf visuals and unrelated APIs are stubbed.
// This establishes component causality, not the timing of a recorded browser paint.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, writeFile, mkdtemp } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { createHash } from 'node:crypto'
const here = new URL('../', import.meta.url).pathname
const webRoot = process.env.READER_TEST_WEB || join(here, 'upstream/reactflux')
const web = createRequire(join(webRoot, 'package.json'))
const {build} = createRequire(web.resolve('vite'))('esbuild')
const {JSDOM} = createRequire(join(here,'runtime/history-test-tools/package.json'))('jsdom')
const React = web('react'), {act} = React, {createRoot} = web('react-dom/client')
const {map, atom} = web('nanostores')
const {useStore} = web('@nanostores/react')
const dom = new JSDOM('<!doctype html><body><div id="root"></div></body>', {url:'http://127.0.0.1/inbox/all'})
Object.assign(globalThis,{window:dom.window,document:dom.window.document,HTMLElement:dom.window.HTMLElement,DOMParser:dom.window.DOMParser,IS_REACT_ACT_ENVIRONMENT:true})
globalThis.requestAnimationFrame = cb => F.frames.push(cb)
const dir = await mkdtemp(join(tmpdir(),'fixed879-reopen-')), out = join(dir,'component.cjs')
const actual = new Set(['@/components/Content/ContentContext','@/hooks/useContentContext','@/utils/url','@/utils/entry-presentation','@/utils/dom','@/utils/reader-entry-detail','@/utils/auth'])
const fixtures = {
 'react-router': String.raw`import {useSyncExternalStore} from 'react'; const loc=()=>useSyncExternalStore(F.route.subscribe,F.route.get,F.route.get); export const useLocation=loc; export const useParams=()=>({entryId:loc().pathname.match(/\/entry\/(\d+)$/)?.[1]}); export const useNavigate=()=>F.navigate`,
 '@nanostores/react': null,
 '@arco-design/web-react': `export const Message={error:()=>{throw Error('unexpected mark-read error')}}`,
 '@arco-design/web-react/icon': `export const IconLeft=()=>null;export const IconRight=()=>null`,
 'classnames': `export default (...args)=>args.filter(x=>typeof x==='string').join(' ')`,
 'framer-motion': `export const AnimatePresence=({children})=>children`,
 'react-swipeable': `export const useSwipeable=()=>({})`,
 '@/apis': `export const getEntry=(id)=>F.getEntry(id)`,
 '@/hooks/useEntryActions': `export const updateEntriesStatusOptimistically=()=>{throw Error('unexpected write')}`,
 '@/hooks/useLanguage': `export const polyglotState=F.language`,
 '@/store/settingsState': `export const settingsState=F.settings;export const articleListLayoutState=F.layout;export const contentGestureSettingsState=F.gestures`,
 '@/store/authState': `export const authState=F.auth`,
 '@/store/dataState': `export const dataState=F.data;export const getDataSessionRevision=()=>F.data.get().sessionRevision`,
 '@/store/contentState': `export const contentState=F.content;export const setActiveContent=x=>F.content.setKey('activeContent',x);export const setInfoFrom=x=>F.content.setKey('infoFrom',x);export const setInfoId=x=>F.content.setKey('infoId',x);export const setIsArticleLoading=x=>F.content.setKey('isArticleLoading',x)`,
 '@/hooks/useAppData': `export default ()=>({refreshFeedData:F.nop})`,
 '@/hooks/useArticleList': `export default ()=>({fetchArticleList:F.nop})`,
 '@/hooks/useContentHotkeys': `export default ()=>{}`,
 '@/hooks/useDocumentTitle': `export default ()=>{}`,
 '@/hooks/useKeyHandlers': `export default ()=>({navigateToNextArticle:F.nop,navigateToPreviousArticle:F.nop})`,
 '@/hooks/useScreenWidth': `export default ()=>({isBelowMedium:false})`,
 '@/utils/content-browsing-direction': `export const isRightToLeftBrowsing=()=>false`,
 '@/components/Article/ArticleDetail': `import {useStore} from '@nanostores/react';export default function Detail(){const {activeContent:e}=useStore(F.content);return <article><h1>{e?.title}</h1><div className="article-body"><div data-original dangerouslySetInnerHTML={{__html:e?.content||''}}/><section data-note>我的笔记</section></div></article>}`,
}
const leaf = /(?:FooterPanel|AiToolbar|ActionButtons|ArticleList|SearchAndSortBar|FadeTransition)$/
await build({stdin:{contents:`export {default as Content} from '${webRoot}/src/components/Content/Content.jsx';export {ContextProvider,ContentContext} from '${webRoot}/src/components/Content/ContentContext.jsx'`,loader:'jsx',resolveDir:webRoot},bundle:true,platform:'node',format:'cjs',jsx:'automatic',outfile:out,
 plugins:[{name:'controlled-boundaries',setup(b){
  b.onResolve({filter:/^(react(?:\/.*)?|nanostores|@nanostores\/react|validator\/lib\/isURL)$/},({path})=>({path:web.resolve(path),external:true}))
  b.onResolve({filter:/\.css$/},()=>({path:'empty',namespace:'fixture'}))
  b.onResolve({filter:/.*/},({path})=>{
   if(actual.has(path)) return {path:join(webRoot,'src',path.slice(2)+(path==='@/components/Content/ContentContext'?'.jsx':'.js'))}
   if(Object.hasOwn(fixtures,path))return {path,namespace:'fixture'}
   if(leaf.test(path))return {path:'empty',namespace:'fixture'}
  })
  b.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'jsx',contents:path==='empty'?'export default ()=>null':fixtures[path]}))
 }}]})
const dtos = id => ({id,title:`性能样本 ${String(id).padStart(3,'0')}`,content:'',content_deferred:true,status:'read',feed:{id:7,site_url:'https://example.test',icon:{feed_id:7,icon_id:0}}})
const full = id => ({...dtos(id),content_deferred:false,content:`<p id="body-${id}">Original paragraph ${id}</p><img src="/fixture-images/${id}.png">`})
const reports=[]
async function setup(name, {initialPath='/inbox/all', cold=true, strict=false}={}) {
 document.body.innerHTML='<div id="root"></div>'
 globalThis.F={frames:[],route:atom({pathname:initialPath}),info:map({from:'all',id:null}),content:map({activeContent:null,isArticleLoading:false,entries:[dtos(1),dtos(2)],infoFrom:'all',infoId:null}),data:map({sessionRevision:1}),auth:map({server:'https://synthetic.test/mf',token:'synthetic-one'}),language:atom({polyglot:{t:x=>x}}),settings:map({markReadBy:'manual'}),layout:atom('magazine'),gestures:atom({contentBrowsingDirection:'left-to-right',enableSwipeGesture:false,swipeSensitivity:1}),pending:[],requests:[],transitions:[],nop:async()=>{}}
 F.navigate=path=>{F.pending.push(path);F.transitions.push({kind:'navigate-request',path})}
 F.getEntry=id=>new Promise((resolve,reject)=>F.requests.push({id:Number(id),resolve,reject,done:false}))
 const require=createRequire(import.meta.url);delete require.cache[out]
 const {Content,ContextProvider,ContentContext}=require(out)
 function Controls(){F.actions=React.useContext(ContentContext);const info=useStore(F.info);return React.createElement(Content,{info,getEntries:F.nop,markAllAsRead:F.nop})}
 const root=createRoot(document.querySelector('#root'));const child=React.createElement(ContextProvider,null,React.createElement(Controls));await act(async()=>root.render(strict?React.createElement(React.StrictMode,null,child):child))
 const snapshot=label=>{const e=F.content.get().activeContent;return {label,pathname:F.route.get().pathname,active_id:e?.id??null,deferred:e?.content_deferred??null,content_bytes:e?.content?.length??0,loading:F.content.get().isArticleLoading,session:F.data.get().sessionRevision,requests:F.requests.map(x=>x.id),body_text:document.querySelector('[data-original]')?.textContent??null,note_present:!!document.querySelector('[data-note]')}}
 const commit=async path=>act(async()=>{F.route.set({pathname:path});F.transitions.push({kind:'router-commit',path})})
 const complete=async(index,id)=>act(async()=>{const r=F.requests[index];assert.ok(r);r.done=true;r.resolve(full(id))})
 if(cold){await act(async()=>F.actions.handleEntryClick(dtos(1)));await commit(F.pending.at(-1));assert.equal(F.requests.length,1);await complete(0,1);assert.ok(F.content.get().activeContent.content.includes('Original paragraph'))}
 return {name,root,snapshot,commit,complete,record:[snapshot('cold-complete')]}
}
async function finish(t){
 for(let i=0;i<3;i++)await act(async()=>{})
 t.record.push(t.snapshot('final'));reports.push({name:t.name,observations:t.record,transitions:F.transitions})
 await act(async()=>t.root.unmount());console.log('PASS',t.name)
}
const original = id => assert.ok(F.content.get().activeContent?.content.includes('Original paragraph '+id))
const close = async()=>act(async()=>F.actions.closeActiveContent())
const open = async entry=>act(async()=>F.actions.handleEntryClick(entry))
{
 const t=await setup('rapid-same-entry-reopen-without-base-commit')
 await close();t.record.push(t.snapshot('closed-before-route-commit'));assert.equal(F.requests.length,1)
 await open(dtos(1));t.record.push(t.snapshot('reopened-before-route-commit'))
 await t.commit(F.pending.at(-1));assert.equal(F.requests.length,2);await t.complete(1,1);original(1);await finish(t)
}
{
 const t=await setup('base-route-first-then-same-entry-reopen')
 await close();await t.commit(F.pending.at(-1));await open(dtos(1));await t.commit(F.pending.at(-1))
 assert.equal(F.requests.length,2);await t.complete(1,1);original(1);await finish(t)
}
{
 const t=await setup('rapid-different-entry-waits-for-matching-route')
 await close();await open(dtos(2));assert.equal(F.requests.length,1)
 await t.commit(F.pending.at(-1));assert.equal(F.requests.length,2);await t.complete(1,2);original(2);await finish(t)
}
{
 const t=await setup('complete-same-entry-list-dto-does-not-refetch')
 await close();await open(full(1));await t.commit(F.pending.at(-1));assert.equal(F.requests.length,1);original(1);await finish(t)
}
{
 const t=await setup('initial-deep-link-null-active-loads', {initialPath:'/inbox/all/entry/1',cold:false})
 assert.equal(F.content.get().activeContent,null);assert.equal(F.requests.length,1)
 await t.complete(0,1);original(1);assert.equal(F.content.get().isArticleLoading,false);await finish(t)
}
{
 const t=await setup('close-pending-before-base-commit-rejects-late-success')
 await close();await open(dtos(1));assert.equal(F.requests.length,2)
 await close();assert.equal(F.requests.length,2);assert.equal(F.content.get().isArticleLoading,false)
 await t.complete(1,1);assert.equal(F.content.get().activeContent,null);assert.equal(F.requests.length,2);await finish(t)
}
{
 const t=await setup('late-old-error-does-not-clear-new-entry-loading')
 await close();await open(dtos(1));assert.equal(F.requests.length,2)
 await close();await open(dtos(2));await t.commit(F.pending.at(-1));assert.equal(F.requests.length,3)
 await act(async()=>F.requests[1].reject(new Error('synthetic old error')))
 assert.equal(F.content.get().isArticleLoading,true);assert.equal(F.content.get().activeContent.id,2)
 await t.complete(2,2);original(2);await finish(t)
}
{
 const t=await setup('late-old-success-does-not-overwrite-new-entry')
 await close();await open(dtos(1));await close();await open(dtos(2));await t.commit(F.pending.at(-1))
 await t.complete(2,2);await t.complete(1,1);original(2);assert.equal(F.content.get().isArticleLoading,false);await finish(t)
}
{
 const t=await setup('session-reset-cancels-pending-without-request-for-cleared-entry')
 await close();await open(dtos(1));assert.equal(F.requests.length,2)
 await act(async()=>{F.content.setKey('activeContent',null);F.data.setKey('sessionRevision',2)})
 await t.complete(1,1);assert.equal(F.content.get().activeContent,null);assert.equal(F.requests.length,2);await finish(t)
}
{
 const t=await setup('source-change-owns-new-request-and-rejects-old')
 await close();await open(dtos(1));assert.equal(F.requests.length,2)
 await act(async()=>F.info.set({from:'feed',id:'9'}));assert.equal(F.requests.length,3)
 await t.complete(1,1);assert.equal(F.content.get().activeContent,null);assert.equal(F.content.get().isArticleLoading,true)
 await t.complete(2,1);original(1);await finish(t)
}
{
 const t=await setup('complete-entry-metadata-update-does-not-refetch')
 await act(async()=>F.content.setKey('activeContent',{...F.content.get().activeContent,starred:true}))
 assert.equal(F.requests.length,1);original(1);await finish(t)
}
{
 const t=await setup('wrong-detail-id-cannot-replace-current-entry')
 await close();await open(dtos(1));const old=console.error;const errors=[];console.error=(...x)=>errors.push(String(x[0]))
 try{await t.complete(1,2)}finally{console.error=old}
 assert.equal(errors.length,1);assert.equal(F.content.get().activeContent.id,1);assert.equal(F.content.get().activeContent.content,'');assert.equal(F.content.get().isArticleLoading,false);await finish(t)
}
{
 const t=await setup('cancel-old-request-and-reopen-same-id-new-owner')
 await close();await open(dtos(1));await close();await open(dtos(1));assert.equal(F.requests.length,3)
 await t.complete(1,1);assert.equal(F.content.get().isArticleLoading,true);assert.equal(F.content.get().activeContent.content,'')
 await t.complete(2,1);original(1);await finish(t)
}
{
 const t=await setup('auth-token-change-before-data-reset-rejects-old-response')
 await close();await open(dtos(1));assert.equal(F.requests.length,2)
 F.auth.setKey('token','synthetic-two');await t.complete(1,1)
 assert.equal(F.content.get().activeContent.content,'');assert.equal(F.requests.length,2);await finish(t)
}
{
 const t=await setup('strict-mode-deep-link-gets-a-current-owner', {initialPath:'/inbox/all/entry/1',cold:false,strict:true})
 assert.equal(F.requests.length,2);await t.complete(0,1);assert.equal(F.content.get().activeContent,null)
 await t.complete(1,1);original(1);assert.equal(F.content.get().isArticleLoading,false);await finish(t)
}
{
 const t=await setup('batched-close-reopen-without-intermediate-react-render')
 await act(async()=>{F.actions.closeActiveContent();F.actions.handleEntryClick(dtos(1))})
 assert.equal(F.requests.length,2);await t.complete(1,1);original(1);await finish(t)
}
const hashes={}
for(const rel of ['src/components/Content/Content.jsx','src/components/Content/ContentContext.jsx','src/utils/reader-entry-detail.js','src/utils/entry-presentation.js','src/utils/url.js']) hashes[rel]=createHash('sha256').update(await readFile(join(webRoot,rel))).digest('hex')
const result={actual_components:hashes,router:'controlled commit seam; not a real-browser execution',cases:reports}
if(process.env.READER_DETAIL_EVIDENCE)await writeFile(process.env.READER_DETAIL_EVIDENCE,JSON.stringify(result,null,2)+'\n')
console.log(JSON.stringify({passed:reports.length,actual_components:hashes},null,2))
