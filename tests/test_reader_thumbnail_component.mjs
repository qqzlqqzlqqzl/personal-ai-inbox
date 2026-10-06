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
const file=new URL('../frontend-review/after/src/components/Article/ReaderThumbnail.jsx',import.meta.url).pathname
const bundled=await build({entryPoints:[file],write:false,bundle:true,platform:'node',format:'cjs',jsx:'automatic',plugins:[{name:'real-react',setup(b){
  b.onResolve({filter:/^react(?:\/.*)?$/},({path})=>({path:webRequire.resolve(path),external:true}))
}}]})
const component=new Module(file);component._compile(bundled.outputFiles[0].text,file)
const ReaderThumbnail=component.exports.default
const signed='/mf/proxy/'+'A'.repeat(43)+'=/aHR0cHM6Ly9leGFtcGxlLm9yZy9jb3Zlci5qcGc='
const original='https://example.org/cover.jpg'

test('thumbnail failure tries original once, clears responsive URLs, then reports real failure',async()=>{
  const root=createRoot(document.querySelector('#root'));let errors=0
  const entry={id:1,coverSource:original,attachments:{images:[{url:signed}]}}
  try {
    await act(async()=>root.render(React.createElement(ReaderThumbnail,{entry,loading:'lazy',onError:()=>errors++})))
    let img=document.querySelector('img')
    assert.equal(img.getAttribute('src'),signed+'?reader_width=480')
    assert.match(img.getAttribute('srcset'),/1600w/)
    await act(async()=>img.dispatchEvent(new dom.window.Event('error')))
    img=document.querySelector('img')
    assert.equal(img.getAttribute('src'),original)
    assert.equal(img.getAttribute('srcset'),null)
    assert.equal(img.getAttribute('sizes'),null)
    assert.equal(errors,0)
    await act(async()=>img.dispatchEvent(new dom.window.Event('error')))
    assert.equal(errors,1);assert.equal(img.getAttribute('src'),original)
  } finally {await act(async()=>root.unmount())}
})

test('changing entry/source starts a fresh thumbnail; unproxied originals report failure directly',async()=>{
  document.querySelector('#root').innerHTML='';const root=createRoot(document.querySelector('#root'));let errors=0
  const render=entry=>root.render(React.createElement(ReaderThumbnail,{key:entry.id+':'+entry.coverSource,entry,onError:()=>errors++}))
  try {
    await act(async()=>render({id:1,coverSource:original,attachments:{images:[{url:signed}]}}))
    await act(async()=>document.querySelector('img').dispatchEvent(new dom.window.Event('error')))
    await act(async()=>render({id:2,coverSource:signed}))
    assert.equal(document.querySelector('img').getAttribute('src'),signed+'?reader_width=480')
    await act(async()=>render({id:3,coverSource:original}))
    await act(async()=>document.querySelector('img').dispatchEvent(new dom.window.Event('error')))
    assert.equal(errors,1);assert.equal(document.querySelector('img').getAttribute('src'),original)
  } finally {await act(async()=>root.unmount())}
})

test('raw Today cover queue and bounded automatic page lifecycle',async()=>{
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
    assert.deepEqual(images.map(image=>image.src),entryList.slice(0,2).map(entry=>entry.coverSource))
    scroll.dispatchEvent(new dom.window.Event('scroll'))
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20))})
    assert.equal(images.length,2)
    // Same actual component, but before Virtua has mounted any visible rows.
    globalThis.__readerFixtureMore=true
    scroll.innerHTML='';scroll.scrollTop=0
    Object.defineProperty(scroll,'scrollHeight',{value:600,configurable:true})
    await act(async()=>{root.render(React.createElement(component.exports.default,{key:'empty-tail',scrollRootRef:{current:scroll},getEntries:()=>{lists++;return Promise.resolve({entries:[]})}}))})
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20))})
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
      await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20))})
      assert.equal(maxActive,1)
      assert.equal(new Set(calls).size,calls.length,'no repeated page cursor')
      return calls
    }
    assert.equal((await pagePhase('ten-pages')).length,10,'exactly ten forward pages without scrolling')
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20))})
    assert.equal(globalThis.__readerFixtureContent.articleListOffset,253,'initial thirteen plus ten pages, not ten total pages')
    assert.equal((await pagePhase('short-list',3)).length,3,'hasNextPage false ends automatic work')
    assert.equal((await pagePhase('failed-page',Infinity,true)).length,1,'failure stops automatic retries')

    let releaseOld,oldCalls=0,newCalls=0
    globalThis.__readerFixtureError=false
    globalThis.__readerFixtureEntries=entryList
    globalThis.__readerFixtureContent={isArticleListReady:true,loadMoreVisible:true,articleListSnapshotRevision:'old-filter',articleListOffset:13,infoFrom:'today',infoId:0}
    const paintRetired=get=>root.render(React.createElement(component.exports.default,{key:'retire',scrollRootRef:{current:scroll},getEntries:get}))
    await act(async()=>paintRetired(()=>{oldCalls++;return new Promise(resolve=>{releaseOld=resolve})}))
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
    await act(async()=>{await new Promise(resolve=>setTimeout(resolve,20))})
    assert.equal(oldCalls,1,'retired filter cannot start more pages')
    assert.equal(newCalls,1,'new filter starts after the existing request retires')
    assert.equal(globalThis.__readerFixtureContent.articleListSnapshotRevision,'new-filter')
  } finally {
    await act(async()=>root.unmount())
    globalThis.Image=oldImage;globalThis.requestAnimationFrame=oldFrame;globalThis.cancelAnimationFrame=oldCancel
    if(oldMore===undefined)delete globalThis.__readerFixtureMore;else globalThis.__readerFixtureMore=oldMore
    for(const key of fixtureGlobals)if(oldGlobals[key]===undefined)delete globalThis[key];else globalThis[key]=oldGlobals[key]
    dom.window.close()
  }
})
