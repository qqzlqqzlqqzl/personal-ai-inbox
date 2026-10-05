# Reader query, reading controls and theme evidence

Refs #109, #110, #112; the badge consumes the fixed #103/#105 contract from backend commit `9b2aa8c1f3a16633151dbb8d41dd1e7d229cacd4`. This branch does not merge that backend implementation.

## Query ownership

The initial list response publishes `articleListResultOwner = {requestKey, sessionRevision}` only after existing request-ID, query-key, session, mutation and response-shape validation. Loading and error results cannot own a displayed total. The serializer remains the existing request key, including AI lens and calendar identity; no second cache or pagination cursor is introduced.

The sidebar selects its scope from the committed router path, independently of the later Content store effect. It displays the filtered total only for the current owned result. A loading/error/mismatched selected scope has no numeric badge; it does not fall back to the unfiltered unread counter. Inactive scopes retain native counts. Successful empty results retain numeric zero in state and the existing empty-state UI. Raw date filters, like search filters, use the response total rather than whole-scope unread counts.

`test_query_result_ownership.mjs` runs the actual generated React hook and stores with controlled promises, including all→today, rapid reversal, late responses, empty/error, search/AI/date changes and session invalidation. This is logical React/jsdom evidence, not a browser paint or performance measurement.

`query_result_ownership_browser.py` is the corresponding real built-browser entry. It holds synthetic loopback responses and records mutation/rAF observations. It must run against the precise combined candidate build before browser acceptance. All test counts, article IDs and content are synthetic.

## Reading controls

The same typography/focus bar now sits between the article header and body, inside the existing AdaptiveScrollArea. Equal font, line-height, border-box and padding align summary/button controls. The bar is sticky within that scroller so exit remains reachable in a long article.

Focus mode hides metadata and the AI summary, retaining the title, navigation, body, notes and original-source link. A visible body-block anchor compensates scroll changes relative to the usable area below the bar, using ArticleDetail's existing `getScrollElement()` ref. It never treats the outer article or `window.scrollY` as the scroller. Explicit toggles keep the same focused button; unmount removes the mode class. Native scroll limits still apply to short articles and document boundaries.

`test_reading_focus_anchor.mjs` uses actual React with explicitly synthetic geometry. `reading_focus_browser.py` measures actual desktop/narrow, light/dark layout and scrollers. Physical Android/iOS, keyboard and browser-toolbar behavior remain the separate #86 gate.

## Theme evidence (#112)

The mobile test persists schema-supported `themeMode` once in a fresh test context. It never paints `arco-theme` itself. Initial load, reload, navigation, Back/Forward, article entry and screenshots check persisted/applied theme, computed color scheme and sampled text contrast. It adds an actual light full-text scenario alongside the existing dark one, preserving the old mobile/desktop focus assertion and the 879 navigation fix.

The existing dark-labelled light screenshot remains failed historical evidence. Source and synthetic tests do not replace new real light/dark screenshots.

## Badge semantics

`content_excluded` is an unscored preserved original entry, not endlessly pending work. Previously completed entries remain done; explicit false eligibility is separately labelled “暂不推荐” while retaining the numeric score. Unknown/null never implies paid or low quality. Publisher nonfree declarations and conflicting access evidence are pending review, not claims of a visible paywall or a price.

Processing labels come only from the fixed reason catalogue. Pause does not hide unresolved submissions or unreadable ledger evidence; a global unknown submission does not claim that this article was submitted. Quota reserve is not described as exhausted quota. Arbitrary messages, unknown enums and invalid time strings are not displayed. The frontend does not recalculate backend eligibility, list totals or queue state.

## Overlay admission

Only these independently byte-verified 9a generated preimages were appended to the existing history, without removing any older entry or weakening the installer:

- ReadingControls.jsx: `733c5102b090d4121afaa3635342dcfe05c13fdf823ab00547215d44d554eba7`
- ReviewWorkflows.css: `1a6eb73d473aedc2373ca21fd746ff963390ccefb22c3e1fe21ede80b69b1ea4`
- ArticleDetail.jsx: `63007fdc9a3e706fcc352511ae9c2957b5cb2cfdc40d5db7881c08cdc36631aa`

Fresh prepare, admitted-old upgrade, repeat prepare and rejection-before-write for each unknown preimage are separate controls. No production deployment, CODE pin update, migration, import or issue closure is implied.
