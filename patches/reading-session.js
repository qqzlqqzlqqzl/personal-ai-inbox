/** Pure per-batch prefetch policy. No network, credentials or model calls. */
export const PREFETCH_FRACTION = 0.25
export const AUTO_PREFETCH_PAGES = 5

/** Let the first page paint before starting one background page at a time. */
export function scheduleAutoPrefetch(callback, started, clock = globalThis) {
  let stopped = false, frame = 0, timer = 0, idle = 0
  frame = clock.requestAnimationFrame(() => {
    frame = clock.requestAnimationFrame(() => {
      timer = clock.setTimeout(() => {
        if (stopped) return
        if (started >= 2 && clock.requestIdleCallback) {
          idle = clock.requestIdleCallback(() => { if (!stopped) callback() }, { timeout: 1500 })
        } else callback()
      }, started < 2 ? 120 : 800)
    })
  })
  return () => {
    stopped = true
    clock.cancelAnimationFrame(frame)
    clock.clearTimeout(timer)
    if (idle) clock.cancelIdleCallback(idle)
  }
}

/** Five additional pages after the initial page, independent of scroll position. */
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
