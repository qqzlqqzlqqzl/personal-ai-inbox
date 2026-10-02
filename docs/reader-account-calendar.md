# Reader account calendar — issue 85

Date scopes use the authenticated Miniflux `/v1/me` account timezone through the existing `currentUser`. A valid UTC value remains UTC. Only a successfully resolved account with an absent timezone uses the explicit personal-deployment default Asia/Shanghai. Invalid explicit zones, unknown identity, failed identity refresh, and an identity bound to another auth session cannot authorize date requests or bulk actions.

The provider and list hook gate cold-start requests. Request identity includes the account/session, effective timezone and calendar day. Identity/day changes and focus/pageshow/visible resume clear rows, totals and pagination, invalidate the list and refresh raw counts. Responses from old list, load-more and count requests cannot publish into the new calendar. Load-more waits for the new initial list. Existing filters, score, unread, search, visibility, sorting and native/AI pagination remain in their existing stores and URL construction.

Calendar selection remains YYYY-MM-DD, including the picker Today shortcut. Conversion uses that calendar value in the account IANA zone. Publication/change instants and global getTimestamp are unchanged. Arbitrary valid account IANA zones retain 23/25-hour DST days.

## Retained timestamp contract

This narrow patch changes the timezone authority, not API interval inclusivity:

| Scope | Raw pinned Miniflux | AI/notes gateway |
| --- | --- | --- |
| Today | published_at > calendar midnight; no upper bound | published_at >= calendar midnight; no upper bound |
| Selected date | strict > start and < endOf(day).unix() | existing inclusive >= / <= where the gateway consumes the corresponding parameters |

Selected-date endOf(day) remains truncated to whole seconds. This does not claim an exact half-open natural-day interval or repair the existing fractional final-second exclusion. No lower-bound second subtraction, new next-midnight query parameter, or global pagination/publication timestamp rewrite is used. Optimistic native Today badges use the raw strict lower-bound predicate, including positive sub-millisecond fractions at midnight. Regular native count refreshes explicitly exclude the persistent AI lens.

Two pre-existing adjacent limitations remain: native dynamic headers do not apply filterDate, and AI starred/history gateway selectors do not consistently consume changed_after/changed_before. This patch does not claim to repair those backend/date-range contracts.

## Bulk writes

Every batch operation captures the resolved calendar/account key before fetching. It checks the key before each fetch and again before each write; a day, timezone or account change stops subsequent batches. Footer completion also checks the original key before updating rows. An already submitted batch remains a write to its original request session; it cannot authorize a subsequent batch on the next day or another account. Unknown/failed identity never falls back to browser timezone.

## Review and verification

Implementation inputs: src/patch_scope_ai_filters.py plus checked-in patches/calendar-overlay.py and its two JS helpers. The same strict anchors and final reviewed-overlay admission remain enabled. Pristine rebuild/repeat equivalence passes.

Synthetic tests freeze 2026-10-02T01:37Z across America/Los_Angeles, UTC and Asia/Shanghai browsers; cover identical IDs/totals, raw counters, exact/fractional boundaries, UTC/missing/invalid/late identity, Shanghai midnight, 23/25-hour DST, real React stale list/load-more/account/zone responses, resume and actual optimistic read/unread badges. Chromium covers all three browser timezones at 1440 and 390px, persistence and zero writes.

Local isolated validation: 691 Python tests and 161 subtests; complete JavaScript/DOM suite and source-history component; repeat build; validated /inbox/ Vite bundle; existing history, console, reading recovery, IME and sorting Chromium gates; six new timezone/viewport Chromium cases. No production test endpoint, database, private config, live content writes, model calls or deployment was used.

The paired acceptance CODE pin is intentionally unchanged until the parent independently reviews the new src tree. Its strict byte comparison must remain enabled; after that review, pin the reviewed source commit and run full hosted CI on the exact final head. No Docker/PostgreSQL setup was attempted on this VM.

Identity refresh and retry remain gated until the new `/me` response succeeds; clearing a prior error while a request is pending cannot authorize the old snapshot. Invalid account zones expose a retry notice, including when identity already has a snapshot. The batch calendar snapshot is taken before collecting starred IDs and checked before/after each ID page and before each write batch. Transport-level account rejection remains intact.

The real Chromium regression uses date-filtered fixtures and covers held identity, Shanghai midnight, focus/pageshow/visibility recovery and actual picker membership across all three browser zones at 1440px and 390px. Picker bounds explicitly retain raw strict `>after/<before` versus AI inclusive `>=after/<=before`, including exact and fractional-second edges. The retained whole-second end-of-day boundary is not expanded to the following midnight.

Resume checks the calendar using the cached authenticated `/me` snapshot. A timezone change made on another device becomes effective after identity reload; resume does not poll `/me`. Before production acceptance, verify the actual account timezone with an authorized read-only `/v1/me` request rather than inferring it from the synthetic Shanghai fixture. No user timezone change is part of this fix.
