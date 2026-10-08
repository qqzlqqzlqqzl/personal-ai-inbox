// Actual prepared Content and ContentContext, with real React/nanostores.
// The fixed 879/9a counterpart is the preserved negative control.
// The router commit seam is controlled; leaf visuals and unrelated APIs are stubbed.
// This establishes component causality, not the timing of a recorded browser paint.
import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, writeFile, mkdtemp } from 'node:fs/promises'
import { existsSync } from 'node:fs'
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
 '@/apis': `export const getEntry=(id,options)=>F.getEntry(id,options)`,
 '@/apis/ofetch': `export default {post:(...args)=>F.verifyBody(...args)}`,
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
await build({stdin:{contents:`export {default as Content} from '${webRoot}/src/components/Content/Content.jsx';export {ContextProvider,ContentContext} from '${webRoot}/src/components/Content/ContentContext.jsx';${existsSync(join(webRoot,'src/utils/reader-entry-detail.js'))?`export {default as useReaderEntryDetail} from '${webRoot}/src/utils/reader-entry-detail.js'`:''}`,loader:'jsx',resolveDir:webRoot},bundle:true,platform:'node',format:'cjs',jsx:'automatic',outfile:out,
 plugins:[{name:'controlled-boundaries',setup(b){
  b.onResolve({filter:/^(react(?:\/.*)?|nanostores|@nanostores\/react|validator\/lib\/isURL)$/},({path})=>({path:web.resolve(path),external:true}))
  // Only the public import completion is controlled; actual loader caching,
  // hook, atomic store publication, React.lazy and Suspense still execute.
  b.onLoad({filter:/[/\\]Content\.jsx$/},async({path})=>{
   const contents=await readFile(path,'utf8'),expression='import("@/components/Article/ArticleDetail")'
   assert.equal(contents.split(expression).length,2)
   const fallback='fallback={<div aria-busy="true" style={{ flex: 1 }} />}'
   assert.equal(contents.split(fallback).length,2)
   return {contents:contents.replace(expression,'F.loadArticleDetail()').replace(fallback,'fallback={<F.PendingDetail />}'),loader:'jsx'}
  })
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
async function setup(name, {initialPath='/inbox/all', cold=true, strict=false, holdModule=false}={}) {
 document.body.innerHTML='<div id="root"></div>'
 globalThis.F={frames:[],route:atom({pathname:initialPath}),info:map({from:'all',id:null}),content:map({activeContent:null,isArticleLoading:false,entries:[dtos(1),dtos(2)],infoFrom:'all',infoId:null}),data:map({sessionRevision:1}),auth:map({server:'https://synthetic.test/mf',token:'synthetic-one'}),language:atom({polyglot:{t:x=>x}}),settings:map({markReadBy:'manual'}),layout:atom('magazine'),gestures:atom({contentBrowsingDirection:'left-to-right',enableSwipeGesture:false,swipeSensitivity:1}),pending:[],requests:[],transitions:[],nop:async()=>{}}
 F.moduleLoads=0;F.module=holdModule?Promise.withResolvers():null;F.boundaryErrors=[];F.suspenseFallbacks=0
 F.PendingDetail=()=>{F.suspenseFallbacks++;return React.createElement('div',{'aria-busy':'true',style:{flex:1}})}
 function Detail(){const {activeContent:e}=useStore(F.content);return React.createElement('article',null,React.createElement('h1',null,e?.title),React.createElement('div',{'data-original':true,dangerouslySetInnerHTML:{__html:e?.content||''}}),React.createElement('section',{'data-note':true},'我的笔记'))}
 F.detailModule={default:Detail};F.loadArticleDetail=()=>{F.moduleLoads++;return F.module?.promise??Promise.resolve(F.detailModule)}
 class Boundary extends React.Component{state={error:null};static getDerivedStateFromError(error){return {error}};componentDidCatch(error){F.boundaryErrors.push(error)};render(){return this.state.error?React.createElement('p',{'data-module-error':true},'module failed'):this.props.children}}
 F.navigate=path=>{F.pending.push(path);F.transitions.push({kind:'navigate-request',path})}
 F.getEntry=(id,options={})=>new Promise((resolve,reject)=>F.requests.push({id:Number(id),signal:options.signal,resolve,reject,done:false}))
 const require=createRequire(import.meta.url);delete require.cache[out]
 const {Content,ContextProvider,ContentContext}=require(out)
 function Controls(){F.actions=React.useContext(ContentContext);const info=useStore(F.info);return React.createElement(Content,{info,getEntries:F.nop,markAllAsRead:F.nop})}
 const root=createRoot(document.querySelector('#root'));const child=React.createElement(Boundary,null,React.createElement(ContextProvider,null,React.createElement(Controls)));await act(async()=>root.render(strict?React.createElement(React.StrictMode,null,child):child))
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
 const before=F.content.get(),observed=[]
 const stop=F.content.listen(state=>observed.push(state))
 try{await t.complete(0,1)}finally{stop()}
 assert.equal(observed.length,1,'detail completion publishes one observable snapshot')
 assert.equal(observed[0].activeContent.id,1);assert.equal(observed[0].activeContent.content_deferred,false)
 assert.ok(observed[0].activeContent.content.includes('Original paragraph 1'))
 assert.equal(observed[0].isArticleLoading,false);assert.equal(observed[0].entries,before.entries)
 assert.equal(observed[0].infoFrom,before.infoFrom);assert.equal(observed[0].infoId,before.infoId)
 assert.equal(F.requests[0].signal.aborted,false,'completed owner is detached before notifying observers')
 original(1);await finish(t)
}
{
 const t=await setup('close-pending-before-base-commit-rejects-late-success')
 await close();await open(dtos(1));assert.equal(F.requests.length,2)
 await close();assert.equal(F.requests.length,2);assert.equal(F.content.get().isArticleLoading,false)
 assert.equal(F.requests[1].signal.aborted,true,'close cancels physical request signal before route commit')
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
 assert.equal(F.requests[1].signal.aborted,true,'session reset cancels transport')
 await t.complete(1,1);assert.equal(F.content.get().activeContent,null);assert.equal(F.requests.length,2);await finish(t)
}
{
 const t=await setup('source-change-owns-new-request-and-rejects-old')
 await close();await open(dtos(1));assert.equal(F.requests.length,2)
 await act(async()=>F.info.set({from:'feed',id:'9'}));assert.equal(F.requests.length,3)
 assert.equal(F.requests[1].signal.aborted,true,'source replacement cancels transport');assert.equal(F.requests[2].signal.aborted,false)
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
 F.auth.setKey('token','synthetic-two');assert.equal(F.requests[1].signal.aborted,true,'auth replacement cancels immediately');await t.complete(1,1)
 assert.equal(F.content.get().activeContent.content,'');assert.equal(F.requests.length,2);await finish(t)
}
{
 const t=await setup('strict-mode-deep-link-gets-a-current-owner', {initialPath:'/inbox/all/entry/1',cold:false,strict:true})
 assert.equal(F.requests.length,2);assert.equal(F.requests[0].signal.aborted,true,'strict-mode cleanup cancels transport');await t.complete(0,1);assert.equal(F.content.get().activeContent,null)
 await t.complete(1,1);original(1);assert.equal(F.content.get().isArticleLoading,false);await finish(t)
}
{
 const t=await setup('batched-close-reopen-without-intermediate-react-render')
 await act(async()=>{F.actions.closeActiveContent();F.actions.handleEntryClick(dtos(1))})
 assert.equal(F.requests.length,2);await t.complete(1,1);original(1);await finish(t)
}
{
 const t=await setup('explicit-close-of-null-active-deep-link-cancels-late-response', {initialPath:'/inbox/all/entry/1',cold:false})
 assert.equal(F.requests.length,1);assert.equal(F.content.get().activeContent,null)
 await close();assert.equal(F.requests.length,1);assert.equal(F.content.get().isArticleLoading,false)
 assert.equal(F.requests[0].signal.aborted,true,'null-active deep link close cancels immediately')
 await t.complete(0,1);assert.equal(F.content.get().activeContent,null);assert.equal(F.requests.length,1)
 await finish(t)
}
{
 const t=await setup('abandoned-concurrent-route-render-preserves-committed-owner',{cold:false});await act(async()=>t.root.unmount())
 const {useReaderEntryDetail}=createRequire(import.meta.url)(out)
 const never=new Promise(()=>{}),seen=[],commits=[];const restore=()=>{}
 function Host(){
  const [route,setRoute]=React.useState({id:'1',block:false});F.setRoute=setRoute
  const requestRef=React.useRef(0),state=useStore(F.content)
  useReaderEntryDetail({entryId:route.id,source:'all',sourceId:null,activeContent:state.activeContent,entryRequestIdRef:requestRef,restoreEntryListFocus:restore})
  React.useLayoutEffect(()=>{commits.push(route.id)},[route.id])
  if(route.block){seen.push(route.id);throw never}
  return React.createElement('div',{'data-current-route':route.id},'committed '+route.id)
 }
 const root=createRoot(document.querySelector('#root'))
 await act(async()=>root.render(React.createElement(React.Suspense,{fallback:React.createElement('p',null,'pending')},React.createElement(Host))))
 assert.equal(F.requests.length,1);assert.deepEqual(commits,['1'])
 await act(async()=>React.startTransition(()=>F.setRoute({id:'2',block:true})))
 assert.ok(seen.includes('2'));assert.deepEqual(commits,['1']);assert.equal(document.querySelector('[data-current-route]').dataset.currentRoute,'1')
 await act(async()=>F.requests[0].resolve(full(1)))
 await act(async()=>F.setRoute({id:'1',block:false}))
 original(1);assert.equal(F.content.get().isArticleLoading,false);assert.equal(F.requests.length,1)
 reports.push({name:t.name,speculative_renders:seen,commits,observations:[t.snapshot('final')]});console.log('PASS',t.name)
 await act(async()=>root.unmount())
}
{
 const t=await setup('ssr-hydration-deep-link-loads-only-on-client',{cold:false});await act(async()=>t.root.unmount())
 const {useReaderEntryDetail}=createRequire(import.meta.url)(out),restore=()=>{}
 function Host(){const state=useStore(F.content),ref=React.useRef(0);useReaderEntryDetail({entryId:'1',source:'all',sourceId:null,activeContent:state.activeContent,entryRequestIdRef:ref,restoreEntryListFocus:restore});return React.createElement('div',null,'SSR details')}
 const savedWindow=globalThis.window,savedDocument=globalThis.document
 let html;try{delete globalThis.window;delete globalThis.document;html=web('react-dom/server').renderToString(React.createElement(Host))}finally{globalThis.window=savedWindow;globalThis.document=savedDocument}
 assert.equal(F.requests.length,0);assert.ok(html.includes('SSR details'));document.querySelector('#root').innerHTML=html
 let root;await act(async()=>{root=web('react-dom/client').hydrateRoot(document.querySelector('#root'),React.createElement(Host))})
 assert.equal(F.requests.length,1);await act(async()=>F.requests[0].resolve(full(1)));original(1);assert.equal(F.content.get().isArticleLoading,false)
 reports.push({name:t.name,server_requests:0,client_requests:F.requests.length,observations:[t.snapshot('final')]});console.log('PASS',t.name)
 await act(async()=>root.unmount())
}
// Deterministic module/API ordering controls; no browser timing or p75 claim.
{
 const t=await setup('no-intent-does-not-import-or-fetch-detail',{cold:false})
 assert.equal(F.moduleLoads,0);assert.equal(F.requests.length,0);await finish(t)
}
for(const moduleFirst of [false,true]){
 const t=await setup('parallel-module-api-atomic-readiness-'+(moduleFirst?'module-first':'api-first'),{cold:false,holdModule:true})
 await open(dtos(1));await t.commit(F.pending.at(-1))
 assert.equal(F.moduleLoads,1);assert.equal(F.requests.length,1)
 const observed=[],stop=F.content.listen(value=>observed.push(value))
 try{
  if(moduleFirst)await act(async()=>F.module.resolve(F.detailModule));else await t.complete(0,1)
  assert.equal(F.content.get().isArticleLoading,true);assert.equal(F.content.get().activeContent.content_deferred,true);assert.equal(observed.length,0)
  if(moduleFirst)await t.complete(0,1);else await act(async()=>F.module.resolve(F.detailModule))
  assert.equal(observed.length,1);assert.equal(observed[0].isArticleLoading,false);original(1)
  assert.equal(document.querySelector('[data-original]').textContent,'Original paragraph 1')
 }finally{stop()}
 await close();await t.commit(F.pending.at(-1));await open(dtos(1));await t.commit(F.pending.at(-1));await t.complete(1,1)
 assert.equal(F.moduleLoads,1,'ready module must reuse the same import');original(1);await finish(t)
}
{
 const t=await setup('ready-module-first-lazy-mount-still-may-suspend',{initialPath:'/inbox/all/entry/1',cold:false})
 assert.equal(F.moduleLoads,1);assert.equal(F.suspenseFallbacks,0)
 // The hook has awaited an already-fulfilled import; React.lazy has never
 // rendered yet because this deep link has no active DTO until API completion.
 await t.complete(0,1);original(1)
 assert.ok(F.suspenseFallbacks>0,'a fulfilled import is not a synchronous React.lazy initialization')
 assert.ok(document.querySelector('[data-original]'));assert.equal(F.moduleLoads,1)
 t.record.push({ready_first_mount_fallback_renders:F.suspenseFallbacks,timing_claim:false})
 console.log('OBSERVED ready-first-mount fallback renders',F.suspenseFallbacks)
 await finish(t)
}
for(const cancel of ['close','auth','source']){
 const t=await setup('module-pending-'+cancel+'-cannot-publish-old-detail',{cold:false,holdModule:true})
 await open(dtos(1));await t.commit(F.pending.at(-1));await t.complete(0,1)
 assert.equal(F.content.get().isArticleLoading,true)
 if(cancel==='close')await close()
 else if(cancel==='auth')await act(async()=>F.auth.setKey('token','different-owner'))
 else await act(async()=>F.info.set({from:'feed',id:'9'}))
 assert.equal(F.requests[0].signal.aborted,true)
 const before=F.content.get()
 await act(async()=>F.module.resolve(F.detailModule))
 assert.equal(F.content.get(),before,'old module completion must not change the current owner snapshot')
 if(cancel==='source'){assert.equal(F.requests.length,2);await t.complete(1,1);original(1)}
 await finish(t)
}
{
 const t=await setup('module-rejection-reaches-react-error-boundary',{cold:false,holdModule:true})
 await open(dtos(1));await t.commit(F.pending.at(-1));await t.complete(0,1)
 const error=Error('synthetic import failure'),old=console.error;console.error=()=>{}
 try{await act(async()=>F.module.reject(error))}finally{console.error=old}
 assert.deepEqual(F.boundaryErrors,[error]);assert.ok(document.querySelector('[data-module-error]'))
 assert.equal(F.moduleLoads,1);await finish(t)
}
{
 const t=await setup('api-error-does-not-wait-for-unresolved-module',{cold:false,holdModule:true})
 await open(dtos(1));await t.commit(F.pending.at(-1))
 const old=console.error,errors=[];console.error=(...args)=>errors.push(args)
 try{await act(async()=>F.requests[0].reject(Error('synthetic current API failure')))}finally{console.error=old}
 assert.equal(F.content.get().isArticleLoading,false);assert.equal(errors.length,1)
 await close();await act(async()=>F.module.resolve(F.detailModule));assert.equal(F.content.get().activeContent,null)
 await finish(t)
}

// Real HTTP cancellation through the owned hook -> actual getEntry -> actual
// ofetch client. The endpoint is a bounded loopback stream, never a user server.
{
 const {createServer} = await import('node:http')
 const adapterOut = join(dir,'detail-transport.cjs')
 const seams = {
  '@/routes': `export default {state:{location:{pathname:'/inbox/all',search:'',hash:''}},navigate:async()=>{throw Error('unexpected unauthorized redirect')}}`,
  '@/store/authState': `export const authState=F.auth`,
  '@/store/dataState': `export const getDataSessionRevision=()=>F.data.get().sessionRevision;export const isEntryScopeFullyVisible=()=>false`,
  '@/utils/session': `export const clearSession=()=>{throw Error('unexpected session clear')}`,
  '@/utils/note-session': `export const getNoteRequestStamp=()=> 'synthetic-loopback-session'`,
  '@/store/aiState': `export const getAiQuery=()=>({})`,
  '@/store/contentState': `export const contentState=F.content`,
  '@/store/settingsState': `export const getSettings=()=>null`,
  '@/store/readingCalendarState': `export const requireReadingCalendar=()=>null;export const assertReadingCalendarCurrent=()=>{}`,
  '@/utils/date': `export const getCalendarStartTimestamp=()=>0;export const getDayEndTimestamp=()=>0;export const getStartOfToday=()=>0;export const getTimestamp=()=>0`,
  '@/utils/constants': `export const ENTRY_UPDATE_BATCH_SIZE=1;export const MAX_ENTRIES_PER_PAGE=24;export const MAX_ENTRY_IDS_PER_PAGE=24`,
 }
 await build({stdin:{contents:`export {getEntry} from '${webRoot}/src/apis/entries.js'`,resolveDir:webRoot,loader:'js'},outfile:adapterOut,bundle:true,platform:'node',format:'cjs',plugins:[{name:'transport-boundaries',setup(b){
  b.onResolve({filter:/^(ofetch|validator\/lib\/isURL)$/},({path})=>({path:web.resolve(path),external:true}))
  b.onResolve({filter:/^@\/utils\/auth$/},()=>({path:join(webRoot,'src/utils/auth.js')}))
  b.onResolve({filter:/.*/},({path})=>Object.hasOwn(seams,path)?{path,namespace:'seam'}:null)
  b.onLoad({filter:/.*/,namespace:'seam'},({path})=>({contents:seams[path],loader:'js'}))
 }}]})
 const t=await setup('real-http-close-cancels-body-and-same-entry-reopens',{cold:false})
 const withinDeadline=async(promise,message)=>{let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error(message)),5000)})])}finally{clearTimeout(timer)}}
 const rows=[],closed=Promise.withResolvers(),started=Promise.withResolvers()
 const body=JSON.stringify({...full(1),padding:'x'.repeat(1024*1024)})
 const server=createServer((req,res)=>{
  assert.equal(req.method,'GET');assert.equal(req.url,'/v1/entries/1')
  const row={path:req.url,body_bytes:Buffer.byteLength(body),sent_bytes:0,closed_early:false};rows.push(row)
  res.writeHead(200,{'Content-Type':'application/json','Content-Length':Buffer.byteLength(body),'Cache-Control':'no-store'})
  if(rows.length>1){res.end(body);row.sent_bytes=Buffer.byteLength(body);return}
  res.write(body.slice(0,4096));row.sent_bytes=4096;started.resolve()
  const interval=setInterval(()=>{const part=body.slice(row.sent_bytes,row.sent_bytes+4096);if(!part){clearInterval(interval);res.end();return}res.write(part);row.sent_bytes+=part.length},10)
  res.on('close',()=>{clearInterval(interval);row.closed_early=row.sent_bytes<row.body_bytes;closed.resolve()})
 })
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve))
 const errors=[],originalError=console.error
 let requestSettled
 try{
  console.error=(...args)=>errors.push(args.map(String).join(' '))
  F.auth.setKey('server',`http://127.0.0.1:${server.address().port}`)
  const {getEntry}=createRequire(import.meta.url)(adapterOut)
  F.getEntry=(id,options)=>{const request=getEntry(id,options);requestSettled=request.then(()=>{},()=>{});return request}
  await open(dtos(1));await t.commit(F.pending.at(-1))
  await withinDeadline(started.promise,'loopback request not started')
  await close()
  await withinDeadline(Promise.all([closed.promise,requestSettled]),'actual transport did not cancel')
  assert.equal(rows.length,1,'aborted GET must not retry')
  assert.equal(rows[0].closed_early,true);assert.ok(rows[0].sent_bytes<rows[0].body_bytes)
  assert.equal(F.content.get().activeContent,null);assert.equal(F.content.get().isArticleLoading,false)
  assert.equal(errors.some(e=>e.startsWith('Failed to fetch entry:')),false,'canceled owner must not show detail error')
  await open(dtos(1));await t.commit(F.pending.at(-1));await act(async()=>requestSettled)
  original(1);assert.equal(rows.length,2);assert.equal(rows[1].sent_bytes,rows[1].body_bytes)
  t.record.push({transport:'actual installed ofetch + native Node HTTP; no browser claim',requests:rows,no_abort_retries:true})
  await finish(t)
 }finally{console.error=originalError;server.closeAllConnections();await new Promise(resolve=>server.close(resolve))}
}

const hashes={}
for(const rel of ['src/components/Content/Content.jsx','src/components/Content/ContentContext.jsx','src/utils/reader-entry-detail.js','src/utils/entry-presentation.js','src/utils/url.js','src/apis/entries.js','src/apis/ofetch.js']) hashes[rel]=createHash('sha256').update(await readFile(join(webRoot,rel))).digest('hex')
const result={actual_components:hashes,router:'controlled commit seam; not a real-browser execution',cases:reports}
if(process.env.READER_DETAIL_EVIDENCE)await writeFile(process.env.READER_DETAIL_EVIDENCE,JSON.stringify(result,null,2)+'\n')
console.log(JSON.stringify({passed:reports.length,actual_components:hashes},null,2))
