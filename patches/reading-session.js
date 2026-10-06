/** Pure per-batch prefetch policy. No network, credentials or model calls. */
export const PREFETCH_FRACTION = 0.25
export const AUTO_PREFETCH_PAGES = 10

/** Ten additional pages after the initial page, independent of scroll position. */
export function autoPrefetchDecision(window, started) {
  if (!window || started >= AUTO_PREFETCH_PAGES) return null
  return { reason: "startup", page: started + 1 }
}

export function nextWindow(previous, snapshot, count, cursor) {
  if (!previous || previous.snapshot !== snapshot || count < previous.count) {
    return { snapshot, count, cursor, start: 0, size: count, stalled: false }
  }
  if (count > previous.count) {
    return { snapshot, count, cursor, start: previous.count, size: count - previous.count, stalled: false }
  }
  // A duplicate-only page advanced the raw cursor but added no visible cards.
  // Stop speculative chaining until the user scrolls again (manual retry still works).
  return { ...previous, cursor, stalled: previous.stalled || cursor !== previous.cursor }
}

export function prefetchDecision(window, firstVisible, remaining, viewport) {
  if (!window?.size || window.stalled) return null
  const target = window.start + Math.floor(window.size * PREFETCH_FRACTION)
  if (!window.stalled && firstVisible >= target) return { reason: 'quarter', target }
  if (remaining <= Math.max(64, viewport * 0.20)) return { reason: 'near-end', target }
  return null
}
