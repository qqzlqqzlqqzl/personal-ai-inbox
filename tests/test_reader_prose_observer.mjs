// Real observer in jsdom; synthetic geometry only, no browser paint claim.
import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {createRequire} from 'node:module'
import {fileURLToPath} from 'node:url'
const require=createRequire(new URL('../runtime/history-test-tools/package.json',import.meta.url))
const {JSDOM}=require('jsdom')
const tests=fileURLToPath(new URL('./',import.meta.url))
const {script,snapshot,text}=JSON.parse(execFileSync(process.env.PYTHON||'python',
 ['-B','-c',"import json;from reader_loading_body import BODY_OBSERVER,SNAPSHOT_FUNCTION,PROSE_TEXT;print(json.dumps({'script':BODY_OBSERVER,'snapshot':SNAPSHOT_FUNCTION,'text':PROSE_TEXT}))"],
 {cwd:tests,encoding:'utf8',timeout:10000}))
function setup(content, options={}) {
 const dom=new JSDOM(`<style>*{opacity:1;visibility:visible;overflow:visible}</style><article class="article-content"><div class="scroll-container" data-native-scroll="true" style="overflow-y:auto"><h1 class="article-title">${options.title||'性能样本 001'}</h1><div class="article-body"${options.busy?' aria-busy="true"':''}>${content}</div></div></article>`,{url:'http://127.0.0.1:43210/inbox/all/1',runScripts:'outside-only',pretendToBeVisual:true})
 const w=dom.window
 Object.defineProperty(w.HTMLElement.prototype,'innerText',{get(){return this.textContent}})
 w.HTMLElement.prototype.getBoundingClientRect=function(){
   let rect=this.matches('.scroll-container')?{left:0,top:50,right:500,bottom:650}:this.matches('p')?
     {left:20,top:options.top??100,right:470,bottom:(options.top??100)+100}:{left:0,top:0,right:500,bottom:1000}
   return {...rect,x:rect.left,y:rect.top,width:rect.right-rect.left,height:rect.bottom-rect.top}
 }
 return dom
}
let controls=0
function check(content,options,expected){const dom=setup(content,options);try{const r=dom.window.eval('('+snapshot+')()');assert.equal(r.ready,expected);assert.equal(r.painted,null);controls++;return JSON.parse(JSON.stringify(r))}finally{dom.window.close()}}
const correct=check(`<p>${text}</p>`,{},true)
assert.equal(correct.visibleParagraphs,1)
assert.equal(correct.prose[0].intersection.height,100)
check(`<p>${text}</p>`,{top:700},false) // in viewport, clipped by the real scroll root
check(`<p>${text}</p>`,{top:1000},false)
check(`<p>${text}</p>`,{busy:true},false)
check(`<p>${text}</p>`,{title:'性能样本 002'},false)
check(`<p>${text.slice(1)}</p>`,{},false)
for(const cls of ['article-note','article-source-footer','ai-verdict-detail','ai-summary']) {
 const row=check(`<section class="${cls}"><p>${text}</p></section>`,{},false)
 assert.ok(row.containerTextLength>100);assert.equal(row.matchingParagraphs,0)
}
check(`<div style="display:none"><p>${text}</p></div>`,{},false)
check(`<div style="opacity:0"><p>${text}</p></div>`,{},false)
{
 const dom=setup(`<p>${text}</p>`)
 try{
  dom.window.document.querySelector('.scroll-container').removeAttribute('data-native-scroll')
  assert.equal(dom.window.eval('('+snapshot+')()').ready,false);controls++
 }finally{dom.window.close()}
}
{
 const dom=setup(`<p>${text}</p><button aria-label="关闭文章">close</button>`)
 try{
  const w=dom.window;w.eval(script)
  w.document.querySelector('button').dispatchEvent(new w.MouseEvent('click',{bubbles:true}))
  w.history.pushState({},'', '/inbox/all')
  w.__readerBodyMark('driver-after-body-hidden')
  assert.equal(w.__readerBodyObservation.samples.at(-1).pathname,'/inbox/all')
  assert.ok(w.__readerBodyObservation.samples.some(x=>x.kind==='user-click'&&x.detail.isTrusted===false))
  assert.equal(w.__readerBodyObservation.paint_observed,false)
  for(let i=0;i<520;i++)w.__readerBodyMark('synthetic-budget-control')
  assert.equal(w.__readerBodyObservation.samples.length,512);assert.equal(w.__readerBodyObservation.truncated,true)
  controls++
 }finally{dom.window.__readerBodyStop?.();dom.window.close()}
}
{
 const dom=setup('<p>not the original prose</p>')
 try {
  const w=dom.window;w.eval(script)
  const begun=w.__readerBodyMark('driver-before-open',{entry:1,input:'keyboard'})
  // Synthetic event-boundary control. This is not a trusted browser event claim.
  w.__readerBodyMark('user-keydown',{entryId:'1',key:'Enter',isTrusted:false})
  assert.equal(w.__readerBodyOpenResult(begun.openSequence).activation,null)
  w.__readerBodyMark('user-keydown',{entryId:'1',key:'Enter',isTrusted:true})
  w.document.querySelector('.article-body p').textContent=text
  await new Promise(resolve=>w.setTimeout(resolve,25))
  const opening=w.__readerBodyOpenResult(begun.openSequence)
  assert.ok(opening.first_prose.ready)
  assert.ok(opening.first_prose.at>=opening.activation.at)
  const first=opening.first_prose.at
  await new Promise(resolve=>w.setTimeout(resolve,30))
  const late=w.__readerBodyMark('container-ready-not-prose-proof')
  assert.ok(late.at>first)
  assert.equal(w.__readerBodyOpenResult(begun.openSequence).first_prose.at,first)
  assert.equal(opening.first_prose.painted,null)
  controls++
 } finally {dom.window.__readerBodyStop?.();dom.window.close()}
}
{
 const dom=setup('<p>not the original prose</p>')
 try {
  const w=dom.window;w.eval(script)
  const before=w.__readerBodyMark('driver-before-open',{entry:1,input:'keyboard'})
  assert.equal(before.ready,false)
  w.document.querySelector('.article-body p').textContent=text
  // Reproduce the reviewed counterexample: prose appears before activation.
  const activation=w.__readerBodyMark('user-keydown',{entryId:'1',key:'Enter',isTrusted:true})
  assert.equal(activation.ready,true)
  await new Promise(resolve=>w.setTimeout(resolve,25))
  assert.equal(w.__readerBodyOpenResult(before.openSequence).first_prose,null)
  controls++
 } finally {dom.window.__readerBodyStop?.();dom.window.close()}
}
console.log(JSON.stringify({synthetic_observer_controls:controls,real_jsdom:true,actual_browser:false,paint_claim:false}))
