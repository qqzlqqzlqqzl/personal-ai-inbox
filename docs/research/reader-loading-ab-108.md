# Reader #108 immutable A/B execution candidate

This route compares fixed 879 with the immutable 7554 regression build. It does
not contain the later deferred-panel optimization. The 7554 producer completed
with **failure** at reading-focus; its six query checks passed. A static build
identity receipt does not replace the producer outcome or remaining regressions.

`tests/fixtures/reader-loading-ab-inputs.json` binds each input separately to its
same-repository artifact ID, archive length/SHA, actual ordered build manifest,
actual file count, full Git identities, producer run/attempt and conclusion.
The candidate presently has 125 files by observation, not by a generic imposed
limit. A later split build needs a newly reviewed exact manifest/count and pins.
The original fixed 879 baseline constants remain unchanged.

After source review the main publisher can push this original-object branch or
run `.github/workflows/reader-loading-ab.yml` on that exact revision. No workflow
inputs accept arbitrary URLs, artifact IDs, commands or credentials. The existing
short-lived `GITHUB_TOKEN` gets only job-level `actions: read` and `contents: read`.
Only two named artifact downloads and their exact producer metadata are read.

Each keyboard, touch and weak-network job runs on one Ubuntu 22.04 VM with fixed
Python 3.12.14, Playwright 1.63.0 and Chromium 1243/153.0.8010.12. The actual browser
SHA is checked. Signed-index/exact-SHA CJK admission, sandbox, loopback rejection,
API no-store, six-image strict cache proof, 24/48/72 pagination and original
5-second/15-second inner deadlines remain. Ubuntu 22.04 is a temporary hosted
label, scheduled to retire 2027-04-17; no fallback runner is supplied.

The actual sequence is A1 B1, B2 A2, A3 B3, B4 A4, A5 B5. Every segment invokes
the same unmodified `one_pair` function in a new browser process and new context;
“cold” does not claim cold OS caches. The separate cache page remains in that
sample's context. The process is closed before the next segment starts, and
started/finished monotonic timestamps prove no overlap. All instrument bytes are
hashed once and rechecked before each segment. Both sides use gzip6-static-text-v1
and exactly the same synthetic fixture and instrumentation. The whole measurement
still has a 590-second deadline; failures do not retry or extend it.

The ordinary performance CLI still runs five pairs. The internal single-sample
entry labels `pairs_requested: 1`; it cannot independently pass the existing
five-pair comparator. Only ten completed serial segments with pair IDs 1..5 per
side can aggregate and call the existing strict comparator. Its request-level
cache proofs are replayed before a comparison is emitted. Each side keeps its own
product identity; the instrument identity must match, not the product head.

Evidence includes exact input/producer receipts, source/toolchain/font identities,
instrument manifest, ten start/finish receipts, each synthetic pair/result/boundary,
failure screenshots, separate five-pair results and comparison JSON. Browser
profiles, raw downloaded archives/builds, GH stderr and raw process output remain
outside the upload root. Failed and incomplete samples stay failed and retained.
No CPU utilization, process RSS, physical-device or production performance claim
is made. Baseline/candidate comparison cannot be inferred from the earlier raw
transport runs or from ordinary CI success alone.

Local checks are source/metadata/ZIP materialization and synthetic negative
controls. Real A/B execution remains **NOT RUN** until this exact candidate is
reviewed, published and produces all three hosted artifacts.
