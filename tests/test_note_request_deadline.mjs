import assert from "node:assert/strict"
import test from "node:test"
import {createRequire} from "node:module"
import {requestWithNoteDeadline, NOTE_REQUEST_TIMEOUT} from "../frontend-review/after/src/components/Ai/note-request.js"
const web=createRequire(new URL("../upstream/reactflux/package.json",import.meta.url))
const {ofetch}=web("ofetch")
assert.equal(web("ofetch/package.json").version,"1.5.1")
const tick=async()=>{for(let i=0;i<8;i++)await Promise.resolve()}
function clock(){const originalSet=globalThis.setTimeout,originalClear=globalThis.clearTimeout,timers=new Map();let id=0
 globalThis.setTimeout=(fn,ms)=>{timers.set(++id,{fn,ms});return id};globalThis.clearTimeout=id=>timers.delete(id)
 return{timers,fire(ms){for(const [id,item] of [...timers])if(item.ms===ms){timers.delete(id);item.fn()}},restore(){globalThis.setTimeout=originalSet;globalThis.clearTimeout=originalClear}}
}
await test("pinned ofetch signal skips its own timeout; explicit combined deadline aborts and settles hung PUT",async()=>{
 const c=clock();let transportSignal
 try{
  const api=ofetch.create({},{fetch:(_,options)=>{transportSignal=options.signal;return new Promise(()=>{})}})
  const controller=new AbortController(),result=requestWithNoteDeadline(controller,signal=>api("/note",{method:"PUT",body:{note:"synthetic draft"},signal,retry:0,timeout:NOTE_REQUEST_TIMEOUT}))
  const rejected=assert.rejects(result,error=>error.name==="TimeoutError")
  await tick();assert.equal(transportSignal,controller.signal);assert.deepEqual([...c.timers.values()].map(t=>t.ms),[15000])
  c.fire(15000);await rejected;assert.equal(transportSignal.aborted,true);assert.equal(c.timers.size,0)
 }finally{c.restore()}
})
await test("session cancellation releases a hung request and clears deadline; late transport completion cannot win",async()=>{
 const c=clock();let finish
 try{
  const controller=new AbortController(),result=requestWithNoteDeadline(controller,()=>new Promise(resolve=>finish=resolve))
  const rejected=assert.rejects(result,error=>error.name==="AbortError");await tick();controller.abort();await rejected
  assert.equal(c.timers.size,0);finish({updated_at:"late"});await tick();assert.equal(controller.signal.aborted,true)
 }finally{c.restore()}
})
await test("success, transport failure and pre-cancelled request leave no deadline or new request",async()=>{
 const c=clock()
 try{
  assert.deepEqual(await requestWithNoteDeadline(new AbortController(),async()=>({note:"saved"})),{note:"saved"});assert.equal(c.timers.size,0)
  await assert.rejects(requestWithNoteDeadline(new AbortController(),async()=>{throw Error("network refused")}),/network refused/);assert.equal(c.timers.size,0)
  const controller=new AbortController();controller.abort();let called=false
  await assert.rejects(requestWithNoteDeadline(controller,()=>{called=true}),error=>error.name==="AbortError");assert.equal(called,false);assert.equal(c.timers.size,0)
 }finally{c.restore()}
})
