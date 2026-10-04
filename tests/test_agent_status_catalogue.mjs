import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {validateStatus} from '../patches/agent-status/status-contract.mjs';
import {createStatusCache} from '../patches/agent-status/status-cache.mjs';
import {sha256,sampleFingerprint} from '../patches/agent-status/status-fingerprint.mjs';
const now=Date.parse('2026-10-04T01:45:00Z');
const fixture=JSON.parse(readFileSync(new URL('../patches/agent-status/mock-state.json',import.meta.url)));
const NAME='SYNTHETIC_UNAPPROVED_TASK',MODEL='SYNTHETIC_UNAPPROVED_MODEL';
const copy=()=>{const v=structuredClone(fixture);v.sample.tasks[0].name=NAME;v.sample.tasks[0].model=MODEL;return v;};
const absent=v=>{const text=JSON.stringify(v);assert.ok(!text.includes(NAME));assert.ok(!text.includes(MODEL));};
for(const text of ['', 'abc', '中文 e\u0301 🚀', ...Array.from({length:150},(_,n)=>'x'.repeat(n)), 'long😀'.repeat(12000)]){
  assert.equal(sha256(text),createHash('sha256').update(text).digest('hex'));
}
const raw=copy(),fingerprint=sampleFingerprint(raw.sample),cache=createStatusCache();
cache.accept(raw,now);absent(cache.get());
assert.ok(!JSON.stringify(cache.get()).includes(fingerprint),'private fingerprint must never be returned');
assert.deepEqual(cache.get().last.sample.tasks,[{state:'running'},{state:'waiting'}]);
assert.deepEqual(cache.get().last.sample.statistics,raw.sample.statistics);
cache.accept(copy(),now);assert.equal(cache.get().requestFailed,false);
for(const key of ['name','model']){
  const changed=copy();changed.sample.tasks[0][key]+='_CHANGED';
  assert.throws(()=>cache.accept(changed,now),/sequence_conflict/);absent(cache.get());
}
const reordered=copy();reordered.sample.tasks=reordered.sample.tasks.map(t=>Object.fromEntries(Object.entries(t).reverse()));
reordered.sample.statistics=Object.fromEntries(Object.entries(reordered.sample.statistics).reverse());
cache.accept(reordered,now);absent(cache.get());
cache.fail(new Error('synthetic transport'));absent(cache.get());assert.equal(cache.get().requestFailed,true);
for(const mutation of [v=>v.sample.sequence--,v=>v.sample.observed_at='2026-10-01T00:00:00Z']){
  const lower=copy();mutation(lower);assert.throws(()=>cache.accept(lower,now),/regression/);absent(cache.get());
}
const projected=validateStatus(copy(),now);absent(projected);
assert.deepEqual(validateStatus(projected,now).sample.tasks,projected.sample.tasks);
const highWater=createStatusCache();highWater.accept(copy(),now);
const unknown={sample:null,last_successful_pull_at:null,last_attempt_at:null,pull_status:'unknown',freshness:'unknown',stale_after_seconds:1800,target_interval_seconds:600};
highWater.accept(unknown,now);assert.equal(highWater.get().last.sample.sequence,raw.sample.sequence);assert.equal(highWater.get().requestFailed,true);absent(highWater.get());
const lowerAfterUnknown=copy();lowerAfterUnknown.sample.sequence--;assert.throws(()=>highWater.accept(lowerAfterUnknown,now),/regression/);
const conflictAfterUnknown=copy();conflictAfterUnknown.sample.tasks[0].name+='_CHANGED';assert.throws(()=>highWater.accept(conflictAfterUnknown,now),/sequence_conflict/);
const empty=copy();empty.sample.sequence++;empty.sample.tasks=[];empty.sample.statistics={total:0,capacity:6,active:0,waiting:0,completed:0,failed:0,unknown:0};
highWater.accept(empty,now);assert.equal(highWater.get().last.sample.statistics.total,0);assert.equal(highWater.get().requestFailed,false);
highWater.accept(unknown,now);assert.equal(highWater.get().last.sample.sequence,empty.sample.sequence);assert.equal(highWater.get().requestFailed,true);
highWater.clearPrivate();assert.equal(highWater.get().last,null);highWater.accept(copy(),now);assert.equal(highWater.get().requestFailed,false);
for(const status of [401,403]){
  cache.fail({status});assert.equal(cache.get().last,null);
  const next=copy();next.sample.sequence=0;cache.accept(next,now);absent(cache.get());
}
cache.clearPrivate();assert.equal(cache.get().last,null);
const surrogate=copy();surrogate.sample.tasks[0].name='\ud800';assert.throws(()=>validateStatus(surrogate,now));
const unicode=copy();unicode.sample.tasks[0].name='合法但未批准的名字😀';assert.deepEqual(validateStatus(unicode,now).sample.tasks[0],{state:'running'});
console.log('Catalogue deny-by-default, 154 SHA-256 vectors, private conflict identity, replay and auth retirement passed');
