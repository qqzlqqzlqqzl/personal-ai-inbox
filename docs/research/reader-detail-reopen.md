# Reader detail ownership during rapid reopening

Refs #108, #110, #111. This corrects a product lifecycle defect; it does not add a
detail cache or claim a network speed improvement.

The fixed 879/9a frontend closes by clearing `activeContent` before navigation
commits. Opening a list row writes its DTO before navigation. If both actions
finish while the committed detail route still names the same entry, the old
effect sees no route dependency change. A deferred list DTO can therefore remain
visible without a detail request. The observed shell can include the title, AI
summary and notes while original paragraphs and images are absent.

The original weak-network pair 5 had exactly those visible/data-request symptoms,
but its trace did not capture internal route commits. The isolated real React
component counterexample demonstrates a sufficient ordering; it does not prove
the precise scheduling of that recorded browser run. The 879 and 9a authoring
trees under src, patches and frontend-review are identical. Their prepared
Content and ContentContext also match the 7554 candidate base byte for byte.

The new hook responds to a newly active deferred DTO even when the route ID is
unchanged. Each request retains its route/source, data-session revision, in-memory
authentication identity, synchronous selection generation and active DTO object.
Completion may publish only while all remain current. Closing does not start a
replacement request. A route's initial deep link may legitimately load with no
active DTO; the exact pending owner distinguishes that state from a user close.
An already complete same-ID DTO does not cause a duplicate detail request.

The original ContentContext navigation and focus body, reading geometry and all
performance driver/ready thresholds remain unchanged. The close handler now calls
a single explicit invalidation function before its original body, so closing an
initial null-active deep link still cancels the pending request. Authentication keys stay
in memory and are neither persisted nor included in evidence.

`tests/test_reader_detail_reopen.mjs` compiles actual prepared Content,
ContentContext and the new hook against real React/nanostores. Its controlled
router-commit seam and leaf visual/API fixtures test 19 orders: rapid and batched
same-entry reopening, base-first reopening, another entry, complete DTOs, initial
deep links, closing a pending request, late success/error, source/session/auth
changes, StrictMode, wrong-ID responses, an explicitly closed null-active deep
link, an abandoned concurrent route render, and server render/client hydration. These are component tests, not a real
browser execution. The same test against the fixed old prepared reader fails the
first reopen assertion. `tests/test_reader_detail_install.py` checks exact native
and earlier reviewed loading blocks, repeatability, unknown helper/loading/wiring
rejection and a missing source helper.

The installer uses the owned helper and exact known loading blocks; unknown
blocks still fail. The other patch stages and guards remain. This modifies src,
so integration requires a new independent whole-src/CODE review and complete
Reader CI. Real built-browser reading/focus controls and same-network/data/cache
A/B measurements must follow. The fixed old benchmark artifact must be retained.

## Revision after independent review

The first candidate a8c6644 retained two independent failures. First, an initial
null-active deep-link request could publish after an explicit close before the
base route committed: null-to-null did not emit an active DTO transition.
SearchAndSortBar can reach that real close handler. The new explicit close intent
counter invalidates even this empty selection and clears only the current
loading display; it does not request another article.

Second, writing the current route ref during render let a suspended, discarded
route-2 render cancel a still-committed route-1 request. Route identity is now
updated in a layout effect. The independent real React startTransition/Suspense
counterexample sees only route 1 commit, then resolves its request and abandons
the speculative route 2. It now retains the route-1 body and clears loading.
The server render starts no request; client hydration starts one valid deep-link
request. StrictMode replay remains explicitly tested.

The independent review's six lifecycle controls and concurrent-render control
were preserved byte for byte. Their two a8 failures and the new passing results
are separate evidence; the old candidate is retained. The new prepared source
and build have a separate directory and per-file hashes to avoid mixing an old
HEAD with newer generated files.
