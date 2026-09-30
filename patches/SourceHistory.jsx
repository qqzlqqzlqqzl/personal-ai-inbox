import { useEffect, useRef, useState } from "react"
import apiClient from "@/apis/ofetch"
import { historyLines } from "./source-history.js"

export default function SourceHistory({ feedId }) {
  const [history, setHistory] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const mounted = useRef(false)
  const inFlight = useRef(false)
  const sequence = useRef(0)
  const controller = useRef(null)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false; sequence.current++; controller.current?.abort() }
  }, [])
  const load = async () => {
    if (inFlight.current) return
    inFlight.current = true
    const request = ++sequence.current
    controller.current = new AbortController()
    setLoading(true); setError(""); setHistory(null)
    try {
      const result = await apiClient.get(`/v1/ai/feeds/${feedId}/history`, { signal: controller.current.signal, retry: 0 })
      if (mounted.current && sequence.current === request) setHistory(result)
    } catch {
      if (mounted.current && sequence.current === request) setError("历史范围查询失败，请重试")
    } finally {
      inFlight.current = false
      if (mounted.current && sequence.current === request) setLoading(false)
    }
  }
  return <details className="ai-source-history" onToggle={event => {
    if (event.currentTarget.open && !history) load()
  }}>
    <summary>历史范围（点击查询）</summary>
    <div aria-live="polite" aria-busy={loading}>
      {loading && <small>正在查询存储与 feed 窗口……</small>}
      {error && <small role="alert">{error}</small>}
      {history && historyLines(history).map(line => <small key={line}>{line}</small>)}
    </div>
    <button type="button" disabled={loading} onClick={load}>刷新历史范围</button>
  </details>
}
