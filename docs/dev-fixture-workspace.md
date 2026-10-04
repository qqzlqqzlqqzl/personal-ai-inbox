# Controlled Reader fixture workspace

`tests/dev_fixture_workspace.py` is an opt-in development tool wrapping the
unchanged `tests/review_reader_harness.py`. It has no production backend, model
provider, import, source activation, production credentials, or production
authentication bypass. It is not a release/real-device/PWA/24-hour acceptance gate.

## Inputs and start

Use the existing isolated Reader build workflow and the development Python
environment locked in `requirements.dev.lock.txt` (Playwright **1.63.0**, including
its matching Chromium). This tool does not install, download, or build anything.
Do not point it at production, private configuration, or a checkout's secrets.

Compute a fingerprint only after verifying the build's source/CI provenance:

```sh
timeout 15s /absolute/dev/venv/bin/python tests/dev_fixture_workspace.py fingerprint \
  --build /absolute/isolated/browser-build
timeout 45s /absolute/dev/venv/bin/python tests/dev_fixture_workspace.py start \
  --build /absolute/isolated/browser-build --build-sha256 REVIEWED_SHA256 \
  --parent /tmp --browser-cache /absolute/pinned/playwright-cache
```

`--browser-cache` is optional if matching Playwright browsers are otherwise
available in the isolated HOME. Parent environment variables, including
PLAYWRIGHT_BROWSERS_PATH, credentials, proxies, production roots and Python path,
are not inherited. There is no arbitrary browser executable override. The build
fingerprint commits to each regular file's path, mode, byte count, and SHA256.
A fingerprint is an integrity commitment, not proof of source approval.

The tool creates a new private `/tmp/reader-fixture-*` or
`/var/tmp/reader-fixture-*` directory and copies the admitted static build into it.
Root creation requires an existing non-symlink parent; it does not adopt a
production/config directory. A root's owner receipt binds its absolute path,
UID and workspace identifier. Only the same OS user may control it. This is not
a sandbox against a malicious process already running as that same user.

Start returns JSON containing the root, instance, generation, build/tool digest
and a live worker acknowledgment. It does not report success from a file or PID
alone. Startup has a bounded readiness wait; failures leave logs and receipts.

For a visible, interactive **controlled** browser, explicitly add:

```sh
--headed --display :0
```

This requires a usable local graphical display. Missing/nonlocal DISPLAY is
rejected; a browser launch failure is recorded. Headless mode is not a visible
manual browser. No Xauthority/credentials are copied, no remote debugging or
CDP endpoint is published, and no display/IPC restriction is bypassed.

## Across calls: status, reset, stop, restart

Use the exact root returned by start, not a URL:

```sh
timeout 25s /absolute/dev/venv/bin/python tests/dev_fixture_workspace.py status --root /tmp/reader-fixture-EXACT
timeout 25s /absolute/dev/venv/bin/python tests/dev_fixture_workspace.py reset  --root /tmp/reader-fixture-EXACT
timeout 25s /absolute/dev/venv/bin/python tests/dev_fixture_workspace.py stop   --root /tmp/reader-fixture-EXACT
timeout 45s /absolute/dev/venv/bin/python tests/dev_fixture_workspace.py start  --root /tmp/reader-fixture-EXACT \
  --browser-cache /absolute/pinned/playwright-cache
```

Restart can also use headed/display options. It reuses the originally pinned
build; passing new build arguments with `--root` is rejected. One advisory
exclusive worker lock prevents concurrent owners. Controls use private,
per-instance file mailboxes and unique response IDs, not an HTTP control API.
Unknown commands, malformed control JSON, root/instance mismatch, symlink or
special-file control paths are rejected. A timeout means no acknowledgment was
obtained: inspect receipts before retrying a mutation. A stale running receipt
never counts as a live process. A stopped/failed receipt is returned as not live.

Control JSON reads and every receipt/response publication open each parent
component with directory file descriptors and `O_NOFOLLOW`. Staging creation and
both ends of the final hardlink use the same bound directory descriptor. A
parent replacement during publication cannot redirect bytes elsewhere and is
rejected rather than acknowledged; any completed bytes remain in the original
directory as retained evidence. This also covers exception-path receipt writes.

Reset first records screenshot (or screenshot failure), page errors, denied
requests, fixture calls/writes, browser storage and session storage. Only after
the evidence file is safely published does it close the old browser/server.
It then verifies the build fingerprint again and starts a fresh generation,
including fresh auth, fixture data, drafts and browser context. If evidence
cannot be saved, reset does not discard the old context. If reopening fails,
old evidence stays and the failure is recorded. Stop follows the same
capture-before-close sequence. Previously produced files are never deleted by
this tool. JSON staging hardlinks, logs, commands, responses and generations are
retained intentionally. Browser-managed transient scratch is not read or used
as a control path. Do not paste real credentials or personal data into this
synthetic UI; evidence is private and may contain anything typed there.

A reset/stop capture failure is a failed **command**, while the worker remains
running with the same generation. Its receipt has `outcome: failed` and
`retained_context: true`; status reports `last_command_failure`. Restore the
evidence destination and retry. If the whole volume cannot accept even receipts
or responses, the caller times out, but that failure does not close the old
context. A later new-generation startup failure is still a worker failure;
it is not swallowed or mislabeled as retaining a live old context.

## Network and fixture boundary

The existing harness server binds only `127.0.0.1` on an ephemeral port. There is
no public bind or firewall/proxy configuration. Service workers and downloads
are disabled. All WebSockets are closed. The controlled Playwright context
aborts non-origin HTTP(S) requests and serves only exact supported `/mf` routes
from the existing in-memory synthetic fixture. This is browser-context request
interception, not an OS-level network sandbox for arbitrary executable code.

Supported methods/routes:

- GET `/mf/v1/me`, `/mf/version`, `/mf/v1/version`
- GET/POST `/mf/v1/categories`; GET `/mf/v1/feeds`, `/mf/v1/feeds/counters`
- GET `/mf/v1/entries` and `/mf/v1/entries/<positive-id>` (missing entry: 404)
- GET/PUT `/mf/v1/ai/settings`; GET `/mf/v1/ai/status`, `/mf/v1/ai/catalog`, `/mf/v1/ai/x/roster`
- POST `/mf/v1/ai/subscribe`; GET/PUT `/mf/v1/ai/notes/<positive-id>`

Unsupported fixture APIs return an explicit 501 error; unsupported methods
return 405. Invalid supported fixture payloads fail 400. These are local
in-memory operations only. The inherited fixture has empty entries by default;
unsupported product features visibly fail instead of silently returning `{}`.
This tool intentionally does not expand the fixture API catalogue.

Opening the bare localhost URL in another browser is **not equivalent** to this
controlled context: that browser has neither synthetic auth nor fixture route
interception. The static server rejects raw `/mf`, `/v1`, and `/api` calls. Use
the launched headed context for manual interaction; never work around production
authentication or expose this port publicly.

## Focused tests

```sh
timeout 60s /absolute/dev/venv/bin/python tests/test_dev_fixture_workspace.py
```

These tests retain their `/tmp/reader-fixture-contract-*` roots and print their
paths. They cover cross-process lifecycle, ownership, replay/instance boundaries,
retention ordering, environment stripping, network route rejection, and the
real harness adapter with a fake browser plus a real loopback static server.
They do not claim that Chromium launched. Report actual browser/headed tests and
hosted CI separately, on the frozen candidate.
