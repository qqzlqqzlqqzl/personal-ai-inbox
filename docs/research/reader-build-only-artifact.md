# Small immutable Reader build input

The F44 ordinary producer (run 37244254918, artifact 11318463498) succeeded.
Its complete evidence ZIP is 33,948,270 bytes, SHA256
`9f6952c97c0fdbb4c3e6ddd646e5715a2738d71d7ba491e312979321fc3853f4`.
That ZIP exceeds the unchanged A/B input cap of 32 MiB by 393,838 bytes.
It remains the complete original evidence artifact. This change neither edits
it nor describes a locally cut-down ZIP as an official producer artifact.

Immediately after the existing validated build succeeds, the same Reader job
uploads a separate immutable `reader-build-RUN_ID-RUN_ATTEMPT` artifact using
the existing pinned upload action and existing permissions. Only these paths
are included:

- `runtime/browser-build/`, the complete generated build, with its actual count
- `artifacts/ci-reader-identity.json`, the exact HEAD/tree/src and pinned tools
- `artifacts/ci-tested-source.json`, the prepared checkout and run/attempt

All three paths already participate in the checkout-first retention preparation.
The new step requires successful preceding steps and successful preparation;
an old build cannot be uploaded after a failed install or build. An empty upload
fails visibly; the unchanged consumer rejects a missing identity or incomplete
build even if another selected upload path exists. The original complete evidence step and all existing
checks remain byte-for-byte unchanged. No additional token, secret or permission
is introduced. A later browser failure does not retroactively make the build
artifact disappear; the producer's terminal full-regression outcome must still
be verified independently before selecting it for any comparison or release.

The unchanged A/B consumer verifies the exact artifact ID/size/SHA, same-repo
producer HEAD/run/attempt/conclusion, ZIP CRC and budget, identity JSON, complete
build member set and every member SHA. Its manifest is derived from the actual
immutable archive, not a predicted list or a hard-coded 125 files. When a new
official artifact exists, retrieve it once, preserve its original bytes, compute
and independently review that complete manifest, then bind those exact values
in the two-input table. The candidate head is the new producer's actual commit;
do not relabel it F44 merely because its product files are unchanged.

The new artifact has not yet been produced by Hosted CI at this source-review
stage. Current A/B inputs remain the fixed 879 and 7554 artifacts. The 32 MiB
input cap, 64 MiB expanded archive cap, file-count cap, strict source checks,
three network/input modes, five AB/BA groups, total deadline, browser sandbox,
CSP and exact six-image cache evidence are unchanged. No speedup is claimed.
