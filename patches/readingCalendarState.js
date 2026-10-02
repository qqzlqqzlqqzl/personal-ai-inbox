import { atom, computed } from "nanostores"
import { authState } from "@/store/authState"
import { dataState } from "@/store/dataState"
import { getAuthSessionKey } from "@/utils/auth"
import { calendarDay, calendarStart, resolveReadingTimezone } from "@/utils/reading-calendar"

const clock = atom(Date.now())
export const getReadingCalendarSnapshot = () => {
  const data = dataState.get()
  const zone = resolveReadingTimezone(data.currentUser?.timezone)
  const ready = Boolean(data.currentUser?.id && data.loadState.identity.hasSnapshot &&
    data.loadState.identity.activity === "idle" && !data.loadState.identity.error && data.identityAuthSessionKey === getAuthSessionKey(authState.get()) && zone)
  const day = ready ? calendarDay(zone) : null
  const key = JSON.stringify([data.sessionRevision, data.identityAuthSessionKey, data.currentUser?.id, ready ? zone : null, day])
  return { ready, zone: ready ? zone : null, day, key, cutoff: ready ? calendarStart(day, zone).unix() : null }
}
export const readingCalendarState = computed([dataState, authState, clock], getReadingCalendarSnapshot)
export const readingCalendarKeyState = computed(readingCalendarState, value => value.key)
export const requireReadingCalendar = () => {
  const snapshot = getReadingCalendarSnapshot()
  if (!snapshot.ready) throw new Error("阅读时区尚未确认，请重试身份加载")
  return snapshot
}
export const assertReadingCalendarCurrent = (snapshot) => {
  if (!getReadingCalendarSnapshot().ready || snapshot.key !== getReadingCalendarSnapshot().key)
    throw new Error("账号、阅读时区或日期已改变，已停止后续批量操作")
}
export const startReadingCalendarClock = () => {
  let timer
  const tick = () => {
    if (getReadingCalendarSnapshot().key !== readingCalendarState.get().key) clock.set(Date.now())
    clearTimeout(timer)
    const snapshot = getReadingCalendarSnapshot()
    // Calendar midnights can be 23/25 hours apart; poll plus a precise next second.
    timer = setTimeout(tick, Math.min(30000, 1000 - (Date.now() % 1000)))
  }
  const resume = () => { if (document.visibilityState !== "hidden") tick() }
  tick()
  window.addEventListener("focus", tick)
  window.addEventListener("pageshow", tick)
  document.addEventListener("visibilitychange", resume)
  return () => {
    clearTimeout(timer)
    window.removeEventListener("focus", tick)
    window.removeEventListener("pageshow", tick)
    document.removeEventListener("visibilitychange", resume)
  }
}
