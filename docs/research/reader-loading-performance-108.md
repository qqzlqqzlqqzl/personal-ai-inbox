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

This implementation supports Linux only. Its bound directory descriptors,
O_NOFOLLOW and UID/mode checks are not a native Windows implementation; on Windows
or macOS the entry returns NOT_RUN/exit 1 before creating output or launching a
browser. Do not remove those guards to run it in a Windows focus-probe environment.
An existing reviewed Linux/Hosted Ubuntu environment with the matching browser can
run the command below. Windows Chromium 1200/1234/1243 caches are not Linux browser
inputs, and no browser version/platform fallback is supported.

Chromium is launched with `chromium_sandbox=True` explicitly. There is no
`--no-sandbox` argument or retry with weaker settings. If the host cannot support
that launch, preserve the failure and stop; do not change OS security controls,
permissions, the user's profile or HOME. Browser HOME/cache/config are new private
directories owned by this run, never permission changes to existing directories.

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
4. Close and reopen the same article in the same page, then repeat image scroll;
   report decoded visibility and user latency without claiming an HTTP cache hit
5. Trigger the next two pages, immediately scroll to the old list bottom, and
   record append timing/bottom waiting and the exact 0/24/48 offset sequence
6. Close that page, create a new page in the **same browser context**, and record
   its independent bootstrap and article activation costs. Its six images must
   pass the original strict HTTP-cache request/completion proof

“Cold” means a new browser context. It does not mean a cold OS page cache,
restarted production service or uncached external CDN. The existing
`warm_click_to_body_ms` and `warm_images` describe the same-page user reopen.
That page may reuse decoded image resources without issuing a new CDP network
request; absence of HTTP traffic alone is not labeled a cache hit. The separate
`http_cache_page` has a different target ID, the same browser-context ID, its own
network event window, and independently reported bootstrap/click/image costs.
Its traffic does not enter the original page's pagination or reopen numbers.
Neither phase clears caches or changes image URLs. APIs remain `no-store`, so
article data can legitimately issue another HTTP request in either phase.

No `context.route`, `page.route`, HAR routing or fetch interception is used:
[Playwright routing disables HTTP cache](https://playwright.dev/python/docs/api/class-browsercontext#browser-context-route).
The new-page HTTP-cache audit requires zero image HTTP GETs at the server and
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
The validated HTTP-cache proof directly writes that new page's image timings. There is
no second pathname-based join. A same-path request with another query cannot
replace the verified ID or improve its measured duration. Optional cold timing
also requires one exact fixture URL, otherwise it remains explicitly unavailable.
Request-to-visible includes deliberate scrolling to that image; download and
scroll-to-visible durations are retained separately.

Both runs and every pair must declare
`same-page-reopen-and-new-page-http-cache-v2`. The comparator refuses old warm
semantics, replays the exact new-page cache proof, and verifies the published
metrics still belong to that proof. It reports the new-page costs separately.
The same-page observations retain decoded visibility, time-window and zero-image-
HTTP checks, but cannot publish an unbound cache request ID or network timing.

This distinction follows the retained J first-pair trace: touch decoded all six
images during a same-page reopen but emitted no new image request events. That
failed the original strict cache gate. The failure is retained; the v2 contract
requires a separate new-page audit rather than turning missing events into hits.
The v2 browser behavior is **NOT RUN** until its exact candidate is run on Hosted.
The original five pairs, 24-entry pages, five-second list readiness, CSP, sandbox,
egress boundary, request budget and every strict six-image cache negative control
remain mandatory. Synchronous DOM observations replace page-side eval polling for
body/image readiness, retaining their original single 15-second deadlines.

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

## Hosted baseline job and full regression integration

`.github/workflows/reader-loading-performance.yml` is an executable **baseline**
measurement candidate for the publisher's reviewed PR. It starts three independent
Ubuntu 22.04 jobs (`keyboard`, `touch`, `weak-network`); each runs five fresh-context
pairs with cold, same-page-reopen and separate shared-context HTTP-cache stages.
Weak network uses the existing keyboard scenario
with 150 ms latency, 250000 bytes/s download and 125000 bytes/s upload. It does not
compare a product fix or substitute for the full Reader regression workflow.

The workflow runs automatically on a PR changing its listed measurement paths.
The publisher can also dispatch `reader-loading-performance.yml` at the reviewed
branch once GitHub exposes that workflow for dispatch. There are no arbitrary URL,
artifact, command, browser executable, toolchain, or token inputs. Do not run this
Linux entry through a native Windows Python/Chromium installation.

Only this new job has the specifically authorized `actions: read` in addition to
`contents: read`. Its download step uses the repository's existing short-lived
`github.token` as `GH_TOKEN`, scoped to that step. It calls only:

```
gh api --hostname github.com --method GET -H 'X-GitHub-Api-Version: 2022-11-28' \
  repos/qqzlqqzlqqzl/personal-ai-inbox/actions/artifacts/11299074112/zip
```

The helper bounds this read to 90 seconds and exactly 8,750,880 bytes, then requires
SHA256 `108536813a6d2e3da2f3801917f082acad2e8271b9a27ef2b2dcf66c7a3b8a85`.
It checks ZIP CRC, member/expanded-byte caps, unique paths, the original successful
879ff545 source identity and regular-file types. Only the 125 build members are
expanded, after their original-order JSON manifest matches SHA256
`05dd86f9c5face0b31a242f27b5cc25174249cc10d41f2d5bd535575c3c06e1f`.
Expired/unavailable artifacts, access denial and mismatched bytes fail the job;
there is no replacement artifact, rebuild, secret, or cross-repository fallback.

The existing setup-python action pin selects Python 3.12.14. The original
requirements lock is verified at SHA256
`106373e10b547e80b632c0d584cf5deb85bb5486d60303936f22e3f2aefdbe4f`, and every
installed package must match it, including Playwright 1.63.0 and Chromium's 1243 /
153.0.8010.12 descriptors. The unchanged measurement entry verifies the actual full
Chromium path/version and launches with its sandbox explicitly enabled. The new
workflow has no weaker launch, alternate executable, increased existing timeout,
or reused user profile.

Playwright's original `install-deps chromium` refreshes signed APT indexes. Font
prepare/install calls reuse the existing `ci_cjk_font.py` metadata, size, SHA256,
cache-admission and `--no-download` installation checks. A narrow process-local
adapter retains that helper's temporary download/install directories and bounds
each external font command to 120 seconds. It changes no package verification,
system permission or existing cache bytes. The full Reader workflow continues to
perform its existing actual Chinese glyph rendering acceptance.

Every job creates its own private directory under `RUNNER_TEMP`, before dependency
installation, and records the exact tested workflow HEAD/tree/src. Original input
archives and extracted builds stay in `inputs/`; driver HOME, raw gh errors and
retained font staging stay in `work/`. Fresh short browser temporary roots are
separate, private `/tmp/rl-*` directories, excluded from uploads. The upload path is only
the fresh `evidence/` directory. It contains safe source/toolchain/input receipts,
signed-index font identity, explicit setup/measurement status, synthetic measurement
stdout/stderr and an allowlisted copy of direct result/pair/boundary JSON files and
failure screenshots. It never uploads the baseline ZIP, frontend files, cookies,
browser cache/profile, arbitrary nested files, environment or raw gh redirect text.
Failed measurement partials remain available. Setup failure explicitly records
measurement `NOT_RUN`. GitHub job cancellation or loss of the runner can prevent
finalization/upload; a missing artifact is not a pass.

Artifact names are
`reader-loading-baseline-<run_id>-<run_attempt>-<keyboard|touch|weak-network>` with
seven-day retention. `source.json` identifies the current workflow commit while
`baseline-input.json` identifies the older exact build being measured. Do not
attribute old 879 CI assertions to the current workflow commit or combine the
three five-pair samples into a p95/p99 or production performance claim.

The full `.github/workflows/reader-regression.yml` separately adds mandatory
`query_result_ownership_browser.py` and `reading_focus_browser.py` steps. Both use
`AI_NEWS_TEST_BUILD=runtime/browser-build` and the existing 600-second limit. Their
files are required by `ci_reader_contract.py`; missing entries fail instead of
skipping. Evidence preparation retains previous `runtime/query-result-ownership/`
and `runtime/reading-focus-*/` output before any dependency step, and the final
guarded upload includes the new synthetic result directories. All earlier Reader
steps, action pins, permissions and required suites remain present.

These two browser entry files belong to the separately reviewed UX candidate.
This CI-only branch intentionally does not copy or merge that product branch.
The publisher must combine the reviewed source commits and this CI candidate,
then verify the resulting exact HEAD/tree and run the full workflow. This branch
alone is not a complete product acceptance candidate and its full identity gate
will reject the absent two inputs. New Hosted browser measurements and the final
combined-product acceptance remain **NOT RUN** until real runs produce receipts.

The first Hosted run `37202844449` at exact `154862c8` used Ubuntu 24.04. All three
jobs downloaded and verified the fixed build, Chromium and CJK font successfully,
but Chromium reported `No usable sandbox!` during launch. Each retained result has
zero pairs, zero fixture requests and no completed browser boundary check. These
are environment failures, not loading measurements. The log's user-namespace /
AppArmor suggestion does not establish the runner's actual kernel policy values.

Only this measurement job now selects Ubuntu 22.04; its exact OS admission guard
rejects other releases. The ordinary Reader and paired jobs are unchanged. Before
comparing a future candidate, run both baseline and candidate on this same 22.04
environment and verify both toolchain receipts, including the fresh signed-index
font identity. Do not compare the failed 24.04 attempts with 22.04 timing samples.
Sandboxing, browser/toolchain pins, exact artifact/manifest checks, HTTP-cache proof,
external-origin rejection and timeouts remain unchanged. No AppArmor/sysctl policy,
setuid binary, permission, or sandbox-disable workaround is introduced.

Official support was checked in [Playwright's Python system requirements](https://playwright.dev/python/docs/intro#system-requirements),
the exact [v1.63.0 platform mapping](https://github.com/microsoft/playwright/blob/v1.63.0/packages/utils/hostPlatform.ts),
[browser descriptors](https://github.com/microsoft/playwright/blob/v1.63.0/packages/playwright-core/browsers.json),
and [download registry](https://github.com/microsoft/playwright/blob/v1.63.0/packages/playwright-core/src/server/registry/index.ts).
The tag marks Ubuntu 22.04 as supported and maps its x64 Chromium to the same
Chrome for Testing Linux archive as 24.04, with revision 1243 / version 153.0.8010.12.
Support documentation is not evidence that this particular Hosted launch or the
five-pair matrix has passed; new actual receipts remain required.

This runner choice is temporary. The official runner image release
[`ubuntu22/20260927.309`](https://github.com/actions/runner-images/releases/tag/ubuntu22%2F20260927.309)
documents image 20260927.309.1, but `ubuntu-22.04` is a moving image label, not a
pin to that image revision. GitHub's [retirement notice](https://github.com/actions/runner-images/issues/14254)
states deprecation began September 17, 2026 and retirement is April 17, 2027.
Record each actual runner image version from job logs. This workaround is not a
long-term availability guarantee; any later runner migration requires a new
reviewed baseline/candidate environment match with sandboxing retained.

The next retained Ubuntu 22.04 run `37205965113`, exact `110255c`, reached another
launch failure before any pair or fixture request. All three original archives
report a Chromium `SingletonSocket` pathname of 138 filesystem bytes, exceeding
Linux's 107-byte pathname budget plus the terminating NUL in `sun_path[108]`.
This is a separate path-length failure; it is not proof that a browser boundary
or performance measurement has passed, nor a new AppArmor diagnosis.

`browser_env` now creates a fresh owner-private 0700 short temporary root under
the real `/tmp`, without following a directory symlink, reading ambient TMPDIR,
reusing a profile, or changing existing ownership/permissions. Both the driver
and browser receive their own fresh root through the existing environment
allowlist. HOME/cache/config still use new private directories under the owned
output; output parents over 400 bytes, including non-ASCII segments, therefore
do not lengthen the IPC path. The outer temporary roots are retained; the tools
retain their normal ephemeral socket/profile lifecycle. Profiles are never uploaded.

Before and after creation, the temporary path's filesystem byte length plus a
conservative 64-byte Chromium socket suffix allowance must fit 107 bytes. Longer
temporary parents are rejected before launch, including paths whose character
count fits but encoded byte count does not. A separate local AF_UNIX contract
binds a 107-byte socket path and rejects 108 bytes, keeping the closed fixture node.
These controls follow the Linux [UNIX-domain socket pathname contract](https://man7.org/linux/man-pages/man7/unix.7.html).
The authoring cloud rejected actual `socket(AF_UNIX, ...)` with EPERM. That failed
attempt is retained, the physical bind test remains mandatory in the repository,
and its local result is BLOCKED rather than passed or silently skipped. The other
80 measurement contracts and 37 Hosted-adapter contracts passed separately.
The fix changes no sandbox, network, cache-observation, browser, source, binary or
fixture pin. The new exact Hosted head still needs an actual three-mode run.
