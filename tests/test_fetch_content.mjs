import assert from 'node:assert/strict';
import fs from 'node:fs';
const source=fs.readFileSync(new URL('../upstream/reactflux/src/hooks/useEntryActions.js',import.meta.url),'utf8');
const block=source.slice(source.indexOf('  const handleFetchContent = async () => {'),source.indexOf('  const handleSaveToThirdPartyServices'));
const detail=fs.readFileSync(new URL('../upstream/reactflux/src/utils/reader-entry-detail.js',import.meta.url),'utf8');
const helper=detail.slice(detail.indexOf('export function readerFetchedBody'),detail.indexOf('export const needsReaderBodyCheck')).replace('export ','');
const readerFetchedBody=new Function(helper+';return readerFetchedBody')();
let current={id:1,content:'old body',reading_time:2};
let fetched;
let messages=[];
const run=new Function('contentState','getOriginalContent','Message','polyglot','extractHeadings','setActiveContent','readerFetchedBody',block+';return handleFetchContent')(
 {get:()=>({activeContent:current})},async()=>await fetched(),
 {success:x=>messages.push(['ok',x]),error:x=>messages.push(['error',x])},
 {t:x=>x},()=>[],value=>{current=value},readerFetchedBody);
const oldConsole=console.error;console.error=()=>{};
try {
 fetched=()=>Promise.reject(new Error('controlled'));
 assert.equal(await run(),false);assert.equal(current.content,'old body');
 fetched=()=>({content:''});assert.equal(await run(),false);assert.equal(current.content,'old body');
 fetched=()=>{current={id:2,content:'other body'};return {content:'stale old response'}};
 assert.equal(await run(),false);assert.equal(current.content,'other body');
 fetched=()=>({content:'new correct body',reading_time:3});
 assert.equal(await run(),true);assert.equal(current.content,'new correct body');
 const cached={id:7,user_id:1,content:'<p>Old English body.</p>',prepared_source:'reader_original_html',
  prepared_at:100,content_source_url:'https://fixture.test/original',fulltext_receipt:{html_sha256:'hash-a'},
  ai:{state:'done',score:9,body_completeness:{policy_version:'reader-body-completeness-v1',status:'verified'}},
  translation:{status:'done',source_hash:'hash-a',blocks_done:1,bilingual_html:'<p>Old bilingual body.</p>',chinese_html:'<p>旧中文正文。</p>'}};
 const before=JSON.stringify(cached);
 current=cached;fetched=()=>({content:cached.content});
 assert.equal(await run(),true);assert.equal(current.translation,cached.translation);assert.equal(current.fulltext_receipt,cached.fulltext_receipt);
 current=cached;fetched=()=>({content:'<p>New complete English body.</p>',reading_time:3});
 assert.equal(await run(),true);assert.equal(current.prepared_source,'reader_native_fetch');
 assert.equal(current.content_deferred,false);
 assert.equal(current.ai.body_completeness.status,'unverified');assert.equal(current.ai.body_completeness.checked_at,null);
 for(const field of ['fulltext_receipt','prepared_at','content_source_url'])assert.equal(field in current,false);
 assert.deepEqual(current.translation,{status:'source_changed'},'old translated HTML and hash cannot describe the new body');
 assert.equal(JSON.stringify(cached),before,'the previous successful cache object stays intact');
 current=null;assert.equal(await run(),false);
} finally {console.error=oldConsole}
console.log('PASS fetch failure/empty response/race preserve content; only actual success returns true');
