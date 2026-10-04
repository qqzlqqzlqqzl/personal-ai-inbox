// Render the actual pinned FeedIcon with the synthetic Python DTO. This is a
// component contract, not a browser/network/performance measurement.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { readFile, mkdtemp } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const web = process.env.READER_PERF_COMPONENT_ROOT || join(root, 'upstream/reactflux');
const source = join(web, 'src/components/ui/FeedIcon.jsx');
const raw = await readFile(source);
assert.equal(createHash('sha256').update(raw).digest('hex'),
  '86a3992573d4ebb00a2168da3ec84223098203f7fd0cdb294e8c8ffe020e7acb',
  'FeedIcon must be the actual component from ReactFlux 534eeb97723ac11025de4ec1ac56335072e3be52');
const webRequire = createRequire(join(web, 'package.json'));
const { build } = createRequire(webRequire.resolve('vite'))('esbuild');
const directory = await mkdtemp(join(tmpdir(), 'reader-feed-contract-'));
const output = join(directory, 'FeedIcon.cjs');
await build({
  entryPoints: [source], outfile: output, bundle: true, platform: 'node', format: 'cjs', jsx: 'automatic',
  alias: {'@': join(web, 'src')}, nodePaths: [join(web, 'node_modules')],
  define: {'import.meta.env.BASE_URL': '"/inbox/"'},
  plugins: [{name: 'offline-icon-hook-only', setup(b) {
    b.onResolve({filter: /^react(?:\/.*)?$/}, ({path}) => ({path: webRequire.resolve(path), external: true}));
    b.onResolve({filter: /(?:^|\/)useFeedIcons(?:\.js)?$/}, () => ({path: 'icon-hook', namespace: 'fixture'}));
    b.onResolve({filter: /(?:^|\/)feedIconsState(?:\.js)?$/}, () => ({path: 'icon-store', namespace: 'fixture'}));
    b.onLoad({filter: /.*/, namespace: 'fixture'}, ({path}) => ({
      contents: path === 'icon-hook'
        ? 'export default function useFeedIcons(id){globalThis.__fixtureIconIds.push(id);return undefined;}'
        : 'export function updateFeedIcon(){throw Error("unexpected mutation in static icon contract");}',
      loader: 'js',
    }));
  }}],
});
const script = `import json,sys
sys.path.insert(0,${JSON.stringify(join(root, 'tests'))})
from reader_loading_fixture import Fixture
f=object.__new__(Fixture);f.base='http://127.0.0.1:31415'
print(json.dumps({'feeds':f.api('/mf/v1/feeds','GET',{},None)[1],
 'list':f.api('/mf/v1/entries','GET',{'limit':['24']},None)[1],
 'detail':f.entry(1)}))`;
const input = spawnSync('python', ['-B', '-c', script], {encoding:'utf8', timeout:10000});
assert.equal(input.status, 0, input.stderr);
const payload = JSON.parse(input.stdout);
const React = webRequire('react');
const { renderToStaticMarkup } = webRequire('react-dom/server');
const { default: FeedIcon } = createRequire(import.meta.url)(output);
globalThis.__fixtureIconIds = [];
const render = (feed) => renderToStaticMarkup(React.createElement(FeedIcon, {feed}));
const feeds = [...payload.feeds, ...payload.list.entries.map(e=>e.feed), payload.detail.feed];
for (const feed of feeds) {
  assert.match(render(feed), /<img/);
  assert.deepEqual(feed.icon, {feed_id:7, icon_id:0});
}
assert.equal(feeds.length, 26);
assert.deepEqual(globalThis.__fixtureIconIds, Array(26).fill(0));
const oldFeed = structuredClone(feeds[0]); delete oldFeed.icon;
assert.throws(()=>render(oldFeed), /icon_id|undefined/, 'old G fixture must reproduce actual component failure');
assert.throws(()=>render({...feeds[0],icon:null}), /icon_id|null/);
assert.equal(payload.list.total,72); assert.equal(payload.list.entries.length,24);
console.log('PASS: pinned real FeedIcon renders 26 native-shaped DTOs; old missing/null icon controls reject; no browser or network run');
console.log('Retained component bundle:', directory);
