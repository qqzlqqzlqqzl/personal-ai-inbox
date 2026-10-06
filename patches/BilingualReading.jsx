import { useLayoutEffect, useRef, useState } from "react"
import apiClient from "@/apis/ofetch"
import "./BilingualReading.css"

export const READING_MODES = [
  ["bilingual", "双语"],
  ["chinese", "仅中文"],
  ["original", "原文"],
]

// Select one server-prepared document. Images, code and links still go through
// ArticleDetail's existing parser; the browser never translates or duplicates DOM.
export function getBilingualReading(entry, requestedMode = "bilingual") {
  const mode = READING_MODES.some(([value]) => value === requestedMode)
    ? requestedMode
    : "bilingual"
  const original = typeof entry?.content === "string" ? entry.content : ""
  if (mode === "original") return { mode, html: original, message: "" }

  const translation = entry?.translation
  const candidate = translation?.language === "zh-CN"
    ? translation[mode === "chinese" ? "chinese_html" : "bilingual_html"]
    : null
  const available = typeof candidate === "string" && candidate.trim().length > 0
  const complete = ["done", "ready", "native"].includes(translation?.status)
  const unavailable = ["error", "notconfigured", "disabled", "budget_paused", "waiting_model"].includes(translation?.status)
  const message = translation?.status === "native" ? "" : available
    ? (complete ? "" : "部分段落已译")
    : (unavailable ? "译文暂不可用，先显示原文" : "译文准备中，先显示原文")
  return { mode, html: available ? candidate : original, message }
}

export const shouldPollTranslation = (status) => status === "pending" || status === "partial"

// One lightweight request at a time, with cancellation independent of whether
// the transport honors AbortSignal. No body fetches, model calls or UI loading.
export function startBilingualPolling({
  status, request, isCurrent, onTranslation,
  setTimer = setTimeout, clearTimer = clearTimeout,
}) {
  let stopped = false, timer = null, controller = null
  const stop = () => {
    stopped = true
    clearTimer(timer)
    controller?.abort()
  }
  const schedule = () => {
    if (!stopped && isCurrent() && shouldPollTranslation(status)) timer = setTimer(poll, 10000)
  }
  const poll = async () => {
    timer = null
    if (stopped || !isCurrent()) return stop()
    controller = new AbortController()
    try {
      const translation = await request(controller.signal)
      if (stopped || !isCurrent()) return stop()
      if (!translation || typeof translation.status !== "string") throw new Error("Invalid translation")
      status = translation.status
      onTranslation(translation)
    } catch {
      // A transient failure keeps the readable document and retries later.
    } finally {
      controller = null
      schedule()
    }
  }
  schedule()
  return stop
}

export function useBilingualTranslation(entry) {
  const entryId = entry.id, content = entry.content, source = entry.translation
  const current = useRef(null)
  current.current = { entryId, content, source }
  const [polled, setPolled] = useState(null)
  useLayoutEffect(() => {
    let active = true
    const isCurrent = () => active && current.current.entryId === entryId &&
      current.current.content === content && current.current.source === source
    const stop = startBilingualPolling({
      status: source?.status,
      isCurrent,
      request: signal => apiClient.get(`/v1/ai/translation/${encodeURIComponent(entryId)}`, {
        signal, retry: 0, timeout: 8000,
      }),
      onTranslation: translation => {
        // A changed server body belongs to a fresh detail request, not this view.
        if (source?.source_hash && translation.source_hash !== source.source_hash) {
          active = false
          return
        }
        setPolled({ entryId, content, source, translation })
      },
    })
    return () => { active = false; stop() }
  }, [entryId, content, source])
  return polled?.entryId === entryId && polled.content === content && polled.source === source
    ? polled.translation : source
}

export default function BilingualReading({ mode = "bilingual", message = "", onModeChange }) {
  return (
    <div className="reader-bilingual-controls">
      <div className="reader-bilingual-modes" role="group" aria-label="正文语言">
        {READING_MODES.map(([value, label]) => (
          <button
            key={value}
            type="button"
            aria-pressed={mode === value}
            onClick={() => onModeChange(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {message && <span role="status">{message}</span>}
    </div>
  )
}
