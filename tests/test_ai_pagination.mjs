import assert from "node:assert/strict";
import fs from "node:fs";
const root = new URL("../upstream/reactflux/src/", import.meta.url);
const source = (name) => fs.readFileSync(new URL(name, root), "utf8").replace(/^import[\s\S]*?from ["'][^"']+["']\s*$/gm, "").replace(/^export default .*$/gm, "");
const dedupe = new Function(source("utils/deduplicate.js") + ";return removeDuplicateEntries")();
let state = {entries: [], articleListOffset: 0, articleListSnapshotRevision: 0, infoFrom: "all", filterString: ""};
let visible = true;
const atom = value => ({get: () => value, set: v => {value = v}});
const dependencies = {
  contentState: {get: () => state, setKey: (key, value) => {state[key] = value}},
  settingsState: atom({pageSize: 20, showStatus: "all", orderBy: "published_at", orderDirection: "desc"}),
  polyglotState: atom({polyglot: {t: x => x}}),
  useStore: store => store.get(), useRef: value => ({current: value}), atom,
  createSetter: store => value => store.set(value),
  setEntriesWithDeduplication: entries => {const result = dedupe(entries, "title"); state.entries = result.entries; return result.duplicates},
  markDuplicatesAsRead: () => {}, prepareEntry: e => e,
  setTotal: total => {state.total = total}, setLoadMoreVisible: value => {visible = value},
  incrementArticleListSnapshotRevision: () => {state.articleListSnapshotRevision++},
  setLoadMoreError: () => {}, aiFilterEnabled: () => true, AI_PAGE_SIZE: 24,
  createArticleListRequestKey: () => "same", getDataSessionRevision: () => 1,
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
