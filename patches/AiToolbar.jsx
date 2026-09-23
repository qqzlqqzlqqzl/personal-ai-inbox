import { useStore } from "@nanostores/react"
import { useState, useEffect, useRef } from "react"
import { useNavigate } from "react-router"
import { aiState } from "@/store/aiState"
import { settingsState, updateSettings } from "@/store/settingsState"
import { invalidateArticleList } from "@/store/contentState"
import AiPanel from "./AiPanel"
import apiClient from "@/apis/ofetch"
import "./AiNews.css"

export default function AiToolbar({ source }) {
  const state = useStore(aiState)
  const { orderDirection } = useStore(settingsState, { keys: ["orderDirection"] })
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()
  const [progress,setProgress] = useState(null)
  const completedCards = useRef(null)
  const [updatesAvailable, setUpdatesAvailable] = useState(false)
  useEffect(() => {
    let active = true
    const load = async () => {
      try {
        const needsSettings = !aiState.get().hydrated
        const [c, s] = await Promise.all([
          needsSettings ? apiClient.get("/v1/ai/settings") : Promise.resolve(null),
          apiClient.get("/v1/ai/status"),
        ])
        if (active && c) {
          const current = aiState.get()
          const minimum = Number(c.minimum_score)
          const changed = current.minimum !== minimum
          aiState.set({ ...current, minimum, hydrated: true })
          if (changed) invalidateArticleList()
        }
        if (active) {
          const count = `${s.translations?.counts?.done || 0}:${s.counts?.done || 0}`
          // A background completion must never replace the current reading snapshot.
          if (completedCards.current !== null && completedCards.current !== count) setUpdatesAvailable(true)
          completedCards.current = count
          setProgress(s)
        }
      } catch { /* Native reader remains usable if the add-on is unavailable. */ }
    }
    load(); const timer = setInterval(load,30000)
    return () => { active=false; clearInterval(timer) }
  }, [])
  const change = (value) => {
    aiState.set({ ...aiState.get(), ...value })
    setUpdatesAvailable(false)
    invalidateArticleList()
    if (source !== "all") navigate("/all")
  }
  return <div className="ai-toolbar">
    <div className="ai-modes">{[["all", "全部原始"], ["recommended", "AI 精选"], ["pending", "待处理 / 异常"]].map(([mode, label]) =>
      <button key={mode} aria-pressed={source === "all" && state.mode === mode} onClick={() => change({ mode })}>{label}</button>)}</div>
    {state.mode === "recommended" && source === "all" && <>
      <label>最低分 <select value={state.minimum} onChange={e => change({ minimum: Number(e.target.value) })}>{[0,3,5,6,7,8,9].map(n => <option key={n}>{n}</option>)}</select></label>
      <select aria-label="AI 排序" value={state.sort === "time" ? `time_${orderDirection}` : state.sort} onChange={e => { const value = e.target.value; updateSettings({ orderDirection: value === "time_asc" ? "asc" : "desc" }); change({ sort: value.startsWith("time_") ? "time" : value }) }}><option value="score">推荐优先</option><option value="technical">技术价值</option><option value="business">商业启发</option><option value="time_desc">最新优先</option><option value="time_asc">最旧优先</option></select>
    </>}
    {progress && <span className="ai-progress">已分析 {progress.counts.done || 0} / {Object.values(progress.counts).reduce((a,b)=>a+b,0)} · {progress.analysis_enabled === false ? "分析已暂停" : "后台按预算处理"}{progress.translation_enabled === false ? " · 中文翻译已暂停" : ""}</span>}
    {updatesAvailable && <button className="ai-updates" onClick={() => { setUpdatesAvailable(false); invalidateArticleList() }}>有新内容 / 中文更新 · 点击刷新</button>}
    <button className="ai-settings-button" onClick={() => setOpen(true)}>AI 设置 · 来源 · 工具</button>
    {open && <AiPanel onClose={() => setOpen(false)} />}
  </div>
}
