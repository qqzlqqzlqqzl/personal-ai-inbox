import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createStatusCache} from '../patches/agent-status/status-cache.mjs';
import {validateStatus} from '../patches/agent-status/status-contract.mjs';
const fixture=JSON.parse(readFileSync(new URL('../patches/agent-status/mock-state.json',import.meta.url))),now=new Date('2026-10-02T05:30:00Z').getTime();
const copy=()=>structuredClone(fixture);
for(const status of [401,403]){
  const cache=createStatusCache();cache.accept(copy(),now);cache.fail({status});assert.equal(cache.get().last,null);assert.equal(cache.get().restricted,status);
  cache.fail(new Error('network'));assert.equal(cache.get().last,null);assert.equal(cache.get().restricted,status);
}
const cache=createStatusCache();cache.accept(copy(),now);cache.fail(new Error('network'));assert.equal(cache.get().last.sample.tasks.length,2);assert.equal(cache.get().requestFailed,true);
const mutations=[v=>v.sample.tasks={},v=>v.sample.tasks=[null],v=>v.sample.tasks[0].state='bad',v=>v.sample.tasks[0].state=['running'],v=>v.sample.tasks[0].state={state:'running'},v=>v.sample.tasks[0].state=true,v=>v.sample.tasks[0].name={},v=>v.sample.tasks[0].model=null,v=>v.sample.tasks[0].prompt='PRIVATE_MARKER',v=>v.sample.tasks[1].name=v.sample.tasks[0].name,v=>v.sample.statistics.active=0,v=>v.sample.schema_version=2,v=>v.sample.sequence=true,v=>v.sample.observed_at='2026-02-30T00:00:00Z',v=>v.sample.observed_at='2026-10-02T06:30:00Z',v=>v.sample.statistics.capacity=-1,v=>v.sample=null];
for(const mutate of mutations){const bad=copy();mutate(bad);assert.throws(()=>cache.accept(bad,now));assert.equal(cache.get().last.sample.tasks.length,2);const empty=createStatusCache();assert.throws(()=>empty.accept(bad,now));assert.equal(empty.get().last,null);}
const incoming=copy();cache.accept(incoming,now);incoming.sample.tasks[0].name='MUTATED_PRIVATE';assert.notEqual(cache.get().last.sample.tasks[0].name,'MUTATED_PRIVATE');
cache.fail({response:{status:403}});assert.equal(cache.get().last,null);cache.accept(copy(),now);assert.equal(cache.get().restricted,null);
for(const message of ['Invalid auth','Stale auth request','Stale auth response']){cache.accept(copy(),now);cache.fail(new Error(message));assert.equal(cache.get().last,null);}
assert.equal(validateStatus(copy(),now).sample.statistics.active,2);
console.log('28 adversarial cache/auth/contract scenarios passed');