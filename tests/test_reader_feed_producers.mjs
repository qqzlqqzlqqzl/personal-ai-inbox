// All checked-in synthetic feed producers -> exact pinned FeedIcon -> real React SSR.
// No browser, network, Harness construction or production data is involved.
import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {createHash} from 'node:crypto'
import {mkdtemp, readFile} from 'node:fs/promises'
import {createRequire} from 'node:module'
import {tmpdir} from 'node:os'
import {join, resolve} from 'node:path'
import {fileURLToPath} from 'node:url'
import {retainTestDirectory} from './retain_test_directory.mjs'

const root=fileURLToPath(new URL('../',import.meta.url))
const web=process.env.READER_TEST_UPSTREAM || join(root,'upstream/reactflux')
const webRequire=createRequire(join(web,'package.json'))
const {build}=createRequire(webRequire.resolve('vite'))('esbuild')
const React=webRequire('react')
const {renderToStaticMarkup}=webRequire('react-dom/server')
const source=join(web,'src/components/ui/FeedIcon.jsx')
assert.equal(createHash('sha256').update(await readFile(source)).digest('hex'),
  '86a3992573d4ebb00a2168da3ec84223098203f7fd0cdb294e8c8ffe020e7acb')
const batches=JSON.parse(execFileSync(process.env.PYTHON || 'python',
  ['-B',join(root,'tests/reader_feed_fixture_contract.py')],{encoding:'utf8',timeout:10000}))
const directory=await mkdtemp(join(tmpdir(),'reader-reading-feed-icon-'))
try {
  const output=join(directory,'FeedIcon.cjs')
  await build({entryPoints:[source],outfile:output,bundle:true,platform:'node',format:'cjs',jsx:'automatic',
    define:{'import.meta.env.BASE_URL':'"/inbox/"'},plugins:[{name:'synthetic-icon-cache',setup(b){
      b.onResolve({filter:/^react(?:\/.*)?$/},({path})=>({path:webRequire.resolve(path),external:true}))
      b.onResolve({filter:/^@\/utils\/url$/},()=>({path:join(web,'src/utils/url.js')}))
      b.onResolve({filter:/^@\/hooks\/useFeedIcons$/},()=>({path:'cache',namespace:'fixture'}))
      b.onResolve({filter:/^@\/store\/feedIconsState$/},()=>({path:'writes',namespace:'fixture'}))
      b.onLoad({filter:/.*/,namespace:'fixture'},({path})=>({loader:'js',contents:path==='cache'
        ? 'export default ()=>null'
        : 'export const updateFeedIcon=()=>{throw new Error("unexpected SSR cache write")}' }))
    }}]})
  const FeedIcon=createRequire(import.meta.url)(output).default
  const render=feed=>renderToStaticMarkup(React.createElement(FeedIcon,{feed}))
  let rendered=0
  for (const [producer, feeds] of Object.entries(batches)) for (const feed of feeds) {
    // Rendering comes first so the original 7554 fixture fails with the real
    // component's TypeError, not merely a mirrored DTO-shape assertion.
    assert.match(render(feed),/<img/)
    assert.equal(feed.icon.feed_id,feed.id); assert.equal(feed.icon.icon_id,0)
    rendered++
  }
  assert.equal(rendered,43)
  const missing={...batches['harness-default'][0]}
  delete missing.icon
  assert.throws(()=>render(missing),/Cannot destructure property 'icon_id'/)
  assert.throws(()=>render({...missing,icon:null}),/Cannot destructure property 'icon_id'/)
  console.log(JSON.stringify({rendered_producer_feeds:rendered,original_missing_icon_rejected:true,
    null_icon_rejected:true,real_pinned_component:true,real_react_ssr:true,browser:false,network_requests:0}))
} finally {
  await retainTestDirectory(directory)
}
