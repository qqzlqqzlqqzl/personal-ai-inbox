import assert from 'node:assert/strict'
import test from 'node:test'
import Module, {createRequire} from 'node:module'
import {join} from 'node:path'
import {fileURLToPath} from 'node:url'

const local=process.env.READER_COMPONENT_TOOLS
const webRequire=createRequire(local?join(local,'package.json'):new URL('../upstream/reactflux/package.json',import.meta.url))
const {build}=local?webRequire('esbuild'):createRequire(webRequire.resolve('vite'))('esbuild')
const {JSDOM}=local?webRequire('jsdom'):createRequire(new URL('../runtime/history-test-tools/package.json',import.meta.url))('jsdom')
const dom=new JSDOM('<body><div id="root"></div></body>',{url:'https://reader.example.test/inbox/'})
Object.assign(globalThis,{window:dom.window,document:dom.window.document,CustomEvent:dom.window.CustomEvent,IS_REACT_ACT_ENVIRONMENT:true})
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
const React=webRequire('react'),{act}=React,{createRoot}=webRequire('react-dom/client')
const file=fileURLToPath(new URL('../frontend-review/after/src/components/Article/ReaderThumbnail.jsx',import.meta.url))
const bundled=await build({entryPoints:[file],write:false,bundle:true,platform:'node',format:'cjs',jsx:'automatic',plugins:[{name:'real-react',setup(b){
  b.onResolve({filter:/^react(?:\/.*)?$/},({path})=>({path:webRequire.resolve(path),external:true}))
}}]})
const component=new Module(file);component._compile(bundled.outputFiles[0].text,file)
const ReaderThumbnail=component.exports.default
const signed='/mf/proxy/'+'A'.repeat(43)+'=/aHR0cHM6Ly9leGFtcGxlLm9yZy9jb3Zlci5qcGc='
const original='https://example.org/cover.jpg'

function trackImageRequests() {
  const requests=[]
  const element=dom.window.Element.prototype,image=dom.window.HTMLImageElement.prototype
  const setAttribute=element.setAttribute
  element.setAttribute=function(name,value){
    if(this instanceof dom.window.HTMLImageElement && ['src','srcset'].includes(name.toLowerCase()) && value) requests.push(String(value))
    return setAttribute.call(this,name,value)
  }
  const descriptors=new Map()
  for(const name of ['src','srcset']){
    const descriptor=Object.getOwnPropertyDescriptor(image,name)
    descriptors.set(name,descriptor)
    Object.defineProperty(image,name,{...descriptor,set(value){
      if(value)requests.push(String(value))
      descriptor.set.call(this,value)
    }})
  }
  return {requests,restore(){
    element.setAttribute=setAttribute
    for(const [name,descriptor] of descriptors)Object.defineProperty(image,name,descriptor)
  }}
}

async function settleUntil(predicate, message, timeout=12000) {
  const deadline=Date.now()+timeout
  while(!predicate() && Date.now()<deadline) {
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,10))})
  }
  assert.ok(predicate(),message)
}

test('real thumbnail proxy failure reports the placeholder path without a raw origin request',async()=>{
  const root=createRoot(document.querySelector('#root'));let errors=0
  const tracked=trackImageRequests()
  const entry={id:1,coverSource:original,attachments:{images:[{url:signed}]}}
  try {
    await act(async()=>root.render(React.createElement(ReaderThumbnail,{entry,src:original,srcSet:original+' 1x',loading:'lazy',onError:()=>errors++})))
    let img=document.querySelector('img')
    assert.equal(img.getAttribute('src'),signed+'?reader_width=480')
    assert.match(img.getAttribute('srcset'),/1600w/)
    await act(async()=>img.dispatchEvent(new dom.window.Event('error')))
    assert.equal(document.querySelector('img'),null)
    assert.equal(errors,1,'the existing card onError can show its placeholder immediately')
    assert.ok(tracked.requests.length)
    assert.ok(tracked.requests.every(value=>value.includes('/mf/proxy/') && !value.includes(original)), 'neither src nor srcset ever requests the external original')
    assert.equal(entry.coverSource,original,'original source data is preserved')
  } finally {await act(async()=>root.unmount());tracked.restore()}
})

test('changing entry/source starts a fresh proxy thumbnail; unproxied covers request nothing',async()=>{
  document.querySelector('#root').innerHTML='';const root=createRoot(document.querySelector('#root'));let errors=0
  const tracked=trackImageRequests()
  const render=entry=>root.render(React.createElement(ReaderThumbnail,{key:entry.id+':'+entry.coverSource,entry,onError:()=>errors++}))
  try {
    await act(async()=>render({id:1,coverSource:original,attachments:{images:[{url:signed}]}}))
    await act(async()=>document.querySelector('img').dispatchEvent(new dom.window.Event('error')))
    await act(async()=>render({id:2,coverSource:signed}))
    assert.equal(document.querySelector('img').getAttribute('src'),signed+'?reader_width=480')
    const before=tracked.requests.length
    await act(async()=>render({id:3,coverSource:original}))
    assert.equal(errors,2)
    assert.equal(document.querySelector('img'),null)
    assert.equal(tracked.requests.length,before,'an unbound original has no image request')
    assert.ok(tracked.requests.every(value=>!value.includes(original)))
  } finally {await act(async()=>root.unmount());tracked.restore()}
})

test('unbound Today covers issue no origin warmups while five-page lifecycle stays bounded',async(t)=>{
  const entryList=Array.from({length:13},(_,id)=>({id,coverSource:`https://example.org/today-${id}.jpg`}))
  const file=fileURLToPath(new URL('../patches/ProgressiveLoadMore.jsx',import.meta.url))
  const fixtures={
    '@arco-design/web-react':'export const Button="button",Spin="span"',
    '@nanostores/react':'export const useStore=store=>store.value',
    '@/hooks/useLoadMore':'export default()=>({loadingMore:false,loadMoreError:globalThis.__readerFixtureError||false,handleLoadMore:async(get,options)=>{try{await get(options)}catch{globalThis.__readerFixtureError=true}}})',
    '@/store/contentState':`export const contentState={get value(){return globalThis.__readerFixtureContent ?? {isArticleListReady:true,loadMoreVisible:globalThis.__readerFixtureMore,articleListSnapshotRevision:1,articleListOffset:13,infoFrom:"today",infoId:0}}};export const filteredEntriesState={get value(){return globalThis.__readerFixtureEntries ?? ${JSON.stringify(entryList)}}}`,
  }
  const bundled=await build({entryPoints:[file],write:false,bundle:true,platform:'node',format:'cjs',jsx:'automatic',plugins:[{name:'today-fixture',setup(b){
    b.onResolve({filter:/^react(?:\/.*)?$/},({path})=>({path:webRequire.resolve(path),external:true}))
    b.onResolve({filter:/^(@arco-design|@nanostores|@\/)/},({path})=>{
      if(path in fixtures)return {path,namespace:'today-fixture'}
      const relative=path==='@/utils/reading-session'?'../patches/reading-session.js':'../frontend-review/after/src/components/Article/reader-image-variants.js'
      return {path:fileURLToPath(new URL(relative,import.meta.url))}
    })
    b.onLoad({filter:/.*/,namespace:'today-fixture'},({path})=>({contents:fixtures[path],loader:'js'}))
  }}]})
  const component=new Module(file);component._compile(bundled.outputFiles[0].text,file)
  const scroll=document.createElement('div');scroll.innerHTML='<div data-entry-id="0"></div><div data-entry-id="1"></div><div data-entry-id="2"><img class="grid-card-cover"></div>'
  scroll.getBoundingClientRect=()=>({top:0,bottom:600})
  Object.defineProperties(scroll,{scrollHeight:{value:1800,configurable:true},clientHeight:{value:600}})
  for(const [i,row] of [...scroll.children].entries())row.getBoundingClientRect=()=>({top:i*200,bottom:(i+1)*200})
  scroll.querySelector('img').getBoundingClientRect=()=>({width:320})
  const oldImage=globalThis.Image,oldFrame=globalThis.requestAnimationFrame,oldCancel=globalThis.cancelAnimationFrame
  const oldMore=globalThis.__readerFixtureMore
  const fixtureGlobals=['__readerFixtureContent','__readerFixtureEntries','__readerFixtureError']
  const oldGlobals=Object.fromEntries(fixtureGlobals.map(key=>[key,globalThis[key]]))
  const images=[];let lists=0
  globalThis.Image=class{constructor(){images.push(this)}}
  globalThis.requestAnimationFrame=callback=>setTimeout(callback,0)
  globalThis.cancelAnimationFrame=clearTimeout
  globalThis.__readerFixtureMore=false
  const root=createRoot(document.querySelector('#root'))
  try {
    await act(async()=>{root.render(React.createElement(component.exports.default,{scrollRootRef:{current:scroll},getEntries:()=>{lists++;return Promise.resolve({entries:[]})}}))})
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20))})
    assert.equal(lists,0)
    assert.equal(images.length,0)
    scroll.dispatchEvent(new dom.window.Event('scroll'))
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20))})
    assert.equal(images.length,0)
    // Same actual component, but before Virtua has mounted any visible rows.
    globalThis.__readerFixtureMore=true
    scroll.innerHTML='';scroll.scrollTop=0
    Object.defineProperty(scroll,'scrollHeight',{value:600,configurable:true})
    await act(async()=>{root.render(React.createElement(component.exports.default,{key:'empty-tail',scrollRootRef:{current:scroll},getEntries:()=>{lists++;return Promise.resolve({entries:[]})}}))})
    await settleUntil(()=>lists===1,'automatic startup prefetch begins after the first paint')
    assert.equal(lists,1,'automatic startup prefetch does not wait for virtual rows or scrolling')
    scroll.scrollTop=60
    Object.defineProperty(scroll,'scrollHeight',{value:660,configurable:true})
    scroll.dispatchEvent(new dom.window.Event('scroll'))
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20))})
    assert.equal(lists,1,'the same cursor cannot be fetched again by the scroll fallback')
    scroll.dispatchEvent(new dom.window.Event('scroll'))
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20))})
    assert.equal(lists,1,'the same batch does not request repeatedly')
    const pagePhase=async(key,stopAfter=Infinity,fail=false)=>{
      const calls=[];let active=0,maxActive=0
      globalThis.__readerFixtureError=false
      globalThis.__readerFixtureEntries=entryList
      globalThis.__readerFixtureContent={isArticleListReady:true,loadMoreVisible:true,articleListSnapshotRevision:key,articleListOffset:13,infoFrom:'today',infoId:0}
      const paint=()=>root.render(React.createElement(component.exports.default,{key,scrollRootRef:{current:scroll},getEntries:get}))
      const get=async options=>{
        const cursor=globalThis.__readerFixtureContent.articleListOffset
        calls.push(cursor);assert.equal(options.prefetch,true)
        active++;maxActive=Math.max(active,maxActive)
        try{
          await Promise.resolve()
          if(fail)throw Error('synthetic failed page')
          const newEntries=Array.from({length:24},(_,i)=>({id:cursor+i,coverSource:'https://example.org/page-'+(cursor+i)+'.jpg'}))
          globalThis.__readerFixtureEntries=[...globalThis.__readerFixtureEntries,...newEntries]
          globalThis.__readerFixtureContent={...globalThis.__readerFixtureContent,articleListOffset:cursor+24,loadMoreVisible:calls.length<stopAfter}
          paint()
        }finally{active--}
      }
      scroll.scrollTop=0
      await act(async()=>paint())
      const expected=fail?1:Math.min(5,stopAfter)
      await settleUntil(()=>calls.length>=expected && active===0,'background page queue finishes its bounded work')
      assert.equal(maxActive,1)
      assert.equal(new Set(calls).size,calls.length,'no repeated page cursor')
      return calls
    }
    assert.equal((await pagePhase('five-pages')).length,5,'exactly five forward pages without scrolling')
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20))})
    assert.equal(globalThis.__readerFixtureContent.articleListOffset,133,'initial thirteen plus five pages, not five total pages')
    assert.equal(images.length,0,'five loaded pages never warm unbound external covers')
    assert.equal((await pagePhase('short-list',3)).length,3,'hasNextPage false ends automatic work')
    assert.equal((await pagePhase('failed-page',Infinity,true)).length,1,'failure stops automatic retries')

    let releaseOld,oldCalls=0,newCalls=0
    globalThis.__readerFixtureError=false
    globalThis.__readerFixtureEntries=entryList
    globalThis.__readerFixtureContent={isArticleListReady:true,loadMoreVisible:true,articleListSnapshotRevision:'old-filter',articleListOffset:13,infoFrom:'today',infoId:0}
    const paintRetired=get=>root.render(React.createElement(component.exports.default,{key:'retire',scrollRootRef:{current:scroll},getEntries:get}))
    await act(async()=>paintRetired(()=>{oldCalls++;return new Promise(resolve=>{releaseOld=resolve})}))
    await settleUntil(()=>oldCalls===1,'old filter request starts after its first paint')
    assert.equal(oldCalls,1)
    globalThis.__readerFixtureContent={...globalThis.__readerFixtureContent,articleListSnapshotRevision:'new-filter',articleListOffset:0}
    globalThis.__readerFixtureEntries=[]
    const next=async options=>{
      assert.equal(options.prefetch,true);newCalls++
      globalThis.__readerFixtureEntries=[{id:999,coverSource:'https://example.org/new-filter.jpg'}]
      globalThis.__readerFixtureContent={...globalThis.__readerFixtureContent,articleListOffset:1,loadMoreVisible:false}
      paintRetired(next)
    }
    await act(async()=>paintRetired(next))
    assert.equal(newCalls,0,'old in-flight page keeps the single network slot')
    await act(async()=>releaseOld())
    await settleUntil(()=>newCalls===1,'new filter starts after the old request retires')
    assert.equal(oldCalls,1,'retired filter cannot start more pages')
    assert.equal(newCalls,1,'new filter starts after the existing request retires')
    assert.equal(globalThis.__readerFixtureContent.articleListSnapshotRevision,'new-filter')

    const NativeObserver=window.MutationObserver,windowSetTimeout=window.setTimeout,windowClearTimeout=window.clearTimeout
    const previousFrame=globalThis.requestAnimationFrame,previousCancel=globalThis.cancelAnimationFrame
    const frames=new Map(),coverTimers=new Map(),observers=[]
    let nextFrame=0,nextTimer=1000000,coverPages=0
    window.MutationObserver=class {
      constructor(callback){this.callback=callback;this.native=new NativeObserver(callback);this.active=false;observers.push(this)}
      observe(...args){this.active=true;this.native.observe(...args)}
      disconnect(){this.active=false;this.native.disconnect()}
    }
    window.setTimeout=(callback,delay,...args)=>{
      if(delay!==2000)return windowSetTimeout.call(window,callback,delay,...args)
      const id=++nextTimer;coverTimers.set(id,callback);return id
    }
    window.clearTimeout=id=>{if(!coverTimers.delete(id))windowClearTimeout.call(window,id)}
    globalThis.requestAnimationFrame=callback=>{const id=++nextFrame;frames.set(id,callback);return id}
    globalThis.cancelAnimationFrame=id=>frames.delete(id)
    const flushFrames=async()=>{
      const pending=[...frames];frames.clear()
      await act(async()=>{for(const[,callback]of pending)callback()})
    }
    const covers=prefix=>Array.from({length:12},(_,id)=>{
      const coverSource=`https://example.org/${prefix}-${id}.jpg`
      return {id,coverSource,ai:{cover_proxy_url:'/mf/proxy/'+'A'.repeat(43)+'=/'+btoa(coverSource)}}
    })
    const paintCovers=(snapshot,{key=snapshot,more=false}={})=>{
      globalThis.__readerFixtureError=false
      globalThis.__readerFixtureEntries=covers(snapshot)
      globalThis.__readerFixtureContent={isArticleListReady:true,loadMoreVisible:more,articleListSnapshotRevision:snapshot,articleListOffset:12,infoFrom:'all',infoId:0}
      root.render(React.createElement(component.exports.default,{key,scrollRootRef:{current:scroll},getEntries:async()=>{coverPages++;return {entries:[]}}}))
    }
    const mountCover=width=>{
      const row=document.createElement('div');row.dataset.entryId='3'
      row.getBoundingClientRect=()=>({top:0,bottom:100})
      const media=document.createElement('div');media.className='grid-card-media'
      media.getBoundingClientRect=()=>({width})
      row.append(media);scroll.append(row);return media
    }
    const activeImages=()=>images.filter(image=>image.onload)
    try {
      await t.test('late grid mount warms covers without scroll or extra pages and keeps six slots',async()=>{
        scroll.innerHTML='';scroll.scrollTop=0
        Object.defineProperty(scroll,'scrollHeight',{value:1800,configurable:true})
        const before=images.length
        await act(async()=>paintCovers('late',{more:true}))
        await flushFrames()
        assert.equal(images.length,before)
        assert.equal(observers.filter(observer=>observer.active).length,1)
        await act(async()=>{mountCover(320);scroll.append(document.createElement('span'))})
        // The normal check would fetch at visible index 3 of 12. This frame must only warm images.
        await flushFrames()
        assert.equal(images.length,before+6)
        assert.equal(coverPages,0)
        assert.equal(observers.filter(observer=>observer.active).length,0)
        assert.equal(coverTimers.size,0)
        await act(async()=>paintCovers('late'))
        await flushFrames()
        while(activeImages().length){
          assert.ok(activeImages().length<=6)
          await act(async()=>activeImages()[0].onload())
          await flushFrames()
        }
        assert.equal(images.length,before+12)
        assert.equal(new Set(images.slice(before).map(image=>image.src)).size,12)
        assert.equal(coverPages,0)
      })

      await t.test('missing and zero-width cards expire their observer while normal scroll still works',async()=>{
        for(const width of [null,0]){
          scroll.innerHTML='';const before=images.length
          await act(async()=>paintCovers('timeout-'+width))
          await flushFrames()
          if(width!==null){await act(async()=>mountCover(width));await flushFrames()}
          assert.equal(images.length,before)
          assert.equal(coverTimers.size,1)
          await act(async()=>{scroll.innerHTML='';mountCover(320)})
          assert.equal(frames.size,1,'a late mount has scheduled but not yet run its image frame')
          await act(async()=>[...coverTimers.values()][0]())
          assert.equal(coverTimers.size,0)
          assert.equal(observers.filter(observer=>observer.active).length,0)
          assert.equal(frames.size,0)
          await act(async()=>{scroll.innerHTML='';mountCover(320)})
          assert.equal(frames.size,0,'expired wait does not restart on another DOM mutation')
          assert.equal(images.length,before)
          scroll.dispatchEvent(new dom.window.Event('scroll'));await flushFrames()
          assert.equal(images.length,before+6,'ordinary scroll still offers the covers after timeout')
        }
        assert.equal(coverPages,0)
      })

      await t.test('snapshot and unmount cancel delayed cover observers timers and queued frames',async()=>{
        scroll.innerHTML='';const before=images.length
        await act(async()=>paintCovers('retired-cover',{key:'cover-retirement'}))
        await flushFrames()
        const oldObserver=observers.at(-1)
        await act(async()=>mountCover(320))
        assert.equal(frames.size,1)
        const retiredFrame=[...frames.values()][0]
        await act(async()=>{scroll.innerHTML='';paintCovers('replacement-cover',{key:'cover-retirement'})})
        assert.equal(oldObserver.active,false)
        await act(async()=>retiredFrame())
        await flushFrames()
        assert.equal(images.length,before,'a retired observer frame cannot enqueue old covers')
        assert.equal(coverTimers.size,1)
        const replacement=observers.at(-1)
        await act(async()=>mountCover(320))
        assert.equal(frames.size,1)
        await act(async()=>root.render(null))
        assert.equal(replacement.active,false)
        assert.equal(coverTimers.size,0)
        assert.equal(frames.size,0)
        await act(async()=>{replacement.callback([]);scroll.append(document.createElement('span'))})
        assert.equal(frames.size,0)
        assert.equal(images.length,before)
        assert.equal(coverPages,0)
      })

      await t.test('snapshot retirement keeps active image slots but never restarts old queued covers',async()=>{
        scroll.innerHTML='';const before=images.length
        await act(async()=>paintCovers('old-queue',{key:'queue-retirement'}));await flushFrames()
        await act(async()=>mountCover(320));await flushFrames()
        const oldActive=activeImages();assert.equal(oldActive.length,6)
        await act(async()=>{scroll.innerHTML='';paintCovers('new-queue',{key:'queue-retirement'})});await flushFrames()
        await act(async()=>mountCover(320));await flushFrames()
        assert.equal(images.length,before+6,'old downloads retain all six slots')
        await act(async()=>oldActive[0].onload());await flushFrames()
        assert.equal(images.length,before+7)
        assert.ok(images.at(-1).src.includes(btoa('https://example.org/new-queue-0.jpg')))
        assert.equal(activeImages().length,6)
        const lateCompletion=activeImages()[0].onload
        await act(async()=>root.render(null))
        await act(async()=>lateCompletion())
        assert.equal(images.length,before+7,'unmount cannot drain either retired queue')
        assert.equal(activeImages().length,0)
        assert.equal(coverTimers.size,0)
        assert.equal(frames.size,0)
        assert.equal(coverPages,0)
      })
    } finally {
      await act(async()=>root.render(null))
      window.MutationObserver=NativeObserver
      window.setTimeout=windowSetTimeout;window.clearTimeout=windowClearTimeout
      globalThis.requestAnimationFrame=previousFrame;globalThis.cancelAnimationFrame=previousCancel
    }
  } finally {
    await act(async()=>root.unmount())
    globalThis.Image=oldImage;globalThis.requestAnimationFrame=oldFrame;globalThis.cancelAnimationFrame=oldCancel
    if(oldMore===undefined)delete globalThis.__readerFixtureMore;else globalThis.__readerFixtureMore=oldMore
    for(const key of fixtureGlobals)if(oldGlobals[key]===undefined)delete globalThis[key];else globalThis[key]=oldGlobals[key]
    dom.window.close()
  }
})
