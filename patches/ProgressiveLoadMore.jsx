import { Button, Spin } from "@arco-design/web-react"
import { useStore } from "@nanostores/react"
import { useEffect, useMemo, useRef, useState } from "react"
import useLoadMore from "@/hooks/useLoadMore"
import { contentState, filteredEntriesState } from "@/store/contentState"
import { nextWindow, prefetchDecision, autoPrefetchDecision, scheduleAutoPrefetch, AUTO_PREFETCH_PAGES } from "@/utils/reading-session"
import { createThumbnailPreloader } from "@/components/Article/reader-image-variants"

/** Warm five additional pages, then retain the existing scroll/manual policy. */
export default function ProgressiveLoadMore({ getEntries, scrollRootRef }) {
  const { isArticleListReady, loadMoreVisible, articleListSnapshotRevision, articleListOffset, infoFrom, infoId } = useStore(contentState, {
    keys: ["isArticleListReady", "loadMoreVisible", "articleListSnapshotRevision", "articleListOffset", "infoFrom", "infoId"],
  })
  const entries = useStore(filteredEntriesState)
  const { handleLoadMore, loadMoreError, loadingMore } = useLoadMore()
  const indexes = useMemo(() => new Map(entries.map((e, i) => [String(e.id), i])), [entries])
  const snapshot = `${infoFrom}:${infoId}:${articleListSnapshotRevision}`
  const windowRef = useRef(null)
  const attemptRef = useRef(null)
  const autoRef = useRef({ snapshot: null, started: 0 })
  const offeredCoversRef = useRef({ snapshot: null, count: 0 })
  const inFlightRef = useRef(false)
  const aliveRef = useRef(true)
  const latest = useRef(null)
  const thumbnailsRef = useRef(null)
  const imageSettledRef = useRef(null)
  const [settled, setSettled] = useState(0)
  latest.current = { isArticleListReady, loadMoreVisible, loadingMore, loadMoreError, snapshot, indexes, entries, handleLoadMore, getEntries }

  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false } }, [])
  useEffect(() => {
    thumbnailsRef.current = createThumbnailPreloader(() => new Image(), () => imageSettledRef.current?.())
    return () => { thumbnailsRef.current.dispose(); thumbnailsRef.current = null }
  }, [])

  useEffect(() => {
    if (!isArticleListReady || offeredCoversRef.current.snapshot !== snapshot) {
      thumbnailsRef.current?.reset()
      offeredCoversRef.current = { snapshot, count: 0 }
    }
  }, [isArticleListReady, snapshot])

  useEffect(() => {
    if (!isArticleListReady) return
    const old = windowRef.current
    windowRef.current = nextWindow(old, snapshot, entries.length, articleListOffset)
    if (!old || old.snapshot !== snapshot) attemptRef.current = null
    if (autoRef.current.snapshot !== snapshot) autoRef.current = { snapshot, started: 0 }
  }, [snapshot, entries.length, articleListOffset, isArticleListReady])

  const request = async (manual = false, observation = null) => {
    const s = latest.current
    if (!s.isArticleListReady || !s.loadMoreVisible || s.loadingMore || inFlightRef.current) return
    if (!manual && s.loadMoreError) return
    const w = windowRef.current
    const key = `${s.snapshot}:${w?.count}:${w?.cursor}`
    if (!manual && attemptRef.current === key) return
    if (observation?.reason === "startup") {
      if (!autoPrefetchDecision(w, autoRef.current.started)) return
      autoRef.current.started++
    }
    attemptRef.current = key
    inFlightRef.current = true
    if (observation) window.dispatchEvent(new CustomEvent("inbox:prefetch", { detail: { ...observation, count: w.count, start: w.start, size: w.size, cursor: w.cursor, scope: s.snapshot } }))
    try { await s.handleLoadMore(s.getEntries, { prefetch: observation?.reason === "startup" }) }
    finally { inFlightRef.current = false; if (aliveRef.current) setSettled(v => v + 1) }
  }
  const requestRef = useRef(request)
  requestRef.current = request

  useEffect(() => {
    if (!isArticleListReady || !loadMoreVisible || loadingMore || loadMoreError || inFlightRef.current) return
    const decision = autoPrefetchDecision(windowRef.current, autoRef.current.started)
    if (decision) return scheduleAutoPrefetch(() => {
      if (latest.current.snapshot === snapshot) void requestRef.current(false, decision)
    })
  }, [isArticleListReady, snapshot, entries.length, articleListOffset, loadingMore, loadMoreError, loadMoreVisible, settled])

  useEffect(() => {
    if (!isArticleListReady) return
    let frame = 0, stopped = false, root = null
    let coverObserver = null, coverFrame = 0, coverTimer = 0, coverWaitFinished = false
    const stopCoverWait = () => {
      coverWaitFinished = true
      coverObserver?.disconnect()
      coverObserver = null
      window.clearTimeout(coverTimer)
      coverTimer = 0
      cancelAnimationFrame(coverFrame)
      coverFrame = 0
    }
    const offerCovers = () => {
      if (stopped || !root) return false
      const s = latest.current
      const offered = offeredCoversRef.current
      if (!s.isArticleListReady || s.snapshot !== snapshot || offered.snapshot !== s.snapshot) return false
      if (offered.count >= s.entries.length) { offered.count = s.entries.length; stopCoverWait(); return true }
      const cover = root.querySelector('.grid-card-cover, .grid-card-media')
      const width = cover?.getBoundingClientRect().width
      if (!(width > 0)) return false
      const start = Math.min(offered.count, s.entries.length)
      thumbnailsRef.current?.enqueue(s.entries.slice(start), width, window.location.origin, window.devicePixelRatio)
      offered.count = s.entries.length
      stopCoverWait()
      return true
    }
    const waitForCovers = () => {
      if (coverWaitFinished || coverObserver || latest.current.snapshot !== snapshot
          || !latest.current.entries.length || typeof window.MutationObserver !== 'function') return
      // Virtual rows may mount after the first check. This retry only warms
      // images; it cannot trigger another page or observe indefinitely.
      coverObserver = new window.MutationObserver(() => {
        if (stopped || coverWaitFinished || coverFrame) return
        coverFrame = requestAnimationFrame(() => { coverFrame = 0; offerCovers() })
      })
      coverObserver.observe(root, { childList: true, subtree: true })
      coverTimer = window.setTimeout(stopCoverWait, 2000)
    }
    const check = () => {
      frame = 0
      if (stopped || !root) return
      const s = latest.current
      if (!s.isArticleListReady) return
      const bounds = root.getBoundingClientRect()
      const visible = [...root.querySelectorAll('[data-entry-id]')].filter(el => el.getBoundingClientRect().bottom > bounds.top + 1 && el.getBoundingClientRect().top < bounds.bottom)
      const first = visible[0]
      const index = first ? (s.indexes.get(first.dataset.entryId) ?? -1) : -1
      if (!offerCovers()) waitForCovers()
      if (!s.loadMoreVisible || s.loadingMore || s.loadMoreError || inFlightRef.current) return
      // Mounted short lists also look near their tail before any scrolling.
      // At the top, only the double-RAF startup queue may fetch its five pages;
      // mount/image checks must not bypass that budget or its read-only flag.
      if (root.scrollTop <= 0) return
      const remaining = root.scrollHeight - root.scrollTop - root.clientHeight
      let progress = -1
      if (index >= 0) {
        const row = first.closest('.article-card-grid-row') || first
        const columns = row.classList.contains('article-card-grid-row') ? row.children.length : 1
        const rect = row.getBoundingClientRect()
        progress = index + Math.max(0, Math.min(1, (bounds.top - rect.top) / Math.max(1, rect.height))) * columns
      }
      const decision = prefetchDecision(windowRef.current, progress, remaining, root.clientHeight)
      if (decision) void requestRef.current(false, { ...decision, index: Number(progress.toFixed(2)), remaining: Math.round(remaining), scrollTop: Math.round(root.scrollTop) })
    }
    const schedule = () => { if (!stopped && !frame) frame = requestAnimationFrame(check) }
    imageSettledRef.current = schedule
    const connect = () => {
      if (stopped) return
      root = scrollRootRef.current
      if (!root) { frame = requestAnimationFrame(connect); return }
      frame = 0
      root.addEventListener('scroll', schedule, { passive: true })
      schedule()
    }
    connect()
    return () => { stopped = true; stopCoverWait(); cancelAnimationFrame(frame); root?.removeEventListener('scroll', schedule); if (imageSettledRef.current === schedule) imageSettledRef.current = null }
  }, [isArticleListReady, snapshot, entries.length, articleListOffset, loadingMore, loadMoreError, loadMoreVisible, settled, scrollRootRef])

  if (!isArticleListReady) return null
  return <div className="load-more-container" role="status" aria-live="polite" data-prefetch-fraction="0.25" data-loaded-count={entries.length} data-more={String(loadMoreVisible)} data-cursor={articleListOffset}>
    {!loadMoreVisible ? (entries.length ? "已加载全部条目" : null) : loadMoreError ? <Button size="small" onClick={() => void request(true)}>加载失败，点击重试</Button>
      : loadingMore ? <><Spin style={{ paddingRight: 10 }} />正在提前加载下一批…</>
      : <Button size="small" onClick={() => void request(true)}>已自动预加载前方最多 {AUTO_PREFETCH_PAGES} 页 · 继续滚动或点击加载</Button>}
  </div>
}
