import dayjs from "dayjs"
import utc from "dayjs/plugin/utc"
import timezone from "dayjs/plugin/timezone"

dayjs.extend(utc)
dayjs.extend(timezone)

// Personal deployment default only for an authenticated account with no zone.
// Invalid explicit values fail closed; UTC is a valid account timezone.
export const resolveReadingTimezone = (value) => {
  const zone = value == null || value === "" ? "Asia/Shanghai" : value
  if (typeof zone !== "string") return null
  try {
    new Intl.DateTimeFormat("en", { timeZone: zone }).format(0)
    return zone
  } catch { return null }
}

export const calendarDay = (zone, now = Date.now()) => dayjs(now).tz(zone).format("YYYY-MM-DD")

export const selectedCalendarDay = (value) => {
  const day = typeof value === "string" ? value : value?.format?.("YYYY-MM-DD")
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day || "") || dayjs.utc(day).format("YYYY-MM-DD") !== day)
    throw new Error("Invalid calendar date")
  return day
}

export const calendarStart = (day, zone) => dayjs.tz(selectedCalendarDay(day), zone).startOf("day")
export const calendarEndTimestamp = (day, zone) => dayjs.tz(selectedCalendarDay(day), zone).endOf("day").unix()

// Today keeps its existing lower-bound-only contract. Raw >, AI/notes >=.
// Preserve sub-millisecond membership at the exact seconds boundary.
export const matchesToday = (value, cutoff, inclusive = false) => {
  const instant = typeof value === "number" ? value * 1000 : Date.parse(value)
  if (!Number.isFinite(instant)) return false
  if (instant > cutoff * 1000) return true
  if (instant < cutoff * 1000) return false
  const fraction = typeof value === "string" ? value.match(/\.(\d+)(?:Z|[+-]\d\d:\d\d)$/)?.[1] : null
  return inclusive || Boolean(fraction && /[1-9]/.test(fraction))
}
