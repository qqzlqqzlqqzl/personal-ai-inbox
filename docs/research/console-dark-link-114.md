# Console link contrast (Refs #114, #111)

Exact J's hosted dark console screenshots show ordinary blue links against
`#232324`. The original `#2563eb` yields 3.038:1 on that background; it passes on
white at 5.169:1. The required minimum for ordinary text is 4.5:1, including hover
and keyboard focus, without rounding a lower value up to the threshold.
[WCAG 2.2 Contrast Minimum](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)

The product change adds only a dark-theme rule for anchors inside `.ai-dialog`,
using `#93c5fd` (8.708:1 on the observed dark background). It keeps the light rule,
font size, decorations, destinations, layout and focus behavior. This arithmetic
is a color control; actual browser computed colors remain an acceptance gate.

`tests/console_header_browser.py` already exercises the real built Reader in
eight viewport/theme contexts. The original geometry and closing-focus checks
remain intact. At 1440 and 390px, the same mandatory entry now also tests the
task-status, deployment, source and subscription links in normal/hover/focus
states. It records computed text colors, alpha-composited ancestor backgrounds,
font metrics, active-link semantics, targets and contrast, and saves screenshots.
Unsupported background images, filters, opacity or blend modes fail visibly
instead of producing a guessed background.

The old dark color is injected only into the isolated browser context as a
negative control; it must fail the ordinary-text threshold in the normal state.
Each state's original content/font/decoration is compared with the candidate.
The control is labelled in the JSON and does not modify the on-disk build. The
original J screenshots remain the observed defect evidence, rather than being
represented as newly collected computed-style measurements.

Validation commands after the usual pinned build:

- `timeout 60s python -m pytest tests/test_console_link_contrast.py`
- `timeout 30s node tests/test_console_link_compositing.mjs`
- `timeout 600s env AI_NEWS_TEST_BUILD=runtime/browser-build python tests/console_header_browser.py`

The browser writes `runtime/console-header/*/link-contrast-*.json` and
`link-*.png`; the existing CI artifact path already includes them. New Python
and Node controls are found by the existing test discovery. No workflow edit is
needed. Cloud authoring lacks pinned Chromium1243, so new browser measurements
are NOT RUN here. Independent review, a full new-head hosted run, deployment and
actual deployed dark-theme verification remain necessary to close #114.
