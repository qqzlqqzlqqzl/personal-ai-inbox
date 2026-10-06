import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {execFileSync} from 'node:child_process'
import vm from 'node:vm'
import { nextWindow, prefetchDecision, autoPrefetchDecision, AUTO_PREFETCH_PAGES } from '../patches/reading-session.js'
let w = nextWindow(null, 'all:1', 24, 24)
assert.equal(prefetchDecision(w, 5, 1800, 800), null)
assert.deepEqual(prefetchDecision(w, 6, 1800, 800), {reason:'quarter',target:6})
w = nextWindow(w, 'all:1', 48, 48)
assert.equal(prefetchDecision(w, 6, 4800, 800), null, 'append cannot recursively fetch entire library')
assert.equal(prefetchDecision(w, 29, 1900, 800), null)
assert.equal(prefetchDecision(w, 30, 1800, 800).target, 30)
w = nextWindow(w, 'all:1', 72, 72)
assert.equal(prefetchDecision(w, 30, 4800, 800), null)
assert.equal(prefetchDecision(w, 54, 1800, 800).reason, 'quarter')
assert.equal(prefetchDecision(w, 50, 80, 800).reason, 'near-end')
assert.equal(prefetchDecision(w, -1, 80, 800).reason, 'near-end', 'tail can prefetch while virtual rows are temporarily absent')
assert.equal(prefetchDecision(w, -1, 1800, 800), null, 'missing rows alone must not fetch more pages')
w = nextWindow(w, 'category:2', 20, 20)
assert.equal(w.start,0);assert.equal(w.size,20)
assert.equal(prefetchDecision(w, 4, 1800, 800), null)
assert.equal(prefetchDecision(w, 5, 1800, 800).target,5)
w = nextWindow(w, 'category:2', 20, 40)
assert.equal(prefetchDecision(w, 18, 0, 800),null,'duplicate-only pages stop speculative chaining; manual remains available')
w = nextWindow(w, 'category:2', 35, 60)
assert.equal(w.stalled,false);assert.equal(w.start,20)
assert.equal(prefetchDecision(w, 23, 500, 800).reason,'quarter')
assert.equal(prefetchDecision(nextWindow(null,'empty',0,0),0,0,800),null)
assert.equal(AUTO_PREFETCH_PAGES,10)
for(let page=0;page<10;page++)assert.deepEqual(autoPrefetchDecision(w,page),{reason:'startup',page:page+1})
assert.equal(autoPrefetchDecision(w,10),null)
assert.equal(autoPrefetchDecision(null,0),null)
assert.deepEqual(autoPrefetchDecision(nextWindow(null,'new-scope',24,24),0),{reason:'startup',page:1})

// Exercise the exact installer replacements on the reviewed hook's small
// updateEntries/call shape, without running the production-path installer.
const installer=new URL('../src/patch_reading_session.py',import.meta.url)
const extract=[
 'import ast,json,sys',
 'tree=ast.parse(sys.stdin.read())',
 'loop=next(n for n in ast.walk(tree) if isinstance(n,ast.For) and isinstance(n.target,ast.Tuple) and [x.id for x in n.target.elts]==["before","after"])',
 'print(json.dumps(ast.literal_eval(loop.iter)))',
].join('\n')
const replacements=JSON.parse(execFileSync(process.env.PYTHON||'python3',['-c',extract],{
 input:readFileSync(installer,'utf8'),encoding:'utf8',timeout:10000,
}))
const original=[
 'let reads=0; const contentState={get:()=>({entries:[]})};',
 'const setEntriesWithDeduplication=entries=>entries;',
 'const markDuplicatesAsRead=entries=>{reads+=entries.length};',
 'const updateEntries = (newEntries) => {',
 '    const duplicateEntries = setEntriesWithDeduplication(newEntries);',
 '    markDuplicatesAsRead(duplicateEntries)',
 '};',
 'const handleLoadMore = async (getEntries) => {',
 '    const response=await getEntries();',
 '    if (response.entries.length > 0) {',
 '        const newEntries=response.entries;',
 '        updateEntries(newEntries)',
 '    }',
 '};',
 'globalThis.run=handleLoadMore;globalThis.reads=()=>reads;',
].join('\n')
const apply=source=>{
 for(const [before,after] of replacements){
  if(source.includes(after)){assert.equal(source.split(after).length-1,1);assert.equal(source.includes(before),false)}
  else{assert.equal(source.split(before).length-1,1);source=source.replace(before,after)}
 }
 return source
}
const patched=apply(original)
assert.equal(apply(patched),patched,'prefetch hook patch is idempotent')
assert.throws(()=>apply(original.replace('updateEntries(newEntries)','unexpectedCall(newEntries)')),'unknown hook is rejected')
for(const prefetch of [true,false]){
 const context=vm.createContext({});vm.runInContext(patched,context)
 await context.run(async()=>({entries:[{id:1}]}),{prefetch})
 assert.equal(context.reads(),prefetch?0:1,'background pages cannot mark duplicate articles as read')
}
console.log('PASS per-batch threshold, bounded ten-page startup, reset, duplicates, empty')
