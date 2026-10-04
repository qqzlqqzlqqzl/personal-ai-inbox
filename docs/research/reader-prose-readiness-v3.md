# Reader original-prose DOM readiness, version 3

The fixed 879 baseline's second 2c930 weak-network run failed in pair 5: the
same-page reopen's old container predicate returned at 49.627ms, but image 1
never attached within the existing 15-second timeout. Its original PNG contained
article 001 metadata, AI summary and notes with no original prose/images. The
network record had only the earlier cold detail GET. That failed run and value
remain evidence; the value established only container text, not body readiness.

The reviewed app's `.article-body` also contains notes and a source footer.
Version 2's `innerText.length > 100` could therefore accept those children. The
new contract retains that predicate and records its measured value separately as
`container_ready_ms`. The reported click-to-body values now require the complete
synthetic original paragraph, an exact 001 heading, a nonbusy body, exactly one
actual article scroll root, computed visibility and positive intersection with
both the viewport and all clipping ancestors including that root. Notes, source
footer and AI summary descendants do not qualify. No paragraph is scrolled or
injected by this admission step. The original image sweep and cache guards still
run afterward, with their existing deadlines and six-image identities.

The contract is `fixture-prose-dom-and-separate-http-cache-v3`. Original v2
receipts cannot compare against v3. Both variants must run the same final driver
and profile again; no existing baseline numbers are relabeled. The old public
metric keys are retained for consumers, but `cold_body_observation`,
`warm_body_observation` and the new-page `body_observation` prove their precise
meaning. The comparator revalidates each proof and exact reported timing.

A bounded passive observer records DOM mutations, original trusted click/key
events, and explicit before-open/before-close/after-hidden boundaries using the
page's `performance.timeOrigin + performance.now()` clock. It stores URL/path,
article title, busy flag, exact matching/visible prose counts, clipping geometry
and image node/decode state. These are labeled DOM observations, not painted
frames; `painted` remains null. No history/navigation API is replaced and no URL
stability wait is added. The original fast close/reopen sequence can still expose
the current baseline's empty-body race. At most 512 samples per page are retained;
missing/truncated observations fail the candidate instead of silently passing.
The full action/mutation trace stays in each immutable pair JSON. Summary/result
JSON retains the precise container/prose admission snapshots without duplicating
all traces across the five pairs; the collector still preserves every pair file.

The actual 2c930 failure remains independent from a controlled React router-seam
reproduction performed by the component owner. This instrumentation changes no
product code or fixture article content and does not by itself repair that race.
It does not measure paint, CPU utilization, process RSS or production performance.
Local jsdom geometry tests are synthetic controls; real browser v3 remains NOT RUN
until an exact reviewed commit is published and all matrix artifacts are verified.
