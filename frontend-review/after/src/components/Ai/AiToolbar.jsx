import { useStore } from "@nanostores/react"
import { useEffect, useRef, useState } from "react"

import AiPanel from "./AiPanel"
import { kaggleStatus } from "./kaggle-status"
import NavigationPalette from "./NavigationPalette"

import apiClient from "@/apis/ofetch"
import { aiState } from "@/store/aiState"
import { invalidateArticleList } from "@/store/contentState"
import "./AiNews.css"

export default function AiToolbar() {
  const state = useStore(aiState)
  const [open, setOpen] = useState(false)
  const [progress,setProgress] = useState(null)
  const [statusUnavailable, setStatusUnavailable] = useState(false)
  const [statusLoading, setStatusLoading] = useState(true)
  const [statusSampled, setStatusSampled] = useState(null)
  const retryStatus = useRef(() => {})
  const completedCards = useRef(null)
  const minimumDirty = useRef(false)
  const saveChain = useRef(Promise.resolve())
  const latestSave = useRef(0)
  const mounted = useRef(false)
  const [saveStatus, setSaveStatus] = useState("")
  const [updatesAvailable, setUpdatesAvailable] = useState(false)

  useEffect(() => {
    mounted.current=true
    let active = true
    let pending=null
    const load = async () => {
      if(document.hidden||pending)return
      const controller=new AbortController();pending=controller
      setStatusLoading(true)
      const current=()=>active&&pending===controller&&!controller.signal.aborted
      try {
        const needsSettings = !aiState.get().hydrated
        const outcomes = await Promise.allSettled([
          needsSettings ? apiClient.get("/v1/ai/settings",{retry:0,timeout:15000,signal:controller.signal}) : Promise.resolve(null),
          apiClient.get("/v1/ai/status",{retry:0,timeout:15000,signal:controller.signal}),
        ])
        const c=outcomes[0].status==='fulfilled'?outcomes[0].value:null,s=outcomes[1].status==='fulfilled'?outcomes[1].value:null
        if (current()) setStatusUnavailable(!s)
        if (current() && c && !minimumDirty.current) {
          const current = aiState.get()
          const minimum = Number(c.minimum_score)
          const changed = current.minimum !== minimum
          aiState.set({ ...current, minimum, hydrated: true })
          if (changed) invalidateArticleList()
        }
        if (current() && s) {
          const count = `${s.translations?.counts?.done || 0}:${s.counts?.done || 0}`
          if (completedCards.current !== null && completedCards.current !== count) {
            setUpdatesAvailable(true)
          }
          completedCards.current = count
          setProgress(s);setStatusSampled(Date.now())
        }
      } catch { /* Native reader remains usable if the add-on is unavailable. */ }
      finally {if(current()){pending=null;setStatusLoading(false)}}
    }
    retryStatus.current=load
    load()
    const timer = setInterval(load,30_000)
    const visible=()=>{if(document.hidden){pending?.abort();pending=null;setStatusLoading(false)}else load()}
    document.addEventListener('visibilitychange',visible)
    return () => { active=false;mounted.current=false;pending?.abort();pending=null;retryStatus.current=()=>{}; clearInterval(timer);document.removeEventListener('visibilitychange',visible) }
  }, [])

  const change = (value) => {
    aiState.set({ ...aiState.get(), ...value })
    if (Object.hasOwn(value, "minimum")) {
      minimumDirty.current = true
      const ticket=++latestSave.current
      setSaveStatus("正在保存…")
      saveChain.current = saveChain.current.catch(() => {}).then(() =>
        mounted.current?apiClient.put("/v1/ai/settings", { minimum_score: value.minimum },{retry:0,timeout:15000}):undefined
      ).then(() => {if(mounted.current&&ticket===latestSave.current)setSaveStatus("已保存")}, () => {
        if(mounted.current&&ticket===latestSave.current)setSaveStatus("保存结果未确认，请重试或查看服务器设置")})
    }
    setUpdatesAvailable(false)
    invalidateArticleList()
  }

  const changePrimary = (mode) => {
    const current = aiState.get()
    change({
      mode,
      auxiliary: current.auxiliary === "pending" ? "none" : current.auxiliary,
      sort: current.sort === "note_updated" && current.auxiliary !== "notes"
        ? (mode === "recommended" ? "score" : "time")
        : current.sort,
    })
  }

  const toggleAuxiliary = (auxiliary) => {
    const current = aiState.get()
    const next = current.auxiliary === auxiliary ? "none" : auxiliary
    change({
      auxiliary: next,
      ...(next !== "notes" && current.sort === "note_updated"
        ? { sort: current.mode === "recommended" ? "score" : "time" }
        : {}),
    })
  }

  return <div className="ai-toolbar">
    <div className="ai-modes" aria-label="阅读方式">
      {[["all", "全部原始"], ["recommended", "AI 精选"]].map(([mode, label]) =>
        <button
          key={mode}
          aria-pressed={state.auxiliary !== "pending" && state.mode === mode}
          onClick={() => changePrimary(mode)}
        >{label}</button>)}
    </div>
    <div className="ai-aux-modes" aria-label="辅助筛选">
      <button
        aria-pressed={state.auxiliary === "notes"}
        onClick={() => toggleAuxiliary("notes")}
      >有笔记</button>
      <button
        aria-pressed={state.auxiliary === "pending"}
        onClick={() => toggleAuxiliary("pending")}
      >待处理 / 异常</button>
    </div>
    {state.mode === "recommended" && state.auxiliary !== "pending" && <>
      <label>最低分 <select value={state.minimum} onChange={e => change({ minimum: Number(e.target.value) })}>
        {[0,3,5,6,7,8,9].map(n => <option key={n}>{n}</option>)}
      </select></label>
    </>}
    {saveStatus && <span role="status">{saveStatus}</span>}
    {!progress && <span role="status" className="ai-progress">{statusLoading?"正在读取资源状态…":"尚未取得资源状态"}{statusUnavailable&&" · 读取失败，当前状态未确认"}</span>}
    {statusUnavailable && <button disabled={statusLoading} onClick={()=>retryStatus.current()}>重试资源状态</button>}
    {progress && <span className="ai-progress" data-sampled-at={statusSampled||''}>
      已分析 {progress.counts?.done || 0} / {Object.values(progress.counts||{}).reduce((a,b)=>a+b,0)}
      {" · "}{statusUnavailable ? "Kaggle 状态暂不可读 · 显示上次快照" : kaggleStatus(progress.kaggle).text}
      {statusSampled&&` · 上次成功读取 ${new Date(statusSampled).toLocaleTimeString()}`}{statusLoading&&" · 正在读取，显示上次快照"}
    </span>}
    {updatesAvailable && <button className="ai-updates" onClick={() => {
      setUpdatesAvailable(false)
      invalidateArticleList()
    }}>有新内容 / 中文更新 · 点击刷新</button>}
    <button className="ai-settings-button" onClick={() => setOpen(true)}>AI 设置 · 来源</button>
    <NavigationPalette onConsole={() => setOpen(true)} />
    {open && <AiPanel onClose={() => setOpen(false)} />}
  </div>
}
