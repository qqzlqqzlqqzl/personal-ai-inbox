# Explicit sandbox entry for new Reader suites

New suites request `Harness(name, sandboxed=True, ...)` and check that the named
parameter exists before constructing a Harness. Omitting the parameter retains
the historical harness behavior; this change does not claim that old suites have
already run sandboxed. The deferred-panel suite is the intended initial caller.

The opt-in preflight runs before the HTTP server or browser starts. It requires
Linux, Python 3.12.14, Playwright 1.63.0 and both exact Chromium 1243 descriptors.
The full Chromium executable must live in the pinned revision directory and match
SHA256 `8c599d43aec53f2460a31ae2f4af6bd863f8258b34ff519564bc5d4726bfaa1e`.
There is no executable override, no fallback launch, no `--no-sandbox`, and no OS
security setting change. `chromium_sandbox=True` is explicit. Actual browser
version must equal 153.0.8010.12 after launch.

The same approved private short-TMPDIR and seven-variable browser environment
helper used by the performance runner creates fresh owned directories. It does
not alter a user's HOME/profile/permissions. The browser's proxy is only the
actual loopback fixture origin; other absolute origins and Host values, including
HEAD and CONNECT, receive refusal. Browser HTTP routing still rejects other
origins. Service workers and downloads cannot be enabled through context options.
Only viewport/locale/mobile/touch/pixel-ratio/theme/motion presentation options and
the fixed blocked-worker/no-download values are admitted. Caller-supplied proxy,
CSP bypass, TLS-ignore, credentials, storage state, HTTP headers, permissions,
capture directories and unknown context options are rejected before any server
or browser is created; they cannot override the admitted launch proxy.
The owned environment directories and launch-failure receipts are retained outside
suite uploads; successful admission metadata is included in the suite result.
Ordinary Playwright launch creates its separate temporary browser user-data
directory in the driver process's temporary directory and manages its lifecycle.
This helper does not claim that profile is retained or located in its environment
root, and does not switch to persistent contexts or alter Playwright cleanup.
The pinned implementation is Playwright v1.63.0
`packages/playwright-core/src/server/browserType.ts`, lines 150–174.

The pure contracts cover exact/malformed descriptors, foreign origins, executable
bytes/version errors, denied sandbox launch with no retry, early preflight refusal,
and real synthetic loopback proxy requests. They do not establish an actual
Chromium sandbox launch in this cloud environment. Ubuntu 24.04 can still reject
unprivileged Chromium sandbox setup; that must remain a visible hosted failure.
The successful Ubuntu 22.04 performance baseline is not approval for this new
entry or another runner. Any runner change belongs in a separately reviewed CI
proposal with all existing browser contracts retained.
