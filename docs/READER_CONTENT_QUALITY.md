# Processing reasons and content eligibility

This extension keeps the numeric score and the original subscription article separate from recommendation eligibility. It does not enable scheduling, resolve unknown submissions, clear claims, refresh provider quota, run models or change read marks/notes.

## Entry API contract

Existing `ai.state` and score fields retain their meanings. New extractions which are explicitly excluded use `state=content_excluded`; they remain in the ordinary reader, skip analysis, and do not appear as indefinitely pending work. Previously completed results remain `done`, even when a reviewed eligibility correction excludes them from recommendations.

`ai.content_quality` has:

- `policy_version`: `reader-content-quality-v1`
- `recommendation_eligible`: `true`, `false` or `null`
- `reason_codes`: bounded machine-readable strings
- `access`: `public`, `paid_fulltext`, `paid_subscription`, `login_required` or `unknown`
- `information`: `substantive`, `low_information` or `unknown`

The recommendation query keeps the user's `done + score >= minimum` rule, excluding only explicit `false` eligibility before total/page selection. Legacy null assessments are not hidden wholesale and must not be presented as verified full text. The same receipt drives the detail badge. Raw articles, public excerpts, original URLs and notes remain available.

An article-level publisher nonfree declaration must match the requested article identity and any page canonical. It creates `recommendation_eligible=false`, `access=unknown`, `reason_codes=[publisher_nonfree_pending_review]`. The UI should say the publisher marks it nonfree and access conditions need checking, not claim a payment popup or a price. Nested Product/recommendation metadata and different canonical identities cannot establish this result. Conflicting same-article evidence creates `conflicting_access_evidence` and requires review; it is not a permanent paid label. Explicit visible gates use `explicit_paid_gate`. Free login/registration remains distinct via `free_login_prompt`.

Known GitHub release download templates are stripped before information judgment. `release_template_only` and `release_subject_without_explanation` exclude template-only or unexplained change-subject entries. Meaningful short security/actionable notices and explanations remain eligible; there is no new general character-count exclusion. Unknown or failed extraction produces `source_unassessed` or `extraction_failed`, not a low-value/paywall assertion.

## Durable identity and correction

Schema migration adds nullable `analyses.content_quality`. Existing rows retain null. Each JSON receipt stores the policy version, observed source hash/time and exact entry/user/URL/content-hash/source-text-hash binding. A stale or malformed receipt projects null with `quality_source_changed` or `quality_unverified`. Raw page/body evidence is not returned in the added API fields.

Fulltext preparation writes the source and receipt in the same existing owner/source compare-and-swap transaction. Import keeps valid exclusions and records a legacy assessment inside its existing result/import transaction; duplicate imports remain idempotent. `core.record_content_quality` supports a reviewed null→false or false→true correction only when the current identity/source and expected previous receipt still match. It does not scan history or fetch a page. No production migration or historical repair is performed by building this change.

## Processing reasons

`ai.processing` contains `reason_code`, `reason_codes`, fixed `message`, `observed_at`, `next_retry_at` and `stale`. It reads only existing local config/pause/status snapshots and existing SQLite ledgers with `mode=ro`; one page shares a snapshot. It never calls `month_control.status`, provider commands or service control.

Primary reasons are `complete`, `not_recommended`, `paused`, `schedule_disabled`, `reconcile_only`, `submission_unknown`, `quota_reserved`, `quota_unknown`, `cooldown`, `queued`, `processing`, `awaiting_import`, `extraction_failed`, `source_review_required`, `unknown`. Secondary codes include `submission_unknown`, `ledger_unavailable`, `state_evidence_conflict`. Global unresolved submissions are not presented as proof that the particular article was submitted. A current control/cache conflict becomes unknown instead of showing an old stopped state after a control change. `quota_reserved` means the guard's safety reserve, not necessarily zero remaining quota.

Existing gateway GET status/entry routes retain their prior side-effect caveats; this change adds no provider refresh to processing projection and does not change `month_control.py`. Production investigation should use verified native reader GET endpoints and direct local cached/SQLite observations rather than assume any gateway GET is mutation-free.

## Fixture provenance and live acceptance

Three llama.cpp release bodies are separate public API fixtures for b11385, b11384 and b11378. Two Verge fixtures contain only public canonical/NewsArticle metadata supplied by the main coordinator, with no user identity or copied article body. Absence of a visible payment prompt is retained. Synthetic and public-source checks do not establish a production migration or live queue recovery. Real article transition, frontend pairing, exact combined CI and production verification remain separate acceptance steps.
