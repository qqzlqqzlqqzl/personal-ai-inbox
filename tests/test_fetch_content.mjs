import assert from 'node:assert/strict';
import fs from 'node:fs';
const source=fs.readFileSync(new URL('../upstream/reactflux/src/hooks/useEntryActions.js',import.meta.url),'utf8');
const block=source.slice(source.indexOf('  const handleFetchContent = async () => {'),source.indexOf('  const handleSaveToThirdPartyServices'));
let current={id:1,content:'old body',reading_time:2};
let fetched;
let messages=[];
const run=new Function('contentState','getOriginalContent','Message','polyglot','extractHeadings','setActiveContent',block+';return handleFetchContent')(
 {get:()=>({activeContent:current})},async()=>await fetched(),
 {success:x=>messages.push(['ok',x]),error:x=>messages.push(['error',x])},
 {t:x=>x},()=>[],value=>{current=value});
const oldConsole=console.error;console.error=()=>{};
try {
 fetched=()=>Promise.reject(new Error('controlled'));
 assert.equal(await run(),false);assert.equal(current.content,'old body');
 fetched=()=>({content:''});assert.equal(await run(),false);assert.equal(current.content,'old body');
 fetched=()=>{current={id:2,content:'other body'};return {content:'stale old response'}};
 assert.equal(await run(),false);assert.equal(current.content,'other body');
 fetched=()=>({content:'new correct body',reading_time:3});
 assert.equal(await run(),true);assert.equal(current.content,'new correct body');
 current=null;assert.equal(await run(),false);
} finally {console.error=oldConsole}
console.log('PASS fetch failure/empty response/race preserve content; only actual success returns true');
