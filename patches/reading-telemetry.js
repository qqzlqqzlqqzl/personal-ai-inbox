import apiClient from "@/apis/ofetch"

const HEARTBEAT_MS = 10_000
const IDLE_MS = 90_000

const newSessionId = () =>
  globalThis.crypto?.randomUUID?.() ??
  `${Date.now()}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`

export const startReadingTelemetry = ({ entryId, getEntryState, getScrollElement }) => {
  const sessionId = newSessionId()
  let activeMs = 0
  let lastTick = performance.now()
  let lastActivity = lastTick
  let foreground = document.visibilityState === "visible" && document.hasFocus()
  let maxScrollPct = 0
  let disposed = false

  const tick = () => {
    const now = performance.now()
    if (foreground && now - lastActivity <= IDLE_MS) {
      activeMs += Math.max(0, now - lastTick)
    }
    lastTick = now
  }

  const updateScroll = () => {
    const el = getScrollElement()
    if (!el) {return}
    const range = Math.max(0, el.scrollHeight - el.clientHeight)
    const pct = range === 0 ? 100 : Math.min(100, Math.max(0, (el.scrollTop / range) * 100))
    maxScrollPct = Math.max(maxScrollPct, pct)
  }

  const touch = () => {
    tick()
    lastActivity = performance.now()
  }

  const snapshot = (action) => {
    tick()
    updateScroll()
    const entry = getEntryState?.()
    return {
      action,
      session_id: sessionId,
      entry_id: entryId,
      active_ms: Math.round(activeMs),
      max_scroll_pct: Math.round(maxScrollPct * 10) / 10,
      starred: typeof entry?.starred === "boolean" ? entry.starred : null,
      read_status: ["read", "unread"].includes(entry?.status) ? entry.status : null,
    }
  }

  const send = (action) => {
    if (disposed && action !== "close") {return}
    void apiClient.post("/v1/ai/reading-session", snapshot(action)).catch(() => {})
  }

  const syncForeground = () => {
    tick()
    foreground = document.visibilityState === "visible" && document.hasFocus()
    lastTick = performance.now()
    if (foreground) {lastActivity = lastTick}
  }

  const scrollElement = getScrollElement()
  const onScroll = () => {
    touch()
    updateScroll()
  }
  const onActivity = () => touch()

  document.addEventListener("visibilitychange", syncForeground)
  window.addEventListener("focus", syncForeground)
  window.addEventListener("blur", syncForeground)
  globalThis.addEventListener("pointerdown", onActivity, { passive: true })
  globalThis.addEventListener("keydown", onActivity)
  scrollElement?.addEventListener("scroll", onScroll, { passive: true })

  updateScroll()
  send("open")
  const timer = globalThis.setInterval(() => send("heartbeat"), HEARTBEAT_MS)

  return () => {
    if (disposed) {return}
    send("close")
    disposed = true
    globalThis.clearInterval(timer)
    document.removeEventListener("visibilitychange", syncForeground)
    window.removeEventListener("focus", syncForeground)
    window.removeEventListener("blur", syncForeground)
    globalThis.removeEventListener("pointerdown", onActivity)
    globalThis.removeEventListener("keydown", onActivity)
    scrollElement?.removeEventListener("scroll", onScroll)
  }
}
