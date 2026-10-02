import assert from 'node:assert/strict';
import {createReaderStatusClient} from '../patches/agent-status/reader-status-client.mjs';
let call;
const apiClient={get:async(path,options)=>{call={path,options};return {sample:null,freshness:'unknown'};}};
const read=createReaderStatusClient(apiClient,'https://example.test/mf/','https://example.test/inbox/');
assert.equal((await read()).freshness,'unknown');assert.equal(call.path,'v1/ai/agent-status');
assert.equal(call.options.cache,'no-store');assert.equal(call.options.redirect,'error');assert.ok(call.options.signal);
assert.equal(call.options.headers,undefined); // Existing client must retain its onRequest auth injection.
assert.throws(()=>createReaderStatusClient(apiClient,'https://other.test/mf/','https://example.test/inbox/'));
assert.throws(()=>createReaderStatusClient(apiClient,'https://example.test/public/','https://example.test/inbox/'));
console.log('8 Reader integration client checks passed');