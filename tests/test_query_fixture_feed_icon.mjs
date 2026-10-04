// Actual query entries() -> exact pinned FeedIcon -> real React SSR.
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
const web=join(root,'upstream/reactflux')
const webRequire=createRequire(join(web,'package.json'))
const {build}=createRequire(webRequire.resolve('vite'))('esbuild')
const React=webRequire('react')
const {renderToStaticMarkup}=webRequire('react-dom/server')
const source=join(web,'src/components/ui/FeedIcon.jsx')
assert.equal(createHash('sha256').update(await readFile(source)).digest('hex'),
  '86a3992573d4ebb00a2168da3ec84223098203f7fd0cdb294e8c8ffe020e7acb')
const query=process.env.READER_QUERY_FIXTURE_SOURCE || join(root,'tests/query_result_ownership_browser.py')
// Extract only the checked-in synthetic data assignments and entries function.
// Importing the browser acceptance module would launch its Harness at top level.
const extract=`
import ast,json,sys
from pathlib import Path
from types import SimpleNamespace
h=SimpleNamespace()
tree=ast.parse(Path(sys.argv[1]).read_text())
cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Harness')
init=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
seed=[n for n in init.body if isinstance(n,ast.Assign) and len(n.targets)==1 and ast.unparse(n.targets[0]) in ('self.categories','self.feeds')]
assert len(seed)==2
exec(compile(ast.Module(body=seed,type_ignores=[]),'<synthetic-harness-feeds>','exec'),{'self':h})
tree=ast.parse(Path(sys.argv[2]).read_text())
fixture=[n for n in tree.body if isinstance(n,ast.Assign) and len(n.targets)==1 and ast.unparse(n.targets[0]) in ('h.feeds[0][\\'icon\\']',)]
entry=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='entries')
ns={'h':h}
exec(compile(ast.Module(body=fixture+[entry],type_ignores=[]),'<actual-query-entries>','exec'),ns)
batches=[ns['entries'](start,count) for start,count in [(101,24),(301,2),(201,24),(401,2),(501,24)]]
print(json.dumps(batches))
`
const batches=JSON.parse(execFileSync(process.env.PYTHON || 'python',
  ['-c',extract,join(root,'tests/review_reader_harness.py'),resolve(query)],
  {encoding:'utf8',timeout:10000}))
const directory=await mkdtemp(join(tmpdir(),'reader-query-feed-icon-'))
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
  for (const batch of batches) for (const entry of batch) {
    // Rendering comes first so the original G fixture fails with the real
    // component's TypeError, not merely a mirrored DTO-shape assertion.
    assert.match(render(entry.feed),/<img/)
    assert.deepEqual(entry.feed.icon,{feed_id:entry.feed_id,icon_id:0})
    rendered++
  }
  assert.equal(rendered,76)
  const missing={...batches[0][0].feed}
  delete missing.icon
  assert.throws(()=>render(missing),/Cannot destructure property 'icon_id'/)
  assert.throws(()=>render({...missing,icon:null}),/Cannot destructure property 'icon_id'/)
  console.log(JSON.stringify({rendered_entry_feeds:rendered,original_missing_icon_rejected:true,
    null_icon_rejected:true,real_pinned_component:true,real_react_ssr:true,browser:false,network_requests:0}))
} finally {
  await retainTestDirectory(directory)
}
