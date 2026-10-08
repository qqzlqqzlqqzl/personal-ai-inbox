import assert from "node:assert/strict";
import fs from "node:fs";
const root = new URL("../upstream/reactflux/src/", import.meta.url);
const source = (name) => fs.readFileSync(new URL(name, root), "utf8").replace(/^import[\s\S]*?from ["'][^"']+["']\s*$/gm, "").replace(/^export default .*$/gm, "");
const dedupe = new Function(source("utils/deduplicate.js") + ";return removeDuplicateEntries")();
let state = {entries: [], articleListOffset: 0, articleListSnapshotRevision: 0, infoFrom: "all", filterString: ""};
let visible = true;
const atom = value => ({get: () => value, set: v => {value = v}});
const dependencies = {
  contentState: {get: () => ({isArticleListReady:true,...state}), setKey: (key, value) => {state[key] = value}},
  settingsState: atom({pageSize: 20, showStatus: "all", orderBy: "published_at", orderDirection: "desc"}),
  polyglotState: atom({polyglot: {t: x => x}}),
  useStore: store => store.get(), useRef: value => ({current: value}), atom,
  createSetter: store => value => store.set(value),
  setEntriesWithDeduplication: entries => {const result = dedupe(entries, "title"); state.entries = result.entries; return result.duplicates},
  markDuplicatesAsRead: () => {}, prepareEntry: e => e,
  setTotal: total => {state.total = total}, setLoadMoreVisible: value => {visible = value},
  incrementArticleListSnapshotRevision: () => {state.articleListSnapshotRevision++},
  setLoadMoreError: () => {}, aiFilterEnabled: () => true, AI_PAGE_SIZE: 24,
  createArticleListRequestKey: () => "same", getDataSessionRevision: () => 1, getReadingCalendarSnapshot: () => ({ready:true}),
  getEntryMutationSnapshot: () => ({pendingRequests: 0}), isEntryMutationSnapshotCurrent: () => true,
  Message: {error: message => {throw Error(message)}},
};
const evaluate = (file, result) => new Function(...Object.keys(dependencies), source(file) + ";return " + result)(...Object.values(dependencies));
const initial = evaluate("hooks/useArticleList.js", "handleResponses");
const useLoadMore = evaluate("hooks/useLoadMore.js", "useLoadMore");
const records = Array.from({length: 52}, (_, index) => ({id: index + 1, title: index < 48 ? "Repeated article" : "Unique " + index}));
initial({entries: records.slice(0, 24), total: 52});
assert.equal(state.entries.length, 1);
assert.equal(state.articleListOffset, 24);
const {handleLoadMore} = useLoadMore();
const offsets = [];
const fetchPage = async (_status, _starred, params) => {offsets.push(params.offset);return {entries: records.slice(params.offset, params.offset + params.limit), total: 52}};
await handleLoadMore(fetchPage);
assert.equal(state.entries.length, 1);
assert.equal(state.articleListOffset, 48);
assert.equal(visible, true);
await handleLoadMore(fetchPage);
assert.deepEqual(offsets, [24, 48]);
assert.deepEqual(state.entries.map(e => e.id), [1, 49, 50, 51, 52]);
assert.equal(state.articleListOffset, 52);
assert.equal(visible, false);
initial({entries: records.slice(0, 12), total: 52});
assert.equal(state.articleListOffset, 12);
await handleLoadMore(async () => {state.articleListSnapshotRevision++;return {entries: records.slice(12, 36), total: 52}});
assert.equal(state.articleListOffset, 12, "stale snapshot must not advance cursor");
console.log("PASS: duplicate-only middle page, no lost unique tail, initial reset, stale snapshot ignored");

// Native total counts the remaining cursor range, not the accumulated list.
dependencies.aiFilterEnabled = () => false;
dependencies.getTimestamp = value => Number(value);
state = {entries:Array.from({length:40},(_,i)=>({id:i+1,title:'Native '+i,published_at:100-i})),articleListOffset:40,articleListSnapshotRevision:1,infoFrom:'feed',filterString:''};
visible = true;
const native = evaluate('hooks/useLoadMore.js','useLoadMore')();
await native.handleLoadMore(async()=>({entries:Array.from({length:20},(_,i)=>({id:i+41,title:'Native '+(i+40),published_at:60-i})),total:25}));
assert.equal(visible,true,'25 remaining with a full 20-row page must continue even after 60 cumulative entries');
await native.handleLoadMore(async()=>({entries:Array.from({length:5},(_,i)=>({id:i+61,title:'Tail '+i,published_at:40-i})),total:5}));
assert.equal(visible,false);
assert.equal(state.entries.length,65);
console.log('PASS native remaining total vs cumulative count, true tail stopping');

// Batch imports: created_at buckets must never choose the same ID boundary twice.
for (const direction of ['desc', 'asc']) {
  dependencies.settingsState.set({pageSize:20,showStatus:'all',orderBy:'created_at',orderDirection:direction});
  const initialIds = direction === 'desc' ? Array.from({length:20},(_,i)=>100-i) : Array.from({length:20},(_,i)=>1+i);
  state={entries:initialIds.map(id=>({id,title:'ID '+id,created_at:123})),articleListOffset:20,articleListSnapshotRevision:2,infoFrom:'feed',filterString:''};
  visible=true;
  const loader=evaluate('hooks/useLoadMore.js','useLoadMore')();
  const key=direction==='desc'?'before_entry_id':'after_entry_id';
  const expectedFirst=direction==='desc'?81:20;
  await loader.handleLoadMore(async(_status,_starred,params)=>{
    assert.equal(params[key],expectedFirst);
    return {total:61,entries:Array.from({length:20},(_,i)=>{const id=direction==='desc'?80-i:21+i;return{id,title:'ID '+id,created_at:123}})};
  });
  await loader.handleLoadMore(async(_status,_starred,params)=>{
    assert.equal(params[key],direction==='desc'?61:40);
    return {total:0,entries:[]};
  });
  assert.equal(visible,false);
}
console.log('PASS created-at native ID boundaries advance both directions despite equal timestamps');

// A changed ordered result is discarded before append/offset commit. The
// existing first-page refresh then exposes the new view, not a stored snapshot.
dependencies.aiFilterEnabled = () => true;
dependencies.settingsState.set({pageSize:20,showStatus:'all',orderBy:'published_at',orderDirection:'desc'});
dependencies.setLoadMoreError = value => {state.loadMoreError = value};
const messages = [];
dependencies.Message.error = message => {messages.push(message)};
const commitInitial = evaluate('hooks/useArticleList.js', 'handleResponses');
const revisionA = 'a'.repeat(64), revisionB = 'b'.repeat(64), revisionC = 'c'.repeat(64);
const base = Array.from({length:52},(_,i)=>({id:i+1,title:'Unique '+(i+1)}));
const firstPage = (rows, revision) => {
  commitInitial({entries:rows.slice(0,24),total:rows.length,ai_revision:revision});
  state.isArticleListReady = true; // the real initial-fetch hook commits readiness
};
const resetDynamic = () => {
  state={entries:[],articleListOffset:0,articleListRevision:0,articleListSnapshotRevision:0,
    articleListAiRefreshes:0,infoFrom:'all',filterString:'',isArticleListReady:true};
  visible=true;messages.length=0;
};
for (const scenario of ['unchanged','insert','rescore']) {
  resetDynamic(); firstPage(base,revisionA);
  const loader=evaluate('hooks/useLoadMore.js','useLoadMore')();
  const rows=scenario==='insert'?[{id:99,title:'Unique 99'},...base]:
    scenario==='rescore'?[base[49],...base.filter(e=>e.id!==50)]:base;
  const revision=scenario==='unchanged'?revisionA:revisionB;
  const fetch=async(_status,_starred,params)=>{
    if(params.ai_revision!==revision)return{entries:[],total:rows.length,ai_revision:revision,ai_result_changed:true};
    return{entries:rows.slice(params.offset,params.offset+params.limit),total:rows.length,ai_revision:revision};
  };
  await loader.handleLoadMore(fetch);
  if(scenario!=='unchanged') {
    assert.equal(state.articleListOffset,24,'discarded changed page cannot advance cursor');
    assert.deepEqual(state.entries.map(e=>e.id),base.slice(0,24).map(e=>e.id));
    assert.equal(state.articleListRevision,1);
    assert.equal(state.isArticleListReady,false);
    assert.equal(state.articleListAiRefreshes,1);
    firstPage(rows,revision);
  }
  for(let attempts=0;visible&&attempts<4;attempts++)await loader.handleLoadMore(fetch);
  assert.equal(visible,false);
  assert.deepEqual(state.entries.map(e=>e.id),rows.map(e=>e.id));
  assert.equal(state.entries.length,new Set(state.entries.map(e=>e.id)).size);
  assert.equal(state.total,rows.length);
  console.log(JSON.stringify({scenario,ids:state.entries.map(e=>e.id),total:state.total,
    offset:state.articleListOffset,automaticRefreshes:state.articleListRevision}));
}

// A second change must stop auto-refreshing and expose the existing retry UI.
resetDynamic();firstPage(base,revisionA);
const bounded=evaluate('hooks/useLoadMore.js','useLoadMore')();
await bounded.handleLoadMore(async()=>({entries:[],total:53,ai_revision:revisionB,ai_result_changed:true}));
firstPage(base,revisionB);
const beforeSecond=state.entries.map(e=>e.id);
await bounded.handleLoadMore(async()=>({entries:[],total:54,ai_revision:revisionC,ai_result_changed:true}));
assert.equal(state.articleListRevision,1,'no second automatic refresh');
assert.equal(state.articleListOffset,24);
assert.deepEqual(state.entries.map(e=>e.id),beforeSecond);
assert.equal(state.loadMoreError,true);
assert.equal(state.articleListAiRefreshRequired,true);
assert.equal(messages.length,1,'the change cannot be swallowed silently');
await bounded.handleLoadMore(async()=>{throw Error('manual retry must refresh the first page, not request another offset')});
assert.equal(state.articleListRevision,2);
assert.equal(state.articleListAiRefreshes,0);
assert.equal(state.isArticleListReady,false);
assert.equal(state.loadMoreError,false);

// An invalid/missing revision is an explicit error; it must never append data.
for(const bad of [undefined,'bad',[revisionA]]) {
  resetDynamic();firstPage(base,revisionA);
  const loader=evaluate('hooks/useLoadMore.js','useLoadMore')();
  await loader.handleLoadMore(async()=>({entries:base.slice(24,48),total:52,ai_revision:bad}));
  assert.equal(state.loadMoreError,true);
  assert.equal(state.articleListOffset,24);
  assert.equal(state.entries.length,24);
  assert.equal(state.articleListRevision,0);
}
console.log('PASS dynamic result replacement, one refresh budget, explicit retry, invalid revision refusal');

// A slow page belongs to its view, not to whichever view is now ready. Exercise
// the real hook including its module-level lock and a late old finally.
for (const change of ['sort', 'snapshot', 'session']) {
  for (const oldFails of [false, true]) {
    let requestKey='a',session=1,reads=0,newCalls=0;
    dependencies.createArticleListRequestKey=()=>requestKey;
    dependencies.getDataSessionRevision=()=>session;
    dependencies.markDuplicatesAsRead=()=>{reads++};
    resetDynamic();firstPage(base,revisionA);
    const realHook=evaluate('hooks/useLoadMore.js','useLoadMore');
    const oldPage=Promise.withResolvers(),newPage=Promise.withResolvers();
    const old=realHook().handleLoadMore(()=>oldPage.promise,{prefetch:true});
    assert.equal(realHook().loadingMore,true);
    if(change==='sort')requestKey='b';
    else if(change==='snapshot')state.articleListSnapshotRevision++;
    else session++;
    assert.equal(realHook().loadingMore,false,'a retired view cannot block the new ready view');
    const getNew=()=>{newCalls++;return newPage.promise};
    const pending=realHook().handleLoadMore(getNew,{prefetch:true});
    assert.equal(newCalls,1,'new pagination starts before the old page settles');
    const before=JSON.stringify(state),oldConsole=console.error;
    try {
      console.error=()=>{};
      if(oldFails)oldPage.reject(Error('retired request'));
      else oldPage.resolve({entries:[{id:999,title:'Retired'}],total:100,ai_revision:revisionA});
      await old;
    } finally {console.error=oldConsole}
    assert.equal(JSON.stringify(state),before,'retired completion cannot mutate the new view');
    assert.equal(realHook().loadingMore,true,'old finally cannot release the new owner');
    await realHook().handleLoadMore(getNew,{prefetch:true});
    assert.equal(newCalls,1,'same-view pagination stays single-flight');
    newPage.resolve({entries:base.slice(24,48),total:52,ai_revision:revisionA});await pending;
    assert.equal(realHook().loadingMore,false);assert.equal(state.articleListOffset,48);
    assert.equal(reads,0,'background pages never mark duplicates as read');
  }
}
console.log('PASS pagination owners isolate sort, snapshot and session changes, late success/error and same-view single-flight');
