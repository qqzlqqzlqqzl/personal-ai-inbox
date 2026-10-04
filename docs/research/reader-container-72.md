# Reader #72 official container experiment

This is an unaccepted CI experiment. Issue #72 remains open until measured
ordinary-case performance and the complete regression satisfy review. PR #75
only addressed CJK font archives; the rejected PR #76 archive-cache experiment
and all of its evidence remain intact.

The source baseline is release `fa7196b7aca8f9c4f7256c20dd24eaeaf62e3140`, whose
tree `163e0949d651377e2c2182ad6c3967d4f45414a9` equals the tested PR #99 head
`bd5b83fbfb2c7030335d989714fd776e3400b382`. Runtime `src` remains unchanged.

## Official inputs

- [Playwright Python Docker guidance](https://playwright.dev/python/docs/docker)
  and [CI guidance](https://playwright.dev/python/docs/ci) describe preinstalled
  browser and OS dependencies; the package version must match the image.
- Lock: Playwright `1.63.0`; full Chromium and headless shell revision `1243`,
  version `153.0.8010.12`. Python `3.12.14`, Node `24.21.0`, pnpm `11.21.0` and
  the existing ReactFlux/action/lock pins remain exact.
- Official image tag: `mcr.microsoft.com/playwright/python:v1.63.0-noble`.
- Verified index: `sha256:72bd171a9ffc2b4b59532aaa6210e21014d07093120dc25528870c0b840da1f0`.
- Workflow pins the Linux/amd64 manifest:
  `sha256:96b39581c89131729a7ecb8d532314af54c7f9bcc7a61fe15c7a9e77602acf59`.
- Config: `sha256:59fe35832a79d1324e778b737760b2afc11cffcbaeba325012f96cabae832a75`.
- The official MCR response body hashes match each digest. Compressed layer
  payloads total **1,013,933,919 bytes**. This is manifest metadata, not measured
  network traffic: a runner can already have shared base layers.
- [Pinned upstream Dockerfile](https://github.com/microsoft/playwright-python/blob/8cb967b3e4199bef5ce38e1cf34df1bf79cb8a8d/utils/docker/Dockerfile.noble).

## Preserved gates and comparison

The normal Reader workflow remains the native baseline. A separate container
workflow runs the same complete Python, Node, build and browser commands,
including native 200% zoom and real CJK glyph rendering, on the same commit.
Only browser/OS installation is replaced with immutable image initialization and
an executable/version/library/marker preflight. No override browser path or
fallback installation is allowed. No privileged mode, extra capability, host IPC,
new secret, cache deletion or mirror rewrite is used.

Fresh signed APT indices are still required. The existing CJK candidate metadata,
size/SHA validation, descriptor copy, exact cache key and no-download install are
unchanged. When already root inside the official image, the helper omits the
unavailable `sudo` prefix from the same validated installation arguments.
Non-root execution is unchanged. All old font safety tests remain intact.

The image-cost job records whether the image was present before pulling, the
first pull, a second pull on the same runner, and exact image/config identity.
It retains images and logs. A second pull is a warm-pull measurement only; a
workflow rerun on a fresh hosted VM is not called a warm-image job. Full job
durations include initialization/pull, dependency steps, tests and artifact work.
Cache hits, runner image versions and partial failures must be reported.

## Measurements

Historical context only: exact tested `bd5b83f` run `37177038471`, job
`111361710250`, completed in 416 seconds: OS dependencies 14 seconds, Chromium
and shell 13 seconds, CJK restore/install 4 seconds. The candidate's final same-
commit native/container comparison and repeated full runs are still pending.

No container performance or browser result has been asserted from local static
tests. The cloud workspace has no Docker daemon; real container execution is a
hosted gate. Keep failures and reject ordinary-case regressions rather than
raising timeouts or removing verification.

First hosted trial (`e990d20`, run `37183957265`) failed before evidence
preparation: the official container's checkout mount had a different owner, and
checkout v7's temporary Git HOME did not make its safe-directory binding visible
to subsequent shell steps. Git rejected the repository; no functional/browser
tests ran. The upload guard correctly withheld stale regression artifacts.

The follow-up binds only that exact, canonical, non-symlink GITHUB_WORKSPACE to
Git for the remainder of the disposable job. It verifies the requested commit
with a one-command exact-directory option before exporting the same exception
through GITHUB_ENV. It rejects existing Git config overrides, different paths,
wildcards and incorrect commits. It changes no global config, file ownership or
permissions; the original HEAD/tree/source and evidence guards still execute.
A real Git negative control confirms another repository remains refused.

The independent image-pull job succeeded: the image was initially absent; first
pull 29.035926 seconds, same-runner second pull 0.296657 seconds. Container job
initialization took 31 seconds. These are setup observations, not full-suite
performance acceptance; the failure and original logs are retained.

Second hosted trial (`aa3544c`, run `37185380760`) passed the exact checkout
binding and fresh-evidence preparation, then failed in setup-python's unchanged
pip-cache step: spawning `/__t/Python/3.12.14/x64/bin/pip` returned ENOENT.
Image initialization took 52 seconds; functional tests still did not run. The
log alone does not establish whether the pip file or its shebang target was
missing. Its single-file preparation artifact is not regression evidence.

The next preflight reads the actual exact-version pip shebang in the hosted
container. Before any path change it runs the existing interpreter with an
isolated probe and that version's library directory, requiring CPython 3.12.14,
the exact executable and prefix. Only a proven original
`/opt/hostedtoolcache/Python/3.12.14/x64/bin/python*` reference can cause creation
of `/opt/hostedtoolcache -> /__t`. Unknown targets, conflicting existing paths
and incomplete distributions fail. Existing contents, owners, modes, package
cache behavior, the pinned setup-python action and all later identity checks are
unchanged. The receipt distinguishes a confirmed mismatch from cases where the
hypothesis was not established. A synthetic kernel-level shebang counterexample
and negative identity cases are retained; actual hosted confirmation is pending.

Third hosted trial (`cfa2539`, run `37187282911`) confirmed the cached pip's
missing absolute interpreter prefix. Its receipt observed CPython 3.12.14,
`/__t/Python/3.12.14/x64`, and the unchanged interpreter and pip hashes before
creating the exact mapping. Pip then started successfully, but the original
cache action rejected `/github/home/.cache/pip`: that host-mounted home was not
owned by the container's root user. Functional tests still did not run. Image
initialization took 42 seconds.

The next change sets only the container job's `PIP_CACHE_DIR` to
`/root/.cache/pip`. This uses the official image user's own home and preserves
pip's ownership validation plus the original setup-python cache restore/save.
It does not chown, chmod, delete or rewrite the host-mounted cache, and leaves the
native baseline unchanged. Any initial miss for the different cache path must
be counted as cold setup cost; cache keys are not churned to manufacture results.
