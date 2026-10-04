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

The original ContentContext, focus restoration, reading geometry and all
performance driver/ready thresholds remain unchanged. Authentication keys stay
in memory and are neither persisted nor included in evidence.

`tests/test_reader_detail_reopen.mjs` compiles actual prepared Content,
ContentContext and the new hook against real React/nanostores. Its controlled
router-commit seam and leaf visual/API fixtures test 16 orders: rapid and batched
same-entry reopening, base-first reopening, another entry, complete DTOs, initial
deep links, closing a pending request, late success/error, source/session/auth
changes, StrictMode and wrong-ID responses. These are component tests, not a real
browser execution. The same test against the fixed old prepared reader fails the
first reopen assertion. `tests/test_reader_detail_install.py` checks exact native
and earlier reviewed loading blocks, repeatability, unknown helper/loading/wiring
rejection and a missing source helper.

The installer uses the owned helper and exact known loading blocks; unknown
blocks still fail. The other patch stages and guards remain. This modifies src,
so integration requires a new independent whole-src/CODE review and complete
Reader CI. Real built-browser reading/focus controls and same-network/data/cache
A/B measurements must follow. The fixed old benchmark artifact must be retained.
