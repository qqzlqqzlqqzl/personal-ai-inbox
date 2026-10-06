import assert from 'node:assert/strict'
import test from 'node:test'
import Module, {createRequire} from 'node:module'
import {join} from 'node:path'

const local=process.env.READER_COMPONENT_TOOLS
const webRequire=createRequire(local?join(local,'package.json'):new URL('../upstream/reactflux/package.json',import.meta.url))
const {build}=local?webRequire('esbuild'):createRequire(webRequire.resolve('vite'))('esbuild')
const {JSDOM}=local?webRequire('jsdom'):createRequire(new URL('../runtime/history-test-tools/package.json',import.meta.url))('jsdom')
const dom=new JSDOM('<body><div id="root"></div></body>',{url:'https://reader.example.test/inbox/'})
Object.assign(globalThis,{window:dom.window,document:dom.window.document,IS_REACT_ACT_ENVIRONMENT:true})
Object.defineProperty(globalThis,'navigator',{value:dom.window.navigator,configurable:true})
const React=webRequire('react'),{act}=React,{createRoot}=webRequire('react-dom/client')
const file=new URL('../frontend-review/after/src/components/Article/ReaderThumbnail.jsx',import.meta.url).pathname
const bundled=await build({entryPoints:[file],write:false,bundle:true,platform:'node',format:'cjs',jsx:'automatic',plugins:[{name:'real-react',setup(b){
  b.onResolve({filter:/^react(?:\/.*)?$/},({path})=>({path:webRequire.resolve(path),external:true}))
}}]})
const component=new Module(file);component._compile(bundled.outputFiles[0].text,file)
const ReaderThumbnail=component.exports.default
const signed='/mf/proxy/'+'A'.repeat(43)+'=/aHR0cHM6Ly9leGFtcGxlLm9yZy9jb3Zlci5qcGc='
const original='https://images.example.test/cover.jpg'

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
  } finally {await act(async()=>root.unmount());dom.window.close()}
})
