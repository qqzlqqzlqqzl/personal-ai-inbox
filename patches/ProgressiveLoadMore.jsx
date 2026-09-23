import { Button, Spin } from "@arco-design/web-react"
import { useStore } from "@nanostores/react"
import { useEffect, useMemo, useRef, useState } from "react"
import useLoadMore from "@/hooks/useLoadMore"
import { contentState, filteredEntriesState } from "@/store/contentState"
import { nextWindow, prefetchDecision } from "@/utils/reading-session"

/** Append at the first quarter of each received batch, at most one batch ahead. */
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
  const inFlightRef = useRef(false)
  const aliveRef = useRef(true)
  const latest = useRef(null)
  const [settled, setSettled] = useState(0)
  latest.current = { isArticleListReady, loadMoreVisible, loadingMore, loadMoreError, snapshot, indexes, handleLoadMore, getEntries }

  useEffect(() => { aliveRef.current = true; return () => { aliveRef.current = false } }, [])

  useEffect(() => {
    if (!isArticleListReady) return
    const old = windowRef.current
    windowRef.current = nextWindow(old, snapshot, entries.length, articleListOffset)
    if (!old || old.snapshot !== snapshot) attemptRef.current = null
  }, [snapshot, entries.length, articleListOffset, isArticleListReady])

  const request = async (manual = false, observation = null) => {
    const s = latest.current
    if (!s.isArticleListReady || !s.loadMoreVisible || s.loadingMore || inFlightRef.current) return
    if (!manual && s.loadMoreError) return
    const w = windowRef.current
    const key = `${s.snapshot}:${w?.count}:${w?.cursor}`
    if (!manual && attemptRef.current === key) return
    attemptRef.current = key
    inFlightRef.current = true
    if (observation) window.dispatchEvent(new CustomEvent("inbox:prefetch", { detail: { ...observation, count: w.count, start: w.start, size: w.size, cursor: w.cursor, scope: s.snapshot } }))
    try { await s.handleLoadMore(s.getEntries) }
    finally { inFlightRef.current = false; if (aliveRef.current) setSettled(v => v + 1) }
  }
  const requestRef = useRef(request)
  requestRef.current = request

  useEffect(() => {
    if (!isArticleListReady) return
    let frame = 0, stopped = false, root = null
    const check = () => {
      frame = 0
      if (stopped || !root) return
      const s = latest.current
      if (!s.isArticleListReady || !s.loadMoreVisible || s.loadingMore || s.loadMoreError || inFlightRef.current) return
      const bounds = root.getBoundingClientRect()
      const first = [...root.querySelectorAll('[data-entry-id]')].find(el => el.getBoundingClientRect().bottom > bounds.top + 1 && el.getBoundingClientRect().top < bounds.bottom)
      const index = first ? s.indexes.get(first.dataset.entryId) : -1
      if (index === undefined || index < 0) return
      const remaining = root.scrollHeight - root.scrollTop - root.clientHeight
      const row = first.closest('.article-card-grid-row') || first
      const columns = row.classList.contains('article-card-grid-row') ? row.children.length : 1
      const rect = row.getBoundingClientRect()
      const progress = index + Math.max(0, Math.min(1, (bounds.top - rect.top) / Math.max(1, rect.height))) * columns
      const decision = prefetchDecision(windowRef.current, progress, remaining, root.clientHeight)
      if (decision) void requestRef.current(false, { ...decision, index: Number(progress.toFixed(2)), remaining: Math.round(remaining), scrollTop: Math.round(root.scrollTop) })
    }
    const schedule = () => { if (!frame) frame = requestAnimationFrame(check) }
    const connect = () => {
      if (stopped) return
      root = scrollRootRef.current
      if (!root) { frame = requestAnimationFrame(connect); return }
      frame = 0
      root.addEventListener('scroll', schedule, { passive: true })
      schedule()
    }
    connect()
    return () => { stopped = true; cancelAnimationFrame(frame); root?.removeEventListener('scroll', schedule) }
  }, [isArticleListReady, snapshot, entries.length, articleListOffset, loadingMore, loadMoreError, loadMoreVisible, settled, scrollRootRef])

  if (!isArticleListReady) return null
  return <div className="load-more-container" role="status" aria-live="polite" data-prefetch-fraction="0.25" data-loaded-count={entries.length} data-more={String(loadMoreVisible)} data-cursor={articleListOffset}>
    {!loadMoreVisible ? (entries.length ? "已加载全部条目" : null) : loadMoreError ? <Button size="small" onClick={() => void request(true)}>加载失败，点击重试</Button>
      : loadingMore ? <><Spin style={{ paddingRight: 10 }} />正在提前加载下一批…</>
      : <Button size="small" onClick={() => void request(true)}>继续滚动自动预加载 · 也可点击加载</Button>}
  </div>
}
