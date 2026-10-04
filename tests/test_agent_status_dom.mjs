import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {mountStatusPage} from '../patches/agent-status/status-controller.mjs';
class Node{constructor(){this.children=[];this.listeners={};this.value='';}set textContent(value){this.value=String(value);this.children=[];}get textContent(){return this.value+this.children.map(x=>x.textContent).join('');}append(...children){this.children.push(...children);}replaceChildren(...children){this.children=children;this.value='';}addEventListener(name,fn){this.listeners[name]=fn;}removeEventListener(name){delete this.listeners[name];}}
const originals={document:globalThis.document,setInterval:globalThis.setInterval,clearInterval:globalThis.clearInterval};
globalThis.document={hidden:false,createElement:()=>new Node(),createTextNode:value=>{const n=new Node();n.textContent=value;return n;},addEventListener(){},removeEventListener(){}};
const timers=new Set();globalThis.setInterval=()=>{const timer=Symbol();timers.add(timer);return timer;};globalThis.clearInterval=timer=>timers.delete(timer);
const nodes=Object.fromEntries(['notice','sample-time','pull-time','page-time','known','counts','tasks','refresh'].map(x=>[x,new Node()]));const root={querySelector:id=>nodes[id.slice(1)]};
const valid=JSON.parse(readFileSync(new URL('../patches/agent-status/mock-state.json',import.meta.url)));valid.sample.tasks[0].name='PRIVATE_MARKER';let outcome=valid;
const stop=mountStatusPage(root,async()=>{if(outcome instanceof Error)throw outcome;return outcome;});
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));await tick();assert.ok(!nodes.tasks.textContent.includes('PRIVATE_MARKER'));assert.ok(nodes.tasks.textContent.includes('运行中'));assert.equal(nodes.tasks.children.length,2);
const refresh=async value=>{outcome=value;nodes.refresh.listeners.click();await tick();};
await refresh(new Error('network'));assert.ok(!nodes.tasks.textContent.includes('PRIVATE_MARKER'));assert.equal(nodes.tasks.children.length,2);
const malformed=structuredClone(valid);malformed.sample.tasks={unexpected:1};await refresh(malformed);assert.ok(!nodes.tasks.textContent.includes('PRIVATE_MARKER'));assert.equal(nodes.tasks.children.length,2);
for(const status of [403,401]){
  await refresh(valid);await refresh(Object.assign(new Error('auth'),{status}));assert.ok(!nodes.tasks.textContent.includes('PRIVATE_MARKER'));assert.ok(!nodes.tasks.textContent.includes('运行中'));assert.equal(nodes['sample-time'].textContent,'未知');assert.equal(nodes['pull-time'].textContent,'未知');assert.ok(nodes.counts.textContent.includes('—'));assert.ok(nodes.notice.textContent.includes('私有状态已清除'));
  await refresh(malformed);assert.ok(!nodes.tasks.textContent.includes('PRIVATE_MARKER'));
}
await refresh(valid);stop();assert.ok(!nodes.tasks.textContent.includes('PRIVATE_MARKER'));assert.ok(!nodes.tasks.textContent.includes('运行中'));assert.equal(timers.size,0);
Object.assign(globalThis,originals);console.log('DOM regression: transient retention, malformed tasks, 401/403 clearing and disposal passed');
