import { useStore } from "@nanostores/react"
import { useEffect, useRef, useState } from "react"

import AiPanel from "./AiPanel"

import apiClient from "@/apis/ofetch"
import { aiState } from "@/store/aiState"
import { invalidateArticleList } from "@/store/contentState"
import "./AiNews.css"

export default function AiToolbar() {
  const state = useStore(aiState)
  const [open, setOpen] = useState(false)
  const [progress,setProgress] = useState(null)
  const completedCards = useRef(null)
  const minimumDirty = useRef(false)
  const saveChain = useRef(Promise.resolve())
  const [saveStatus, setSaveStatus] = useState("")
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
        if (active && c && !minimumDirty.current) {
          const current = aiState.get()
          const minimum = Number(c.minimum_score)
          const changed = current.minimum !== minimum
          aiState.set({ ...current, minimum, hydrated: true })
          if (changed) invalidateArticleList()
        }
        if (active) {
          const count = `${s.translations?.counts?.done || 0}:${s.counts?.done || 0}`
          if (completedCards.current !== null && completedCards.current !== count) {
            setUpdatesAvailable(true)
          }
          completedCards.current = count
          setProgress(s)
        }
      } catch { /* Native reader remains usable if the add-on is unavailable. */ }
    }
    load()
    const timer = setInterval(load,30_000)
    return () => { active=false; clearInterval(timer) }
  }, [])

  const change = (value) => {
    aiState.set({ ...aiState.get(), ...value })
    if (Object.hasOwn(value, "minimum")) {
      minimumDirty.current = true
      setSaveStatus("正在保存…")
      saveChain.current = saveChain.current.catch(() => {}).then(() =>
        apiClient.put("/v1/ai/settings", { minimum_score: value.minimum })
      ).then(() => setSaveStatus("已保存"), () =>
        setSaveStatus("保存失败，请重试；服务器设置未更新"))
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
    {progress && <span className="ai-progress">
      已分析 {progress.counts.done || 0} / {Object.values(progress.counts).reduce((a,b)=>a+b,0)}
      {" · "}{progress.kaggle?.enabled ? "Kaggle 持续增量处理" : "处理已暂停"}
    </span>}
    {updatesAvailable && <button className="ai-updates" onClick={() => {
      setUpdatesAvailable(false)
      invalidateArticleList()
    }}>有新内容 / 中文更新 · 点击刷新</button>}
    <button className="ai-settings-button" onClick={() => setOpen(true)}>AI 设置 · 来源</button>
    {open && <AiPanel onClose={() => setOpen(false)} />}
  </div>
}
