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
  At most three upstream translation batches in flight, no duplicate paid work across workers.
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
