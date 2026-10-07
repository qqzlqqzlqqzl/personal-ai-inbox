# On-demand cached bilingual Reader articles

Translate eligible English article bodies when a user actually opens the article.
Do not translate unseen articles because list, detail, image or ten-page prefetch runs.

Acceptance:
- Current analysis must be done with score >= 8; preserve existing content-quality and ownership checks.
- Detail GET and status GET only read cached results. An authenticated POST
  /mf/v1/ai/translation/{entry_id} requests work for the owned, current body.
- Remove automatic library seeding. Previously cancelled pending work is dormant
  until the article is explicitly opened again. Existing complete translations are reused.
- Show original content immediately and add completed Chinese paragraphs above English.
  Persist partial and final results; repeat opens do not pay again for unchanged content.
- Keep Chinese, bilingual and original mode controls as top-right icons with accessible labels.
  Preserve the existing reading-tools icons, source markup, signed images and latest 1 GiB cache.
- Use existing server-side New API credentials and gpt-4o-mini. No keys in Git/browser/Dot.
  Reference the public Immersive Translate prompt rules: faithful translation, same structure,
  preserved technical terms/code, and no extra commentary:
  https://immersivetranslate.com/zh-Hans/docs/prompts/
- Reuse existing bounded chunks/cache validation; wake the worker promptly on a real request.
  BILINGUAL_CONCURRENCY controls 1-100 upstream batches (fallback 3); the HTTP
  connection pool uses the same capacity. Empty queues do not start idle batch tasks.
  Claims and daily budgets still prevent duplicate or unbounded paid work.
- Record actual API usage and safe elapsed time. No all-history paid probe or new service/environment.
- Run focused backend/API and React tests plus the locked frontend build. Publish that tested build
  to the existing service, verify a real article and cache reuse, then merge/close this issue.

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
