import apiClient from "@/apis/ofetch"

const HEARTBEAT_MS = 10_000
const IDLE_MS = 90_000
const newSessionId = () => globalThis.crypto?.randomUUID?.() ??
  "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, c => {
    const r = Math.floor(Math.random() * 16)
    return (c === "x" ? r : (r & 3) | 8).toString(16)
  })

export const startReadingTelemetry = ({ entryId, getEntryState, getScrollElement }) => {
  const sessionId = newSessionId()
  const initialEntry = getEntryState?.()
  let activeMs = 0
  let lastTick = performance.now()
  let lastActivity = lastTick
  let foreground = document.visibilityState === "visible" && document.hasFocus()
  let maxScrollPct = 0
  let disposed = false
  let queue = Promise.resolve()

  const tick = () => {
    const now = performance.now()
    if (foreground) {
      const until = Math.min(now, lastActivity + IDLE_MS)
      activeMs += Math.max(0, Math.min(until - lastTick, HEARTBEAT_MS * 1.5))
    }
    lastTick = now
  }
  const updateScroll = () => {
    const el = getScrollElement()
    const body = el?.querySelector(".article-body")
    // Empty/deferred content must not count as a fully read short article.
    if (!el || el.clientHeight <= 0 || !body || body.getAttribute("aria-busy") === "true" ||
        (!body.textContent?.trim() && !body.querySelector("img,video,audio"))) { return }
    const range = Math.max(0, el.scrollHeight - el.clientHeight)
    const pct = range <= 1 ? 100 : Math.min(100, Math.max(0, el.scrollTop / range * 100))
    maxScrollPct = Math.max(maxScrollPct, pct)
  }
  const touch = () => { tick(); lastActivity = performance.now() }
  const send = (action, keepalive = false) => {
    if (disposed) { return }
    tick(); updateScroll()
    const current = getEntryState?.()
    const entry = current?.id === entryId ? current : initialEntry
    const body = { action, session_id: sessionId, entry_id: entryId,
      active_ms: Math.round(activeMs), max_scroll_pct: Math.round(maxScrollPct * 10) / 10,
      starred: typeof entry?.starred === "boolean" ? entry.starred : null,
      read_status: ["read", "unread"].includes(entry?.status) ? entry.status : null }
    // Serialize so a heartbeat/close cannot arrive before the open request.
    queue = queue.catch(() => {}).then(() =>
      apiClient.post("/v1/ai/reading-session", body, { keepalive, retry: 0 })
    ).catch(() => {})
  }
  const syncForeground = () => {
    tick()
    foreground = document.visibilityState === "visible" && document.hasFocus()
    lastTick = performance.now()
    if (foreground) { lastActivity = lastTick }
    else { send("heartbeat", true) }
  }
  const onPageHide = () => send("close", true)
  const scrollElement = getScrollElement()
  const onScroll = () => { touch(); updateScroll() }
  document.addEventListener("visibilitychange", syncForeground)
  globalThis.addEventListener("focus", syncForeground)
  globalThis.addEventListener("blur", syncForeground)
  globalThis.addEventListener("pagehide", onPageHide)
  globalThis.addEventListener("pointerdown", touch, { passive: true })
  globalThis.addEventListener("keydown", touch)
  scrollElement?.addEventListener("scroll", onScroll, { passive: true })
  send("open")
  const timer = globalThis.setInterval(() => send("heartbeat"), HEARTBEAT_MS)
  return () => {
    if (disposed) { return }
    send("close", true)
    disposed = true
    globalThis.clearInterval(timer)
    document.removeEventListener("visibilitychange", syncForeground)
    globalThis.removeEventListener("focus", syncForeground)
    globalThis.removeEventListener("blur", syncForeground)
    globalThis.removeEventListener("pagehide", onPageHide)
    globalThis.removeEventListener("pointerdown", touch)
    globalThis.removeEventListener("keydown", touch)
    scrollElement?.removeEventListener("scroll", onScroll)
  }
}
