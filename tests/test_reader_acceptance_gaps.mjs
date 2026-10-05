import { retainTestDirectory } from './retain_test_directory.mjs'
// Real owned components with synthetic deferred APIs and clock/visibility events.
// Unlike a network abort mock, these promises can complete after abort to test isolation.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {mkdtemp} from 'node:fs/promises'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import test from 'node:test'
import {createNoteSessionManager,createNoteSessionAuth} from '../frontend-review/after/src/components/Ai/note-session-core.js'
const local=process.env.READER_COMPONENT_TOOLS
const webRequire=createRequire(local?join(local,'package.json'):new URL('../upstream/reactflux/package.json',import.meta.url))
const {build}=local?webRequire('esbuild'):createRequire(webRequire.resolve('vite'))('esbuild')
const {JSDOM}=local?webRequire('jsdom'):createRequire(new URL('../runtime/history-test-tools/package.json',import.meta.url))('jsdom')
const dom=new JSDOM('<!doctype html><body></body>',{url:'https://reader.example.test/inbox/'})
Object.assign(globalThis,{window:dom.window,document:dom.window.document,HTMLElement:dom.window.HTMLElement,IS_REACT_ACT_ENVIRONMENT:true})
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
dom.window.HTMLDialogElement.prototype.showModal=function(){this.open=true}
const React=webRequire('react'),{act}=React,{createRoot}=webRequire('react-dom/client')
const directory=await mkdtemp(join(tmpdir(),'reader-resource-components-')),output=join(directory,'components.cjs')
const base=new URL('../frontend-review/after/src/components/',import.meta.url).pathname
await build({stdin:{contents:`export {default as AiPanel} from '${base}Ai/AiPanel.jsx';export {default as AiToolbar} from '${base}Ai/AiToolbar.jsx';export {default as ArticleNote} from '${base}Ai/ArticleNote.jsx';export {default as RecoverableImage} from '${base}Article/ImageRecovery.jsx';export {default as ImageOverlayButton} from '${base}Article/ImageOverlayButton.jsx'`,resolveDir:base,loader:'jsx'},outfile:output,bundle:true,platform:'node',format:'cjs',jsx:'automatic',plugins:[{name:'synthetic-boundaries',setup(b){
 b.onResolve({filter:/^react(?:\/.*)?$/},({path})=>({path:webRequire.resolve(path),external:true}))
 b.onResolve({filter:/\.css$/},()=>({path:'css',namespace:'fixture'}))
 b.onResolve({filter:/SourceHistory$|NavigationPalette$|ImageLinkTag$/},()=>({path:'unused',namespace:'fixture'}))
 b.onResolve({filter:/^(classnames|html-react-parser)$/},({path})=>({path,namespace:'fixture'}))
 b.onResolve({filter:/^@/},({path})=>({path,namespace:'fixture'}))
 b.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'js',contents:path==='css'?'':path==='unused'?'export default ()=>null':path==='classnames'?`export default (...args)=>args.filter(x=>typeof x==='string').join(' ')`:path==='html-react-parser'?`export const attributesToProps=attrs=>attrs`:path==='@arco-design/web-react'?`export const Tooltip=({children})=>children`:path==='@/hooks/useLanguage'?`const value={polyglot:{t:key=>key}};export const polyglotState={get:()=>value,subscribe:()=>()=>{}}`:path==='@/hooks/usePhotoSlider'?`export default ()=>({isPhotoSliderVisible:false,photoSliderSessionId:1})`:path==='@/store/settingsState'?`export const articleFontSizeState={get:()=>1,subscribe:()=>()=>{}}`:path==='@/utils/constants'?`export const MIN_THUMBNAIL_SIZE=32`:path==='@/utils/note-session'?`export const noteSession=new Proxy({},{get:(_,key)=>{const value=fixture.noteManager[key];return typeof value==='function'?value.bind(fixture.noteManager):value}})`:path==='@/apis/ofetch'?`export default Object.fromEntries(['get','put','post'].map(method=>[method,(...args)=>globalThis.fixture.api(method,...args)]))`:path==='@/hooks/useAppData'?'export default ()=>({refreshFeedData:async()=>{}})':path==='@nanostores/react'?`import {useSyncExternalStore} from 'react';export const useStore=store=>useSyncExternalStore(store.subscribe,store.get,store.get)`:path==='@/store/aiState'?`export const aiState={get:()=>fixture.ai.get(),set:v=>fixture.ai.set(v),setKey:(k,v)=>fixture.ai.setKey(k,v),subscribe:fn=>fixture.ai.subscribe(fn)}`:path==='@/store/authState'?`export const authState={get:()=>fixture.auth.get(),subscribe:fn=>fixture.auth.subscribe(fn)}`:path==='@/store/dataState'?`export const dataState={get:()=>fixture.data.get(),subscribe:fn=>fixture.data.subscribe(fn)};export const getDataSessionRevision=()=>fixture.revision`:path==='@/store/contentState'?`export const invalidateArticleList=()=>fixture.invalidations++;export const contentState={get:()=>fixture.content};export const setActiveContent=v=>{fixture.content.activeContent=v};export const setEntries=v=>{fixture.content.entries=v};export const activeContentState={get:()=>fixture.active,subscribe:()=>()=>{}}`:(()=>{throw Error('Unexpected dependency '+path)})()}))
}}]})
const {AiPanel,AiToolbar,ArticleNote,RecoverableImage,ImageOverlayButton}=createRequire(import.meta.url)(output)
const config={enabled:false,translation_enabled:false,base_url:'https://example.test/v1',model:'initial-model',prompt:'initial prompt',minimum_score:6,daily_articles:80,daily_tokens:500000,max_chars:40000,json_mode:true}
const samples={settings:config,status:{counts:{done:3},coverage:{reader_total:7},kaggle:{enabled:false}},catalog:[{name:'stored source',url:'https://example.test/feed',category:'test',status:'ok',subscribed:false,subscription_supported:true}],roster:{counts:{total:2},sources:[]}}
function store(value){const listeners=new Set();return{get:()=>value,set(next){value=next;listeners.forEach(fn=>fn())},setKey(k,v){this.set({...value,[k]:v})},subscribe(fn){listeners.add(fn);return()=>listeners.delete(fn)}}}
function setup(){
 globalThis.fixture?.noteManager?.dispose()
 document.body.innerHTML='<button id="opener">Open</button><div id="root"></div>';document.querySelector('#opener').focus();window.sessionStorage.clear()
 globalThis.fixture={requests:[],writes:[],ai:store({mode:'all',auxiliary:'none',sort:'time',minimum:6,hydrated:true}),auth:store({server:'https://reader.example.test/mf',username:'',token:'test'}),data:store({currentUser:{id:1}}),content:{entries:[]},active:{url:'https://example.test/article'},revision:1,invalidations:0,api(method,url,body,options){if(method==='get'){options=body;body=null}else this.writes.push({method,url,body});return new Promise((resolve,reject)=>this.requests.push({method,url,body,options,resolve,reject,done:false}))}}
 fixture.data.set({...fixture.data.get(),identityAuthSessionKey:JSON.stringify([fixture.auth.get().server,fixture.auth.get().token,fixture.auth.get().username,fixture.auth.get().password])})
 fixture.noteManager=createNoteSessionManager({auth:fixture.auth,data:fixture.data,authKey:a=>JSON.stringify([a.server,a.token,a.username,a.password]),validAuth:a=>!!(a.server&&a.token),getRevision:()=>fixture.revision,getStorage:()=>window.sessionStorage})
 const root=createRoot(document.querySelector('#root'));return root
}
const nameOf=r=>r.url.endsWith('/roster')?'roster':r.url.split('/').at(-1)
const button=name=>[...document.querySelectorAll('button')].find(b=>b.textContent===name||b.getAttribute('aria-label')===name)
const resource=name=>document.querySelector(`[data-resource="${name}"]`)
const request=name=>fixture.requests.findLast(r=>!r.done&&nameOf(r)===name)
async function complete(r,value,error=false){assert.ok(r,'expected pending request');r.done=true;await act(async()=>error?r.reject(value):r.resolve(value))}
async function fill(element,value){await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype,'value').set.call(element,value);element.dispatchEvent(new dom.window.Event('input',{bubbles:true}))})}
async function click(name){await act(async()=>button(name).click())}
async function settle(){for(const r of fixture.requests.filter(r=>!r.done))await complete(r,samples[nameOf(r)]||{note:'server note',updated_at:'fixture'})}
const snapshot=()=>Object.fromEntries(Object.keys(samples).map(name=>[name,resource(name).dataset.sampledAt]))

await test('preflight 503 then Cancel retires all pending progress',async()=>{
 const root=setup();await act(async()=>root.render(React.createElement(AiPanel,{onClose:()=>root.render(null)})));await settle();await click('来源目录');await click('添加当前可用来源');await click('确认添加来源');await complete(request('categories'),{status:503},true)
 const before={text:document.body.textContent,writes:fixture.writes.length};assert.equal(fixture.writes.length,0);await click('取消添加');const after={text:document.body.textContent,confirmation:!!document.querySelector('[aria-label="订阅确认"]'),writes:fixture.writes.length};console.log(JSON.stringify({case:'preflight503_Cancel',before,after}));await act(async()=>root.unmount());assert.equal(after.confirmation,false);assert.doesNotMatch(after.text,/正在添加|停止只影响尚未发送/)
})
await test('counts preserve authoritative zero rather than older fallback statistics',async()=>{
 const root=setup();await act(async()=>root.render(React.createElement(AiPanel,{onClose:()=>root.render(null)})));for(const r of fixture.requests.filter(r=>!r.done))await complete(r,nameOf(r)==='status'?{counts:{done:3},coverage:{reader_total:0,total_articles:7,ai_done:0},kaggle:{enabled:false}}:samples[nameOf(r)]);await click('资源看板');const cards=[...document.querySelectorAll('.ai-dashboard-grid>div')].map(x=>({name:x.querySelector('span').textContent,value:x.querySelector('strong').textContent}));console.log(JSON.stringify({case:'counts_explicit_zero',cards}));await act(async()=>root.unmount());assert.equal(cards[0].value,'0');assert.equal(cards[2].value,'0')
})
fixture.noteManager.dispose();await retainTestDirectory(directory);dom.window.close()
