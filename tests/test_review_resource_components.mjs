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
const samples={settings:config,status:{counts:{done:3},coverage:{reader_total:7},kaggle:{enabled:false}},catalog:[{name:'stored source',url:'https://example.test/feed',category:'test',status:'ok',subscription_supported:true,subscribed:false}],roster:{counts:{total:2},sources:[]}}
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
await test('R01 each resource retains timestamp/value on 401/403/503; local retries preserve dirty draft and send no writes',async()=>{
 const root=setup();await act(async()=>root.render(React.createElement(AiPanel,{onClose:()=>root.render(null)})));assert.equal(fixture.requests.length,4)
 await settle();const first=snapshot();for(const value of Object.values(first))assert.ok(Number(value)>0)
 for(const [name,code,label] of [['settings',403,'设置'],['status',503,'看板'],['catalog',403,'目录'],['roster',401,'名单']]){
  const before=fixture.requests.length;await act(async()=>{button('重新读取'+label).click();button('重新读取'+label).click()})
  assert.equal(fixture.requests.length,before+1,'same-tick repeated click does not queue '+name);assert.equal(button('重新读取'+label).disabled,true)
  assert.match(resource(name).textContent,/正在显示旧快照/);await complete(request(name),{status:code},true)
  assert.equal(resource(name).dataset.state,'error');assert.equal(resource(name).dataset.sampledAt,first[name]);assert.match(resource(name).textContent,/正在显示旧快照/)
  assert.doesNotMatch(document.body.textContent,/设置已与服务器同步/)
 }
 assert.equal(document.querySelector('[aria-label="模型 ID"]').value,'initial-model');await fill(document.querySelector('[aria-label="模型 ID"]'),'local draft')
 await click('重试设置');await complete(request('settings'),{...config,model:'new server model',daily_articles:81});assert.equal(document.querySelector('[aria-label="模型 ID"]').value,'local draft');assert.match(document.body.textContent,/有未保存的修改/)
 await click('来源目录');assert.match(document.body.textContent,/stored source/);assert.match(document.body.textContent,/X 核心名单：2/);await click('资源看板');assert.equal(document.querySelector('.ai-dashboard-grid strong').textContent,'7');assert.equal(fixture.writes.length,0)
 await act(async()=>root.unmount())
})
await test('R01/R03 post-subscription refresh supersedes a held older catalog and keeps its own busy lock',async()=>{
 const root=setup();await act(async()=>root.render(React.createElement(AiPanel,{onClose:()=>root.render(null)})));await settle();await click('来源目录');await click('重新读取目录');const old=request('catalog')
 await click('添加当前可用来源');await click('确认添加来源');await complete(request('categories'),[{id:1,title:'test'}]);await complete(request('feeds'),[]);await complete(request('subscribe'),{id:7})
 const fresh=request('catalog');assert.notEqual(fresh,old);assert.equal(old.options.signal.aborted,true)
 await complete(old,samples.catalog);assert.equal(resource('catalog').dataset.state,'loading');assert.equal(button('重新读取目录').disabled,true)
 await complete(fresh,[{...samples.catalog[0],subscribed:true,feed_id:7}]);assert.equal(resource('catalog').dataset.state,'success');assert.ok(button('已添加').disabled);assert.equal(fixture.writes.length,1);assert.equal(fixture.writes[0].url,'/v1/ai/subscribe')
 await act(async()=>root.unmount())
})
await test('R01 first-load failures terminate loading and can recover independently',async()=>{
 const root=setup();await act(async()=>root.render(React.createElement(AiPanel,{onClose:()=>root.render(null)})))
 for(const [i,r] of fixture.requests.entries())await complete(r,{status:i%2?403:401},true)
 for(const name of Object.keys(samples)){assert.equal(resource(name).dataset.state,'error');assert.equal(resource(name).dataset.sampledAt,'');assert.match(resource(name).textContent,/尚无可显示的成功快照/)}
 assert.doesNotMatch(document.body.textContent,/正在读取服务器配置/);await click('重试看板');await complete(request('status'),samples.status);assert.equal(resource('status').dataset.state,'success');assert.equal(resource('settings').dataset.state,'error');assert.equal(fixture.writes.length,0)
 await act(async()=>root.unmount())
})
await test('R01 close/reopen aborts pending reads; late old success/failure cannot alter newer dialog or release its lock',async()=>{
 const root=setup(),render=()=>React.createElement(AiPanel,{onClose:()=>root.render(null)});await act(async()=>root.render(render()));const old=[...fixture.requests]
 await click('关闭');assert.equal(document.querySelector('.ai-dialog'),null);assert.equal(document.activeElement.id,'opener');assert.ok(old.every(r=>r.options.signal.aborted))
 await act(async()=>root.render(render()));const fresh=fixture.requests.slice(4);await complete(old[0],{...config,model:'stale response'});await complete(old[1],{status:403},true)
 assert.equal(resource('settings').dataset.state,'loading');assert.equal(button('重新读取设置').disabled,true);assert.equal(resource('status').dataset.state,'loading')
 for(const r of fresh)await complete(r,nameOf(r)==='settings'?{...config,model:'new response'}:samples[nameOf(r)])
 const times=snapshot();await complete(old[2],[{...samples.catalog[0],name:'stale source'}]);await complete(old[3],{counts:{total:99},sources:[]})
 assert.deepEqual(snapshot(),times);assert.equal(document.querySelector('[aria-label="模型 ID"]').value,'new response');await click('来源目录');assert.doesNotMatch(document.body.textContent,/stale source|X 核心名单：99/);assert.equal(fixture.writes.length,0)
 await act(async()=>root.unmount())
})
await test('R01 toolbar hidden intervals emit zero polls; visible refresh is bounded and old response cannot win; initial failure retries',async()=>{
 const root=setup();let hidden=false;Object.defineProperty(document,'hidden',{configurable:true,get:()=>hidden})
 const realSet=globalThis.setInterval,realClear=globalThis.clearInterval;let tick;globalThis.setInterval=fn=>(tick=fn,17);globalThis.clearInterval=()=>{}
 try{
  await act(async()=>root.render(React.createElement(AiToolbar)));assert.equal(fixture.requests.length,1);const old=request('status')
  await act(async()=>{hidden=true;document.dispatchEvent(new dom.window.Event('visibilitychange'));tick();tick();tick()});assert.equal(fixture.requests.length,1);assert.equal(old.options.signal.aborted,true)
  await act(async()=>{hidden=false;document.dispatchEvent(new dom.window.Event('visibilitychange'));document.dispatchEvent(new dom.window.Event('visibilitychange'));tick()});assert.equal(fixture.requests.length,2)
  await complete(old,{counts:{done:999},kaggle:{enabled:true}});assert.doesNotMatch(document.body.textContent,/999/)
  await complete(request('status'),{status:503},true);assert.match(document.body.textContent,/尚未取得资源状态.*读取失败/);assert.equal(button('重试资源状态').disabled,false)
  await act(async()=>{button('重试资源状态').click();button('重试资源状态').click()});assert.equal(fixture.requests.length,3);assert.equal(button('重试资源状态').disabled,true)
  await complete(request('status'),samples.status);assert.match(document.body.textContent,/已分析 3/);assert.equal(button('重试资源状态'),undefined);assert.equal(fixture.writes.length,0)
  await act(async()=>{tick()});const closing=request('status');await act(async()=>root.unmount());assert.equal(closing.options.signal.aborted,true);await complete(closing,{counts:{done:777}});assert.equal(document.querySelector('.ai-toolbar'),null)
 }finally{globalThis.setInterval=realSet;globalThis.clearInterval=realClear;delete document.hidden}
})
await test('R05 logout invalidates pending save, queued write and metadata; unauthenticated editor makes zero calls',async()=>{
 const root=setup();fixture.content={activeContent:{id:101,ai:{has_note:false}},entries:[{id:101,ai:{has_note:false}}]};await act(async()=>root.render(React.createElement(ArticleNote,{entry:{id:101}})));await settle()
 const textarea=document.querySelector('textarea');const change=async value=>act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(textarea,value);textarea.dispatchEvent(new dom.window.Event('input',{bubbles:true}))})
 await change('first edit');await click('立即保存笔记');const pending=request('101');await change('queued edit');await click('立即保存笔记');assert.equal(fixture.writes.length,1)
 const metadata=JSON.stringify(fixture.content);await act(async()=>{fixture.revision++;fixture.data.set({currentUser:null});fixture.auth.set({server:'https://reader.example.test/mf',username:''})})
 await complete(pending,{updated_at:'late'});assert.equal(fixture.writes.length,1);assert.equal(JSON.stringify(fixture.content),metadata);assert.match(document.body.textContent,/正在确认笔记账号/);assert.equal(window.sessionStorage.length,0)
 const before=fixture.requests.length;await act(async()=>{window.dispatchEvent(new dom.window.Event('pagehide'));root.unmount()});assert.equal(fixture.requests.length,before)
 const next=createRoot(document.querySelector('#root'));await act(async()=>next.render(React.createElement(ArticleNote,{entry:{id:102}})));assert.equal(fixture.requests.length,before);await act(async()=>next.unmount())
})
await test('R05 pending load after session change cannot publish note or metadata',async()=>{
 const root=setup();await act(async()=>root.render(React.createElement(ArticleNote,{entry:{id:101}})));const old=request('101')
 await act(async()=>{fixture.revision++;fixture.data.set({...fixture.data.get(),currentUser:{id:2}})});const fresh=request('101');assert.notEqual(fresh,old);assert.equal(old.options.signal.aborted,true)
 await complete(fresh,{note:'owner two',updated_at:'new'});await complete(old,{note:'owner one secret',updated_at:'old'});assert.equal(document.querySelector('textarea').value,'owner two');assert.equal(fixture.writes.length,0);await act(async()=>root.unmount())
})
await test('R05 recovery discard and pagehide boundaries; storage refusal and clipboard fallback stay explicit',async()=>{
 const root=setup(),key='reader.note.draft:v1:'+encodeURIComponent(JSON.stringify(['https://reader.example.test/mf','1','101']))
 window.sessionStorage.setItem(key,JSON.stringify({version:1,note:'recovery draft',base:'old server',at:Date.now()}))
 await act(async()=>root.render(React.createElement(ArticleNote,{entry:{id:101}})));await settle();await act(async()=>window.dispatchEvent(new dom.window.Event('pagehide')));assert.equal(fixture.writes.length,0)
 await click('放弃草稿，保留服务器内容');assert.equal(document.querySelector('textarea').value,'server note');assert.equal(window.sessionStorage.getItem(key),null);assert.equal(fixture.writes.length,0)
 const original=dom.window.Storage.prototype.setItem;dom.window.Storage.prototype.setItem=function(){throw Error('storage denied')};Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw Error('clipboard denied')}}})
 try{
  const textarea=document.querySelector('textarea');await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(textarea,'unsynced backup');textarea.dispatchEvent(new dom.window.Event('input',{bubbles:true}))})
  assert.match(document.body.textContent,/浏览器未允许草稿暂存/);await click('复制笔记备份');assert.equal(document.querySelector('[aria-label="笔记备份文本"]').value,'unsynced backup')
  const before=new dom.window.Event('beforeunload',{cancelable:true});await act(async()=>window.dispatchEvent(before));assert.equal(before.defaultPrevented,true)
  await act(async()=>window.dispatchEvent(new dom.window.Event('pagehide')));assert.equal(fixture.writes.length,1);assert.equal(request('101').options.keepalive,true);await complete(request('101'),{updated_at:'saved'});await act(async()=>root.unmount())
  const count=fixture.requests.length,event=new dom.window.Event('beforeunload',{cancelable:true});await act(async()=>{window.dispatchEvent(event);window.dispatchEvent(new dom.window.Event('pagehide'))});assert.equal(event.defaultPrevented,false);assert.equal(fixture.requests.length,count)
 }finally{dom.window.Storage.prototype.setItem=original}
})
await test('R07 image src and article-key changes reset failed attempts, without implicit retry',async()=>{
 const root=setup();const render=(src,key='101')=>React.createElement(RecoverableImage,{key,src,alt:'fixture image'})
 await act(async()=>root.render(render('https://example.test/a.png')))
 const fail=async()=>act(async()=>document.querySelector('img').dispatchEvent(new dom.window.Event('error')))
 await fail();for(let i=0;i<3;i++){await click('重试这张图片');await fail()};assert.equal(button('已达本次重试上限').disabled,true)
 await act(async()=>root.render(render('https://example.test/b.png')));assert.ok(document.querySelector('img'));await fail();assert.match(document.body.textContent,/已手动重试 0 \/ 3/)
 await click('重试这张图片');await fail();await act(async()=>root.render(render('https://example.test/b.png','102')));await fail();assert.match(document.body.textContent,/已手动重试 0 \/ 3/);assert.equal(fixture.requests.length,0);await act(async()=>root.unmount())
})
await test('R07 actual image-overlay src/article changes reset retries and readiness; failure never opens preview',async()=>{
 const root=setup();let opens=0;const render=(src,key='101')=>React.createElement(ImageOverlayButton,{key,node:{attribs:{src,alt:'overlay image'}},index:0,togglePhotoSlider:()=>opens++})
 await act(async()=>root.render(render('https://example.test/one.png')));assert.equal(document.querySelector('.image-overlay-button').disabled,true)
 const fail=async()=>act(async()=>document.querySelector('img').dispatchEvent(new dom.window.Event('error')))
 await fail();assert.equal(document.querySelector('.image-overlay-button'),null);for(let i=0;i<3;i++){await click('重试这张图片');assert.equal(document.querySelector('.image-overlay-button').disabled,true);await fail()};assert.equal(button('已达本次重试上限').disabled,true)
 await act(async()=>root.render(render('https://example.test/two.png')));assert.equal(document.querySelector('.image-overlay-button').disabled,true);await fail();assert.match(document.body.textContent,/已手动重试 0 \/ 3/)
 await click('重试这张图片');await fail();await act(async()=>root.render(render('https://example.test/two.png','102')));await fail();assert.match(document.body.textContent,/已手动重试 0 \/ 3/);assert.equal(opens,0)
 await click('重试这张图片');await act(async()=>{const img=document.querySelector('img');Object.defineProperties(img,{naturalWidth:{value:900},naturalHeight:{value:600}});img.dispatchEvent(new dom.window.Event('load'))});assert.equal(document.querySelector('.image-overlay-button').disabled,false)
 await act(async()=>document.querySelector('.image-overlay-button').click());assert.equal(opens,1);await act(async()=>root.unmount())
})
await test('privacy closed A+B failed PUTs survive navigation but are purged together at session exit',async()=>{
 const root=setup()
 for(const id of [101,102]){
  await act(async()=>root.render(React.createElement(ArticleNote,{entry:{id},key:id})));await complete(request(String(id)),{note:'server '+id})
  const textarea=document.querySelector('textarea');await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(textarea,'failed '+id);textarea.dispatchEvent(new dom.window.Event('input',{bubbles:true}))})
  await click('立即保存笔记');await complete(request(String(id)),{status:503},true);await act(async()=>root.render(null))
  const retry=request(String(id));if(retry)await complete(retry,{status:503},true)
 }
 assert.equal(fixture.noteManager.inspect().count,2)
 const key='reader.note.draft:v1:'+encodeURIComponent(JSON.stringify(['https://reader.example.test/mf','2','101']));window.sessionStorage.setItem(key,'keep')
 await act(async()=>fixture.noteManager.end());assert.equal(window.sessionStorage.length,1);assert.equal(window.sessionStorage.getItem(key),'keep');await act(async()=>root.unmount())
})
await test('privacy unchanged-revision owner replacement aborts PUT and late success cannot alter metadata/new draft',async()=>{
 const root=setup();fixture.content={activeContent:{id:101,ai:{has_note:false}},entries:[{id:101,ai:{has_note:false}}]}
 await act(async()=>root.render(React.createElement(ArticleNote,{entry:{id:101}})));await complete(request('101'),{note:'owner one'})
 const textarea=document.querySelector('textarea');await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(textarea,'owner one secret');textarea.dispatchEvent(new dom.window.Event('input',{bubbles:true}))});await click('立即保存笔记');const old=request('101'),revision=fixture.revision
 await act(async()=>fixture.data.set({...fixture.data.get(),currentUser:{id:2}}));assert.equal(fixture.revision,revision);assert.equal(old.options.signal.aborted,true)
 await complete(request('101'),{note:'owner two'});const fresh=document.querySelector('textarea');await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(fresh,'owner two draft');fresh.dispatchEvent(new dom.window.Event('input',{bubbles:true}))})
 const metadata=JSON.stringify(fixture.content);await complete(old,{updated_at:'late owner one'});assert.equal(document.querySelector('textarea').value,'owner two draft');assert.equal(JSON.stringify(fixture.content),metadata);assert.equal(fixture.noteManager.inspect().notes[0].note,'owner two draft')
 await act(async()=>{fixture.noteManager.end();root.unmount()})
})
await test('privacy old closed pending writer cannot remove a reopened same-account draft with identical text',async()=>{
 const root=setup(),render=key=>React.createElement(ArticleNote,{entry:{id:101},key})
 await act(async()=>root.render(render('old')));await complete(request('101'),{note:'base one'})
 const change=async value=>act(async()=>{const e=document.querySelector('textarea');Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(e,value);e.dispatchEvent(new dom.window.Event('input',{bubbles:true}))})
 await change('same text');await click('立即保存笔记');const old=request('101')
 await act(async()=>root.render(render('fresh')));assert.equal(old.options.signal.aborted,true);await complete(request('101'),{note:'base two'});await click('恢复本地草稿');await change('same text')
 const draft=fixture.noteManager.inspect().notes[0];assert.equal(draft.base,'base two');await complete(old,{updated_at:'stale'});assert.equal(fixture.noteManager.inspect().count,1);assert.equal(fixture.noteManager.inspect().notes[0].base,'base two')
 await act(async()=>{fixture.noteManager.end();root.unmount()})
})


await test('deadline hung PUT releases pending, preserves queued latest draft and enables explicit retry; late old success is ignored',async()=>{
 const root=setup();await act(async()=>root.render(React.createElement(ArticleNote,{entry:{id:101}})));await complete(request('101'),{note:'server'})
 const realSet=globalThis.setTimeout,realClear=globalThis.clearTimeout,timers=new Map();let id=0
 globalThis.setTimeout=(fn,ms)=>{timers.set(++id,{fn,ms});return id};globalThis.clearTimeout=id=>timers.delete(id)
 const change=async value=>act(async()=>{const e=document.querySelector('textarea');Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(e,value);e.dispatchEvent(new dom.window.Event('input',{bubbles:true}))})
 try{
  await change('first hung');await click('立即保存笔记');const old=request('101');assert.equal(fixture.writes.length,1);assert.ok([...timers.values()].some(x=>x.ms===15000))
  await change('latest queued');await click('立即保存笔记');assert.equal(fixture.writes.length,1)
  await act(async()=>{for(const [key,item]of [...timers])if(item.ms===15000){timers.delete(key);item.fn()}})
  assert.equal(old.options.signal.aborted,true);assert.match(document.body.textContent,/保存未完成/);assert.equal(fixture.noteManager.inspect().notes[0].note,'latest queued');assert.equal(fixture.writes.length,1)
  await click('重试保存');const retry=request('101');assert.notEqual(retry,old);assert.equal(retry.body.note,'latest queued');assert.equal(fixture.writes.length,2)
  await complete(old,{updated_at:'late timed-out success'});assert.match(document.body.textContent,/正在保存/);assert.equal(fixture.noteManager.inspect().count,1)
  await complete(retry,{updated_at:'current success'});assert.match(document.body.textContent,/已保存/);assert.equal(fixture.noteManager.inspect().count,0);assert.ok(![...timers.values()].some(x=>x.ms===15000))
 }finally{await act(async()=>{fixture.noteManager.end();root.unmount()});globalThis.setTimeout=realSet;globalThis.clearTimeout=realClear}
})
await test('deadline logout clears hung PUT deadline and queue; no timer or late response can affect relogin',async()=>{
 const root=setup();await act(async()=>root.render(React.createElement(ArticleNote,{entry:{id:101}})));await complete(request('101'),{note:'server'})
 const realSet=globalThis.setTimeout,realClear=globalThis.clearTimeout,timers=new Map();let id=0
 globalThis.setTimeout=(fn,ms)=>{timers.set(++id,{fn,ms});return id};globalThis.clearTimeout=id=>timers.delete(id)
 try{
  const e=document.querySelector('textarea');await act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(e,'hung draft');e.dispatchEvent(new dom.window.Event('input',{bubbles:true}))})
  await click('立即保存笔记');const old=request('101');await click('立即保存笔记')
  await act(async()=>{fixture.noteManager.end();fixture.auth.set({server:'',token:'',username:''});fixture.revision++;fixture.data.set({currentUser:null});root.render(null)})
  assert.equal(old.options.signal.aborted,true);assert.ok(![...timers.values()].some(x=>x.ms===15000||x.ms===700));assert.equal(window.sessionStorage.length,0)
  await act(async()=>{fixture.auth.set(createNoteSessionAuth({server:'https://reader.example.test/mf',token:'test',username:''},{id:1}));fixture.data.set({currentUser:{id:1},identityAuthSessionKey:JSON.stringify(['https://reader.example.test/mf','test','',undefined])});root.render(React.createElement(ArticleNote,{entry:{id:101},key:'relogin'}))})
  await complete(request('101'),{note:'new server'});const metadata=JSON.stringify(fixture.content);await complete(old,{updated_at:'old after logout'})
  assert.equal(document.querySelector('textarea').value,'new server');assert.equal(JSON.stringify(fixture.content),metadata);assert.equal(fixture.writes.length,1);assert.ok(![...timers.values()].some(x=>x.ms===15000||x.ms===700))
 }finally{await act(async()=>{fixture.noteManager.end();root.unmount()});globalThis.setTimeout=realSet;globalThis.clearTimeout=realClear}
})
await test('deadline hung GET terminates loading, allows reload and never accepts late timed-out content',async()=>{
 const root=setup(),realSet=globalThis.setTimeout,realClear=globalThis.clearTimeout,timers=new Map();let id=0
 globalThis.setTimeout=(fn,ms)=>{timers.set(++id,{fn,ms});return id};globalThis.clearTimeout=id=>timers.delete(id)
 try{
  await act(async()=>root.render(React.createElement(ArticleNote,{entry:{id:101}})));const old=request('101')
  await act(async()=>{for(const [key,item]of [...timers])if(item.ms===15000){timers.delete(key);item.fn()}})
  assert.equal(old.options.signal.aborted,true);assert.match(document.body.textContent,/笔记加载失败/);assert.equal(button('重新加载笔记').disabled,false)
  await click('重新加载笔记');const fresh=request('101');await complete(old,{note:'stale hung GET'});await complete(fresh,{note:'fresh GET'})
  assert.equal(document.querySelector('textarea').value,'fresh GET');assert.equal(timers.size,0)
 }finally{await act(async()=>{fixture.noteManager.end();root.unmount()});globalThis.setTimeout=realSet;globalThis.clearTimeout=realClear}
})

await retainTestDirectory(directory);dom.window.close()
