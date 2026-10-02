// Actual owned session, API boundary and explicit logout helper; synthetic stores/network only.
import assert from 'node:assert/strict'
import {createRequire} from 'node:module'
import {mkdtemp,rm} from 'node:fs/promises'
import {join} from 'node:path'
import {tmpdir} from 'node:os'
import test from 'node:test'
const web=createRequire(new URL('../upstream/reactflux/package.json',import.meta.url))
const {build}=createRequire(web.resolve('vite'))('esbuild')
const {JSDOM}=createRequire(new URL('../runtime/history-test-tools/package.json',import.meta.url))('jsdom')
const dom=new JSDOM('<body><div id="root"></div>',{url:'https://reader.example.test/inbox/'})
Object.assign(globalThis,{window:dom.window,document:dom.window.document,IS_REACT_ACT_ENVIRONMENT:true})
function store(value){const listeners=new Set();return{get:()=>value,set(next){value=next;for(const fn of [...listeners])fn()},setKey(key,next){this.set({...value,[key]:next})},listen(fn){listeners.add(fn);return()=>listeners.delete(fn)}}}
const blank={server:'',username:'',password:'',token:''}
globalThis.boundary={auth:store(blank),data:store({currentUser:null,identityAuthSessionKey:null,sessionRevision:0}),events:[],warnings:[],navigations:[],hooks:null,modal:null}
const base=new URL('../frontend-review/after/src/',import.meta.url).pathname
const directory=await mkdtemp(join(tmpdir(),'note-session-integration-')),output=join(directory,'integration.cjs')
await build({stdin:{contents:`export * from '${base}utils/session.js';export * from '${base}utils/note-session.js';export * from '${base}components/Ai/LogoutConfirm.jsx';import '${base}apis/ofetch.js'`,resolveDir:base,loader:'jsx'},bundle:true,platform:'node',format:'cjs',jsx:'automatic',outfile:output,plugins:[{name:'boundaries',setup(b){
 b.onResolve({filter:/^react(?:\/.*)?$/},({path})=>({path:web.resolve(path),external:true}))
 b.onResolve({filter:/\.css$/},()=>({path:'css',namespace:'fixture'}))
 b.onResolve({filter:/^@\/components\/Ai\/note-session-core$/},({path})=>({path:base+path.slice(2)+'.js'}))
 b.onResolve({filter:/^@\/utils\/(session|note-session)$/},({path})=>({path:base+path.slice(2)+'.js'}))
 b.onResolve({filter:/^@|^ofetch$/},({path})=>({path,namespace:'fixture'}))
 b.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'js',contents:{
  css:'',
  '@arco-design/web-react':`export const Notification={warning:value=>boundary.warnings.push(value)};export const Modal={confirm:props=>{boundary.modal=props;return{close:()=>{boundary.events.push('modalClose');props.afterClose?.()}}}}`,
  '@/utils/confirm-dialog':'export const confirmDialogProps={};export const destructiveConfirmButtonProps={}',
  '@/store/authState':`export const authState=boundary.auth;export const resetAuth=()=>{boundary.events.push('resetAuth');authState.set({server:'',username:'',password:'',token:''})};export const setAuth=value=>authState.set(value)`,
  '@/store/dataState':`export const dataState=boundary.data;export const getDataSessionRevision=()=>dataState.get().sessionRevision;export const resetData=()=>{boundary.events.push('resetData');dataState.set({currentUser:null,identityAuthSessionKey:null,sessionRevision:getDataSessionRevision()+1})};export const commitIdentityData=(currentUser,key)=>dataState.set({...dataState.get(),currentUser,identityAuthSessionKey:key});export const setVerifiedServer=value=>dataState.set({...dataState.get(),verifiedServer:value})`,
  '@/store/contentState':"export const resetContent=()=>boundary.events.push('resetContent')",
  '@/store/feedIconsState':"export const resetFeedIcons=()=>boundary.events.push('resetIcons')",
  '@/utils/auth':`export const getAuthSessionKey=a=>JSON.stringify([a.server,a.token,a.username,a.password]);export default a=>!!(a.server&&(a.token||(a.username&&a.password)))`,
  '@/routes':`export default {state:{location:{pathname:'/all',search:'?x=1',hash:'#note'}},navigate:async(...args)=>boundary.navigations.push(args)}`,
  ofetch:`export const ofetch={create:hooks=>{boundary.hooks=hooks;return()=>{}}}`,
 }[path]??(()=>{throw Error('Unexpected '+path)})()}))
}}]})
let {startSession,clearSession,resetSessionData,noteSession,confirmDraftLogout,getNoteRequestStamp}=createRequire(import.meta.url)(output)
const React=web('react'),{act}=React,{createRoot}=web('react-dom/client')
const auth={server:'https://reader.example.test/mf',token:'synthetic-token',username:'',password:''}
const key=(owner,id,server=auth.server)=>'reader.note.draft:v1:'+encodeURIComponent(JSON.stringify([server,String(owner),String(id)]))
function begin(){clearSession();window.sessionStorage.clear();boundary.events=[];boundary.warnings=[];boundary.navigations=[];startSession(auth,'2.3.3',{id:1})}
function closedDrafts(){for(const id of [101,102]){const lease=noteSession.acquire(id);noteSession.store(lease,'failed '+id,'server '+id);noteSession.release(lease)}}
await test('owned clearSession retires closed A+B and invalidates live writer synchronously before auth reset',()=>{
 begin();closedDrafts();let atInvalidation;const live=noteSession.acquire(103,()=>{atInvalidation=boundary.auth.get().token;boundary.events.push('invalidate')});noteSession.store(live,'live','base')
 for(const other of [key(2,101),key(1,101,'https://other.example.test/mf'),'other-feature'])window.sessionStorage.setItem(other,'keep')
 boundary.events=[];const result=clearSession();assert.equal(result.retired,true);assert.equal(result.ok,true);assert.equal(atInvalidation,auth.token);assert.ok(boundary.events.indexOf('invalidate')<boundary.events.indexOf('resetAuth'));assert.equal(window.sessionStorage.getItem(key(1,101)),null);assert.equal(window.sessionStorage.getItem(key(1,102)),null);assert.equal(window.sessionStorage.getItem(key(2,101)),'keep');assert.equal(window.sessionStorage.getItem('other-feature'),'keep')
})
await test('owned API fresh 401 purges all closed drafts; old 401 and retry cannot reset relogin or owner replacement without revision change',async()=>{
 begin();closedDrafts();const old={options:{}};boundary.hooks.onRequest(old);const revision=boundary.data.get().sessionRevision
 boundary.data.set({...boundary.data.get(),currentUser:{id:2}});assert.equal(boundary.data.get().sessionRevision,revision);assert.equal(window.sessionStorage.getItem(key(1,101)),null)
 const stamp=getNoteRequestStamp();await assert.rejects(boundary.hooks.onResponseError({...old,response:{status:401,statusText:'old',_data:{}}}));assert.equal(boundary.auth.get().token,auth.token);assert.equal(getNoteRequestStamp(),stamp);assert.equal(boundary.navigations.length,0);assert.throws(()=>boundary.hooks.onRequest(old),/Stale/);assert.throws(()=>boundary.hooks.onResponse(old),/Stale/)
 begin();closedDrafts();const current={options:{}};boundary.hooks.onRequest(current);await assert.rejects(boundary.hooks.onResponseError({...current,response:{status:401,statusText:'current',_data:{}}}));assert.equal(boundary.auth.get().token,'');assert.equal(window.sessionStorage.getItem(key(1,102)),null);assert.equal(boundary.navigations.length,1)
 startSession(auth,'2.3.3',{id:1});const fresh=noteSession.acquire(101);noteSession.store(fresh,'new session','new base');await assert.rejects(boundary.hooks.onResponseError({...current,response:{status:401,statusText:'late',_data:{}}}));assert.equal(noteSession.read(fresh).note,'new session');assert.equal(boundary.auth.get().token,auth.token);assert.equal(boundary.navigations.length,1)
})
await test('explicit logout offers local export; cancel retains closed drafts and newer-session confirmation is rejected',async()=>{
 begin();closedDrafts();let confirmed=0;confirmDraftLogout({title:'退出',description:'退出当前账号',onConfirm:()=>confirmed++})
 const props=boundary.modal;assert.equal(props.title,'退出前处理未同步笔记');assert.equal(props.cancelText,'取消，保留会话')
 const root=createRoot(document.querySelector('#root'));await act(async()=>root.render(props.content));assert.match(document.body.textContent,/2 篇未同步笔记草稿/);const exportButton=[...document.querySelectorAll('button')].find(x=>x.textContent==='导出本地草稿')
 // A browser denying download exposes the exact local backup text instead.
 const original=URL.createObjectURL;URL.createObjectURL=()=>{throw Error('download denied')}
 try{await act(async()=>exportButton.click());const backup=JSON.parse(document.querySelector('textarea').value);assert.deepEqual(backup.notes.map(x=>x.entryId),['101','102']);assert.equal(backup.owner,'1');assert.doesNotMatch(JSON.stringify(backup),/synthetic-token|password/)}finally{URL.createObjectURL=original}
 props.afterClose();assert.equal(boundary.auth.get().token,auth.token);assert.equal(noteSession.inspect().count,2);assert.equal(confirmed,0)
 let lease;await act(async()=>{startSession(auth,'2.3.3',{id:1});lease=noteSession.acquire(101);noteSession.store(lease,'new draft','base');props.onOk()});assert.equal(confirmed,0);assert.equal(noteSession.read(lease).note,'new draft');await act(async()=>root.unmount())
})
await test('explicit confirmed discard purges closed drafts; storage failure reports limitation without resurrecting old generation',()=>{
 begin();closedDrafts();confirmDraftLogout({title:'退出',description:'退出',onConfirm:()=>boundary.events.push('confirmed')});boundary.modal.onOk();assert.equal(boundary.auth.get().token,'');assert.equal(window.sessionStorage.getItem(key(1,102)),null);assert.ok(boundary.events.includes('confirmed'))
 begin();closedDrafts();const original=dom.window.Storage.prototype.removeItem;dom.window.Storage.prototype.removeItem=function(){throw Error('denied')}
 try{const result=clearSession();assert.equal(result.ok,false);assert.equal(boundary.warnings.length,1);startSession(auth,'2.3.3',{id:1});assert.equal(noteSession.read(noteSession.acquire(101)),null)}finally{dom.window.Storage.prototype.removeItem=original}
})

function reloadLegacy(){
 noteSession.dispose();boundary.auth.set({...auth});boundary.data.set({currentUser:null,identityAuthSessionKey:null,sessionRevision:0})
 boundary.events=[];boundary.warnings=[];boundary.navigations=[];window.sessionStorage.clear()
 const moduleRequire=createRequire(import.meta.url);delete moduleRequire.cache[output]
 ;({startSession,clearSession,resetSessionData,noteSession,confirmDraftLogout,getNoteRequestStamp}=moduleRequire(output))
 for(const eid of [101,102])window.sessionStorage.setItem(key(1,eid),JSON.stringify({version:1,note:'legacy closed '+eid,base:'server',at:Date.now()}))
 for(const other of [key(2,101),key(1,101,'https://other.example.test/mf'),'other-feature'])window.sessionStorage.setItem(other,'keep')
}
await test('actual legacy first-refresh cancel permits verified migration; early explicit logout admits incomplete scoped cleanup',async()=>{
 reloadLegacy();const snapshot=noteSession.inspect();assert.equal(snapshot.scopeKnown,false);assert.equal(snapshot.storageOK,false);assert.equal(noteSession.exportText(noteSession.context()),null)
 confirmDraftLogout({title:'退出',description:'退出'});const props=boundary.modal;assert.equal(props.title,'退出前确认草稿清理限制');assert.equal(props.okText,'仍退出账号')
 const root=createRoot(document.querySelector('#root'));await act(async()=>root.render(props.content));assert.match(document.body.textContent,/退出不能保证删除/);assert.equal(document.querySelector('button'),null)
 props.afterClose();assert.equal(boundary.auth.get().token,auth.token);assert.ok(window.sessionStorage.getItem(key(1,101)))
 await act(async()=>boundary.data.set({...boundary.data.get(),currentUser:{id:1},identityAuthSessionKey:JSON.stringify([auth.server,auth.token,auth.username,auth.password])}))
 assert.equal(noteSession.inspect().count,2);assert.equal(noteSession.read(noteSession.acquire(101)).note,'legacy closed 101');await act(async()=>root.unmount())
 reloadLegacy();const result=clearSession();assert.equal(result.ok,false);assert.equal(result.reason,'unverified_owner');assert.equal(result.removed,0);assert.equal(boundary.auth.get().token,'');assert.equal(boundary.warnings.length,1);assert.match(boundary.warnings[0].content,/尚未确认旧会话账号/)
 for(const other of [key(1,101),key(1,102),key(2,101),key(1,101,'https://other.example.test/mf'),'other-feature'])assert.ok(window.sessionStorage.getItem(other))
 startSession(auth,'2.3.3',{id:1});assert.equal(noteSession.read(noteSession.acquire(101)),null)
})
await test('actual pre-identity legacy 401 warns and logs out without deleting unverified owner scopes',async()=>{
 reloadLegacy();const current={options:{}};boundary.hooks.onRequest(current)
 await assert.rejects(boundary.hooks.onResponseError({...current,response:{status:401,statusText:'early legacy 401',_data:{}}}))
 assert.equal(boundary.auth.get().token,'');assert.equal(boundary.warnings.length,1);assert.equal(noteSession.lastCleanup().scopeKnown,false)
 assert.ok(window.sessionStorage.getItem(key(1,101)));assert.equal(window.sessionStorage.getItem(key(2,101)),'keep');assert.equal(boundary.navigations.length,1)
})
await test('actual removeItem denial warns on forced owner replacement, data reset and startSession without blocking leases',()=>{
 begin();closedDrafts();const old=noteSession.acquire(103);noteSession.store(old,'owner one live','server');const revision=boundary.data.get().sessionRevision
 const original=dom.window.Storage.prototype.removeItem;dom.window.Storage.prototype.removeItem=function(){throw Error('remove denied')}
 try{
  boundary.data.set({...boundary.data.get(),currentUser:{id:2}});assert.equal(boundary.data.get().sessionRevision,revision)
  assert.equal(noteSession.lastCleanup().ok,false);assert.equal(boundary.warnings.length,1);assert.match(boundary.warnings[0].content,/无法保证物理删除/)
  assert.ok(window.sessionStorage.getItem(key(1,101)));assert.equal(noteSession.store(old,'stale','server'),false)
  const fresh=noteSession.acquire(201);assert.ok(fresh);noteSession.store(fresh,'owner two draft','server');assert.equal(noteSession.read(fresh).note,'owner two draft')
  resetSessionData();assert.equal(boundary.warnings.length,2);assert.equal(noteSession.isCurrentLease(fresh),false);assert.equal(noteSession.acquire(201),null);assert.ok(boundary.auth.get().token)
  boundary.data.set({...boundary.data.get(),currentUser:{id:2},identityAuthSessionKey:JSON.stringify([auth.server,auth.token,auth.username,auth.password])});assert.equal(noteSession.read(noteSession.acquire(201)),null)
  startSession(auth,'2.3.3',{id:1});assert.equal(boundary.warnings.length,3);assert.equal(noteSession.editorContext().scope.owner,'1');assert.equal(noteSession.read(noteSession.acquire(101)),null)
 }finally{dom.window.Storage.prototype.removeItem=original}
})

noteSession.dispose();await rm(directory,{recursive:true,force:true});dom.window.close()
