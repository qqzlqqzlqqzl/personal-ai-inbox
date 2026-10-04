# Reader #108: bounded loading measurement candidate

This is measurement infrastructure, not a product optimization or a production
performance result. The first source inspection used release
`9a66803bdb191dce023dca9e3800821ee570203e`, tree
`65355bc7b2849809c83121e90e953429ba81ffde`, src
`0049640f793f5dd505bf2fea6fd8274f0f074f08`. The original CI artifact built at
`879ff545aa19330ac884dd320fdd3ccfbd265d9b` has that identical whole tree.

## Exact input and start contract

Use the existing locked development environment: Playwright 1.63.0 and its
already installed full Chromium revision 1243, version 153.0.8010.12. This tool
does not install anything and has no arbitrary browser executable override.
Preserve the virtual environment's launcher path when invoking Python; resolving
its symlink to the system interpreter can select a different package environment.

Supply a reviewed original Reader CI artifact ZIP, its SHA256, the extracted
`runtime/browser-build` directory, a reviewed JSON manifest of every build file
(`path`, `bytes`, `sha256`), that manifest's SHA256, and the expected source tree.
The entry verifies the artifact checksum, CRC, bounded member set, exact CI
identity and every build byte before launch. It reads the build into memory;
it neither overwrites compiled JavaScript nor copies the build to another cache.
The output parent must already exist, have no symlink ancestor, and be outside
the input build. A fresh owner-private directory is created for every invocation.

Baseline input identity:

- Original artifact: `PR102_879ff5_Reader_37191705233.zip`, 8,750,880 bytes
- Artifact SHA256: `108536813a6d2e3da2f3801917f082acad2e8271b9a27ef2b2dcf66c7a3b8a85`
- Build: 125 files, 6,620,292 bytes
- Manifest SHA256: `05dd86f9c5face0b31a242f27b5cc25174249cc10d41f2d5bd535575c3c06e1f`
- Pinned ReactFlux: `534eeb97723ac11025de4ec1ac56335072e3be52`
- pnpm lock SHA256: `39c940c62fb66b77e524d30ae7b1dfcd1fb97ac73ceb95722757ed36e79b606b`

After materializing those existing reviewed inputs locally, run:

```sh
timeout --signal=TERM --kill-after=10s 600s "$READER_DEV_PYTHON" -B tests/reader_loading_performance.py \
  --build "$READER_REVIEWED_BUILD" \
  --manifest "$READER_BUILD_MANIFEST" \
  --manifest-sha 05dd86f9c5face0b31a242f27b5cc25174249cc10d41f2d5bd535575c3c06e1f \
  --artifact-zip "$READER_ORIGINAL_CI_ZIP" \
  --artifact-sha 108536813a6d2e3da2f3801917f082acad2e8271b9a27ef2b2dcf66c7a3b8a85 \
  --source-tree 65355bc7b2849809c83121e90e953429ba81ffde \
  --phase baseline --input keyboard --output-parent "$READER_PERF_OUTPUT_PARENT"
```

Use a second fresh invocation with `--input touch` for touch emulation. Add
`--weak-network` in separate runs for fixed 150 ms latency and 2 Mbit/s download,
1 Mbit/s upload. Baseline and candidate must use exactly matching conditions.
No mouse hover dwell is performed. Keyboard activation and touch activation
retain a control that cannot depend on a hover-triggered article fetch.
Touch emulation is not a physical phone test.

## HTTP and process boundary

The synthetic server binds only 127.0.0.1 on an ephemeral port. It also acts as
the launched browser's rejecting HTTP proxy: it serves only its own exact origin,
never forwards requests, rejects other origins and CONNECT, and accepts no
WebSocket upgrade. Chromium's implicit loopback bypass is removed. A separate
loopback sentinel receives one parent positive control; the browser must fail
to reach that sentinel and must visibly hit the rejecting proxy. Unknown HTTP,
other-loopback and HTTPS cases run before measurement. Failure prevents a pass.

Pages receive a strict same-origin CSP. Service workers and downloads are
disabled. The browser gets an explicit environment allowlist and a new private
HOME/cache/config/temp area; real credentials, proxies and other parent variables
are not passed to the browser. The API accepts only a fixed synthetic token.
It serves exactly bounded synthetic routes and returns 501 for unsupported API
paths, 405 for unsupported mutations, and 400 for invalid input. Version 2.3.3
here is openly synthetic; this fixture does not validate real Miniflux admission.

This is a controlled browser HTTP boundary, not an operating-system network
sandbox for arbitrary programs. No OS networking, permissions, user HOME,
production configuration or persistent browser profile is changed. Browser
managed transient scratch is separate from the retained result files.

The fixture uses 72 synthetic read entries in pages of 24 and six locally
generated PNGs, with explicit dimensions and deterministic content. Fixed delays
are 150 ms per list/image response and 200 ms per detail response. It stores no
image cache on disk and has a 1,200-request/600-second run budget. Unknown resource
paths never fall through to a successful SPA document. ZIP/manifest/build input
sizes are bounded, symlink and special-file input is refused, and file content is
read through bound directory descriptors without following links.

## Five pairs and the cache distinction

Each phase runs five complete pairs. Each pair starts a new isolated context:

1. Load 24 recommended cards and record initial-list readiness
2. Activate article 1 without hover; record click to meaningful, non-busy body
3. Scroll through six images; record decode/visibility, source dimensions,
   request timing, bytes and cache events
4. Close and reopen the same article in the same context, then repeat image scroll
5. Trigger the next two pages, immediately scroll to the old list bottom, and
   record append timing/bottom waiting and the exact 0/24/48 offset sequence

“Cold” means a new browser context. It does not mean a cold OS page cache,
restarted production service or uncached external CDN. “Warm” means same-context
reopen, with no cache clear and no cache-busting URL. APIs remain `no-store`, so
warm article data can legitimately issue another HTTP request. The tool records
that fact separately from cached JavaScript and images.

No `context.route`, `page.route`, HAR routing or fetch interception is used:
[Playwright routing disables HTTP cache](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route).
Image warmth requires both zero second-phase image HTTP GETs at the server and
separate proof for each of the six exact image URLs. Each must have one Image
request ID started inside the warm window, a successful PNG response, a completed
loading event before decoded visibility, and that same ID's memory/disk cache
evidence. Other resources, cold requests delivered late, duplicate IDs, incomplete
requests and service-worker responses cannot count. A bounded two-second event
drain reads the live event list; if a browser supplies no image cache events,
the measurement fails instead of inferring a hit from unrelated JavaScript.
Server conditional-GET unit tests alone are not browser-cache evidence. CDP
request wall time and monotonic response timing are joined by exact request ID;
missing events stay missing, never zero-filled.
The validated warm proof directly writes the published image timings. There is
no second pathname-based join. A same-path request with another query cannot
replace the verified ID or improve its measured duration. Optional cold timing
also requires one exact fixture URL, otherwise it remains explicitly unavailable.
Request-to-visible includes deliberate scrolling to that image; download and
scroll-to-visible durations are retained separately.

The server emits real HTTP cache headers for generated images and hashed assets.
It does not model the production gateway's buffering, auth fan-out, gzip, media
proxy, image origin, server cache or compression latency. Those remain separate
same-sample production measurements performed by the authorized operator.

Run the same five pairs on the reviewed candidate build, changing the explicitly
reviewed artifact/manifest hashes and expected source tree, and using
`--phase candidate`. Then compare only two successful runs:

```sh
timeout 30s "$READER_DEV_PYTHON" -B tests/compare_reader_loading.py \
  --before "$READER_BASELINE_RESULT" --after "$READER_CANDIDATE_RESULT" \
  --output "$READER_PRIVATE_COMPARISON_DIRECTORY/comparison.json"
```

The output directory must be a new or existing owner-private 0700 evidence
directory, and comparison.json must not exist. The comparison refuses partial,
failed, NOT_RUN, differently configured or non-five-pair inputs. Every side must
have exact integer IDs 1 through 5 in order. Required click/image/list durations
must be finite nonnegative numbers; bool, strings, NaN, Infinity and negative
values are rejected. Missing optional network durations remain null with no
calculated gain. It emits each paired delta; median/min/max are retained by the
runner. Five pairs do not justify
p95/p99 claims, and there is no invented production improvement threshold.

## Failure retention and current status

Each pair writes an exclusive result file, including partial HTTP/CDP evidence
on failure. The first failing pair stops the run; no retry replaces it with a
passing sample. Screenshot failure is recorded separately from the original
failure. The final status file and every prior invocation are retained. Source,
artifact, dependencies and compiled build are never edited by the measurement.
There are no explicit deletion or cache-purge calls. A missing pinned browser
returns NOT_RUN with exit 1; a launch/environment rejection is FAILED, also exit 1.
Neither result is eligible for before/after comparison.

The focused contracts are executable with existing dependencies:

```sh
timeout 90s "$READER_DEV_PYTHON" -B tests/test_reader_loading_performance.py
```

They retain their synthetic test directories and exercise real loopback HTTP,
identity/path/input limits, conditional responses, wrong-origin/CONNECT rejection,
private evidence retention, explicit browser environment and comparison refusal.
They do not launch Chromium or claim a real browser boundary/cache pass.

On the author cloud environment, the actual baseline admission verified all 125
build files and original CI identity, then found the pinned full Chromium absent.
Browser five-pair, cache, weak-network, touch and before/after results are therefore
NOT_RUN. No new browser was downloaded and no system Chromium fallback was used.
The earlier failed invocation that selected the global interpreter by resolving
the venv symlink is retained as a version-guard failure, separately from the
correct-venv missing-browser result. Browser behavior and selectors still require
the first authorized local/hosted execution. This candidate is not automatically
added to Reader regression CI and does not authorize a production run.

The first frozen measurement commit `ae6c5ff` was independently blocked: an
unrelated cached JavaScript response could satisfy the image cache gate, and
duplicate pair IDs/nonfinite durations were accepted by the comparator. Its
original source and independent counterexamples are retained. The follow-up
changes only those evidence gates and adds synthetic positive/negative controls;
it does not convert missing browser or production measurements into passing data.
The next retained commit `1331a8b` fixed admission but still allowed its later
pathname join to select another query's timing. The final follow-up removes that
warm re-selection and publishes timings directly from each verified proof.
