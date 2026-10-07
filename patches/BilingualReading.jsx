import { useEffect, useRef, useState } from "react"
import apiClient from "@/apis/ofetch"
import "./BilingualReading.css"

export const READING_MODES = [
  ["bilingual", "中英对照"],
  ["chinese", "中文"],
  ["original", "原文"],
]

export const TRANSLATION_POLL_MS = 2000
export const shouldPollTranslation = status => ["pending", "partial", "queued", "processing"].includes(status)

// This is a conservative UI guard, not permission to call a model. The POST
// endpoint independently verifies ownership, score, source language and budget.
export function isTranslationEligible(entry) {
  if (!entry?.id || entry.content_deferred || entry.ai?.state !== "done" ||
      !Number.isFinite(Number(entry.ai.score)) || Number(entry.ai.score) < 8 ||
      typeof entry.content !== "string" || !entry.content.trim()) return false
  const language = entry.content_language || entry.language
  if (typeof language === "string" && language && !/^en(?:[-_]|$)/i.test(language)) return false
  const text = entry.content.slice(0, 50000)
    .replace(/<(script|style|pre|code)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, " ")
    .replace(/<[^>]*>|&(?:#\w+|\w+);/g, " ")
  const latin = (text.match(/[A-Za-z]/g) || []).length
  const cjk = (text.match(/[\u3400-\u9fff]/g) || []).length
  return latin > 0 && cjk / Math.max(1, latin + cjk) < 0.25
}

// Choose server-prepared HTML without modifying URLs, duplicating images, or
// introducing a second parser. ArticleDetail retains its native render channel.
export function getBilingualReading(entry, requestedMode = "bilingual") {
  const mode = READING_MODES.some(([value]) => value === requestedMode) ? requestedMode : "bilingual"
  const original = typeof entry?.content === "string" ? entry.content : ""
  if (mode === "original") return { mode, html: original, message: "" }
  const translation = entry?.translation
  const candidate = !translation?.language || translation.language === "zh-CN"
    ? translation?.[mode === "chinese" ? "chinese_html" : "bilingual_html"] : null
  const available = typeof candidate === "string" && candidate.trim().length > 0
  const complete = ["done", "ready", "native"].includes(translation?.status)
  const ineligible = ["native", "ineligible", "not_eligible", "skipped"].includes(translation?.status)
  let message = ""
  if (available && !complete) message = "部分段落已译"
  else if (!available && !ineligible && (translation || isTranslationEligible(entry))) {
    message = !translation || shouldPollTranslation(translation.status)
      ? "译文准备中，先显示原文" : "译文暂不可用，先显示原文"
  }
  return { mode, html: available ? candidate : original, message }
}

// The only write is one explicit demand POST per mounted reading session. Every
// later request is a read-only GET; a transient GET error never repeats the POST.
export function startBilingualTranslation({
  entryId, source, eligible, start, request, isCurrent, onTranslation, onError,
  setTimer = setTimeout, clearTimer = clearTimeout,
}) {
  let stopped = false, timer = null, controller = null
  let status = source?.status, sourceHash = source?.source_hash || null
  const stop = () => {
    stopped = true
    if (timer !== null) clearTimer(timer)
    timer = null
    controller?.abort()
  }
  const current = () => !stopped && isCurrent()
  const accept = payload => {
    const translation = payload?.translation
    if (String(payload?.entry_id) !== String(entryId) || !translation || typeof translation.status !== "string") {
      stop()
      onError?.()
      return false
    }
    // A source hash obtained from the demand response owns every later result.
    if ((sourceHash && translation.source_hash !== sourceHash) ||
        (shouldPollTranslation(translation.status) && !translation.source_hash)) {
      stop()
      onError?.()
      return false
    }
    sourceHash = translation.source_hash || sourceHash
    status = translation.status
    onTranslation(translation)
    return true
  }
  const schedule = () => {
    if (current() && shouldPollTranslation(status)) timer = setTimer(poll, TRANSLATION_POLL_MS)
  }
  const poll = async () => {
    timer = null
    if (!current()) return stop()
    controller = new AbortController()
    try {
      const payload = await request(controller.signal)
      if (!current()) return stop()
      accept(payload)
    } catch {
      // Keep the last readable partial document through temporary GET failures.
    } finally {
      controller = null
      schedule()
    }
  }
  const begin = async () => {
    if (!current()) return stop()
    controller = new AbortController()
    try {
      const payload = await start(controller.signal)
      if (!current()) return stop()
      accept(payload)
    } catch {
      if (current()) onError?.()
      stop()
    } finally {
      controller = null
      schedule()
    }
  }
  if (eligible && !["done", "ready", "native"].includes(status)) void begin()
  return stop
}

export function useBilingualTranslation(entry) {
  const entryId = entry?.id, content = entry?.content, source = entry?.translation
  const eligible = isTranslationEligible(entry)
  const current = useRef(null)
  current.current = { entryId, content, source, eligible }
  const [received, setReceived] = useState(null)
  useEffect(() => {
    let active = true
    const isCurrent = () => active && current.current.entryId === entryId &&
      current.current.content === content && current.current.source === source && current.current.eligible === eligible
    const path = `/v1/ai/translation/${encodeURIComponent(entryId)}`
    const save = translation => setReceived({ entryId, content, source, eligible, translation })
    const stop = startBilingualTranslation({
      entryId, source, eligible, isCurrent,
      start: signal => apiClient.post(path, {}, { signal, retry: 0, timeout: 8000 }),
      request: signal => apiClient.get(path, { signal, retry: 0, timeout: 8000 }),
      onTranslation: save,
      onError: () => setReceived(previous => {
        const owned = previous?.entryId === entryId && previous.content === content && previous.source === source && previous.eligible === eligible
        return { entryId, content, source, eligible, translation: { ...(owned ? previous.translation : source), status: "error" } }
      }),
    })
    return () => { active = false; stop() }
  }, [entryId, content, source, eligible])
  return received?.entryId === entryId && received.content === content && received.source === source && received.eligible === eligible
    ? received.translation : source
}

// A single icon inside the existing ReadingControls bar, alongside its layout
// and focus icons. It does not create another toolbar or alter the reading DOM.
export default function BilingualReading({ mode = "bilingual", message = "", onModeChange }) {
  const popup = useRef(null)
  const selected = READING_MODES.find(([value]) => value === mode) || READING_MODES[0]
  const close = () => {
    if (!popup.current) return
    popup.current.open = false
    popup.current.querySelector("summary")?.focus({ preventScroll: true })
  }
  return (
    <>
      <details className="review-reading-controls reader-bilingual-controls" ref={popup} onKeyDownCapture={event => {
        if (event.key !== "Escape" || event.isComposing || event.nativeEvent?.isComposing || event.keyCode === 229 || !event.currentTarget.open) return
        event.preventDefault()
        event.stopPropagation()
        close()
      }}>
        <summary aria-label={`正文语言：${selected[1]}`} title={`正文语言：${selected[1]}`}>
          <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 5h12M9 3v2M5 5c1 5 4 8 8 10M13 5c-1 5-4 8-9 11M13 21l4-10 4 10M14.5 17h5" />
          </svg>
        </summary>
        <div className="review-reading-options reader-bilingual-options" role="group" aria-label="正文语言">
          {READING_MODES.map(([value, label]) => (
            <button key={value} type="button" aria-pressed={selected[0] === value} onClick={() => { onModeChange?.(value); close() }}>
              {label}
            </button>
          ))}
        </div>
      </details>
      {message && <span className="reader-bilingual-status" role="status">{message}</span>}
    </>
  )
}
