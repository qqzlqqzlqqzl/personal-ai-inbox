# Rolling and on-demand cached bilingual Reader articles

Current user scope (2026-10-09, Issue194):
- Automatically discover global >=8 English recommendations every five minutes; no browser visit is required. Newly analyzed recommendations have priority, followed by the historical backlog in newest-published order. Future-dated, hidden, non-English and ineligible articles stay excluded.
- Each discovery pass retains the existing 24-body / 30-second bound and keyset continuation. Completed translations are reused, and queued/partial work continues under the shared daily budget. This policy does not imply that the historical backlog is already complete.
- A real article-open POST also requests work; ordinary detail/status GET and list/body/image prefetch stay read-only.
- Reuse the existing bilingual worker and queue. BILINGUAL_ROLLING_ENABLED defaults false for rollout, enabled in the existing production private environment after deployment.
- Automatic and article-open requests share bilingual_usage and its atomic reserved-or-actual token ceiling. Production ceiling is 1,000,000 input+output tokens and 1000 requests per UTC day, resetting at 08:00 Asia/Shanghai. This is a maximum, not a consumption target.
- At the ceiling, preserve pending/partial translations and resume under the next day's budget. Cached reads are free.
- Analysis must be current/done and score >=8, with current ownership, visibility, recommendation quality and English-language checks. Already translated exact content is not paid for again.
- In bilingual mode, render each original English segment first and its Chinese translation immediately after it. Chinese-only and original-only modes retain their behavior.
- Keep VERSION, PROMPT and source-hash identity: existing successful and partial translations stay reusable. Image-address-only rotation retains the existing completed-text reuse.
- Use the existing private New API key and gpt-4o-mini. No new service, environment, database or image-cache allocation.
- Maintain the existing top-right language/layout/focus controls, original markup, signed images and shared 1GiB image-cache cap.
- Validation uses relevant offline mocked selection/budget/idempotence/order tests, existing scoped CI, actual backend deployment and a real cached reading result.

Verified baseline before this change:
- Completed translation versions:108; total stored article versions:110.
- Stored translated UTF-8 text:621023 bytes; bilingual-related SQLite pages roughly3.1MiB. Text storage grows separately from the shared1GiB image cache; HTML image links do not duplicate image binaries here.
- Recent published >=8 records:12-52 per day over the sampled week, including Chinese articles.
- The previous newest100 batch completed; this does not imply that every historical >=8 article was translated.

The following deployment/cost entries are historical evidence, not the current automatic-discovery policy.

Cost research (2026-10-07, estimates rather than billed totals):
- 2248 current >=8 analyzed entries; roughly 2076 have English bodies, including prepared/source fallback.
- Their Reader-visible text totals about 19.7 million characters. The newest 100 English bodies total 0.598 million.
- Earlier completed translations: 56 articles, 206105 actual input+output tokens including retries.
- Estimate newest 100: 0.4-0.6 million tokens; all current English selections: 12-18 million.
- Existing relay's upstream default-group pricing: input 0.15 / output 0.60 balance credits per million
  for gpt-4o-mini. Credit-to-cash conversion depends on the user's purchase price.
- gpt-4.1-mini is 2.67 times these rates. Flex/Batch/peak-offpeak savings must not be assumed to pass
  through a Chat Completions relay. On-demand first reading uses the normal endpoint.

Deployment completed 2026-10-07 17:59:02 Asia/Shanghai:
- PR #166 merged as 9f80a2744d8657ea297a0be540da911f005f6a03.
- Reader CI 37603741588: 141 Python tests, 110 subtests, 14 JS/React tests passed;
  locked frontend build passed. Metadata compatibility 37603741649 passed.
- Tested head 1c419e24512d55f55958bab7a0f81eeacb634e61, src tree c27d6521f80851ec937d12082ff194b50438a5d2.
- Exact CI build 11474091866 is live; public index SHA256
  2287b8e54d5b8f8ae9889c74e0424d73c78a998c3ba33b12f26550f35e05c1e8.
- Production before explicit POST: 0 requested articles; detail/status GET added no calls/tokens.
- One real eligible English article #8644 completed in 8.48 seconds with 1104 total actual tokens.
  Repeat POST reused done content in 0.17 seconds with no new call/token.
- Windows Edge verified completed bilingual body and original/bilingual mode switching.
- Original 56 completed rows remain; 57 completed rows after the probe. Cancelled unfinished rows stay dormant.
- Existing native Miniflux PID 1230783 unchanged; health 200; shared image cache remains <=1 GiB.
- Existing private New API token reused server-side; model gpt-4o-mini.
  Global guardrail: at most 1000 batch requests / 1,000,000 reserved-or-actual tokens per UTC day.
  This is a ceiling, not a daily translation target. No article-library backfill.
- Costs above remain estimates. Cash conversion is unknown; upstream balance uses credits (symbol lightning).

Bounded preview authorized 2026-10-07:
- Pretranslate the newest 100 global eligible English articles with score >=8,
  rather than waiting for their first opens. Reuse already completed translations.
- This explicit batch does not enable automatic all-library translation.
- Reuse completed text when only source image addresses rotate: compare the source
  structure, other attributes, and every text block exactly. Keep raw source hashes,
  VERSION and PROMPT unchanged; render current images without storing alias bodies.
- Any text, regular link, alt text, user, model or prompt-version change remains isolated.
- Validate 100 concurrent batch claims and the matching HTTP connection pool offline.
  This verifies program capacity, not the external provider's sustained throughput.

Completed bounded preview 2026-10-07 19:43 +0800:
- All 100 selected current Reader bodies are readable as completed bilingual/Chinese
  content: 3159 blocks. Initially completed 52 articles were reused;
  the remaining 48 completed. One publisher body changed during the run (19 -> 18
  blocks) and its current version was translated using the existing demand path.
- Total newly recorded actual input+output usage was 266163 tokens,
  including retries and that source update. Ledger totals do not provide a split
  suitable for an exact cash bill.
- 100 status reads and repeat requests for seven completed bodies produced zero
  new upstream calls and zero new tokens; one rotated image address reused saved text.
- Production BILINGUAL_CONCURRENCY=100; the HTTP pool allows 100 connections.
  Empty queues do not fan out work. No sustained external 100-request load test
  was run, and no automatic all-library seeding was enabled.
- PR #168/#169 technical-label fixes and PR #171 image-address/concurrency fixes
  are merged. Final tested head 6c680d3641da7e1ef467655264f37aa26570e193,
  merge e5dba75445b73457d805c214cf325db0805e6af5, source tree b09fb874c0848bd9e8bb83a71599bbb330fe431b.
- Related Reader CI 37615234814 passed. Backend deployed at
  2026-10-07 19:38:52 +0800; readiness 200 and native Miniflux PID 1230783 unchanged.
- Frontend uses the existing deployed build. Windows Edge shows Chinese followed
  by English in the actual article; repeated opens use the completed cache.
