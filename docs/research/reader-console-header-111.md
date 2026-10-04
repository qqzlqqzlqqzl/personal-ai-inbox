# Reader console header inset (Refs #111)

F110255's hosted history and console screenshots show scrolled body text in the
strip above the sticky title. The dialog has 22px top padding (14px at widths up
to 600px), while the header sticks at the content inset. Its opaque background
does not cover that strip.

The change moves exactly that top inset from the dialog into the header's top
padding. Negative inline margins and matching inline padding extend the header
background across the dialog padding without moving its title or close button.
The initial body position, total scroll extent, typography, dialog size limits,
bottom padding and scrollbar remain the same by construction. Reading and
navigation styles after the console section are unchanged byte for byte.

Only the F110255 stylesheet SHA256
`d043b3b48c99076fa9cfdf83ee7c95509c5fa9ac3d75504414dd5c49a2e39686`
is appended to the existing reviewed upgrade history. Unknown source is still
rejected before installation. The old stylesheet is retained as a test fixture.

## Reproducible checks

- `timeout 60s python -m pytest tests/test_console_header_overlay.py tests/test_interaction_installer.py`
  checks the exact old input, clean installation, reviewed upgrade, repeated
  installation, unknown drift rejection and the unchanged reading suffix
- The existing `test_review_resource_components.mjs` and
  `test_review_workflows.mjs` cover console state and resource/draft behavior
- Build using the existing pinned `tests/build_ci_reader.py` procedure, then run
  `timeout 600s env AI_NEWS_TEST_BUILD=runtime/browser-build python tests/console_header_browser.py`
  with CI's pinned Playwright Chromium

The browser entry exercises all three tabs at 1440×900, 390×844, 844×390 and
720×480, in light and dark themes. Each tab first restores the exact legacy CSS
inside the isolated context and confirms its exposed top gap. The candidate
then must preserve the initial title, close button, nav, body and scroll extent;
cover the top and sides at middle and bottom scroll positions; keep the footer
reachable; and restore focus on closing. No synthetic API writes are allowed.
It saves both comparison screenshots and geometry, including a failure capture.
It never changes the on-disk build to create that comparison.

## Acceptance limits

The cloud authoring environment lacks Chromium headless-shell revision 1243.
The real browser attempt failed before page creation; new geometry and pixels
are **NOT RUN**, rather than inferred from the passing installer/component tests.
The original hosted screenshots remain the observed defect baseline.

720×480 is a constrained viewport, not native 200% zoom. Physical safe-area,
native zoom and device acceptance remain separate gates. This change leaves
the existing dialog width/height limits and safe-area rules untouched, but does
not claim those device checks passed. Hosted browser acceptance and independent
review are required before release. This candidate does not close #111.
