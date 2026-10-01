import { useEffect, useRef, useState } from "react"

import apiClient from "@/apis/ofetch"
import { contentState, setActiveContent, setEntries } from "@/store/contentState"
import "./AiNews.css"

const MAX_NOTE = 20_000
const SAVE_DELAY_MS = 700

const withNoteMeta = (entry, hasNote, updatedAt) =>
  entry
    ? {
        ...entry,
        ai: {
          ...entry.ai,
          has_note: hasNote,
          note_updated_at: updatedAt || null,
        },
      }
    : entry

const syncNoteMeta = (entryId, hasNote, updatedAt) => {
  const current = contentState.get()
  if (current.activeContent?.id === entryId) {
    setActiveContent(withNoteMeta(current.activeContent, hasNote, updatedAt))
  }
  if (current.entries?.some((entry) => entry.id === entryId)) {
    setEntries(current.entries.map((entry) =>
      entry.id === entryId ? withNoteMeta(entry, hasNote, updatedAt) : entry,
    ))
  }
}

export default function ArticleNote({ entry }) {
  const entryId = entry.id
  const [note, setNote] = useState("")
  const [loaded, setLoaded] = useState(false)
  const [status, setStatus] = useState("正在加载…")
  const noteRef = useRef("")
  const savedRef = useRef("")
  const loadedRef = useRef(false)
  const timerRef = useRef(null)
  const queueRef = useRef(Promise.resolve())

  const enqueueSave = (id, value, keepalive = false, showStatus = true) => {
    if (!loadedRef.current || value === savedRef.current) {return}
    if (showStatus) {setStatus("正在保存…")}
    queueRef.current = queueRef.current
      .catch(() => {})
      .then(() => apiClient.put(`/v1/ai/notes/${id}`, { note: value }, { keepalive, retry: 0 }))
      .then((result) => {
        savedRef.current = value
        syncNoteMeta(id, result.has_note, result.updated_at)
        if (showStatus) {setStatus(result.has_note ? "已保存" : "已清空")}
        return result
      })
      .catch(() => {
        if (showStatus) {setStatus("保存失败 · 内容仍在当前页面")}
        return null
      })
  }

  useEffect(() => {
    let active = true
    loadedRef.current = false
    noteRef.current = ""
    savedRef.current = ""
    apiClient.get(`/v1/ai/notes/${entryId}`, { retry: 0 })
      .then((result) => {
        if (!active) {return}
        const value = result.note || ""
        noteRef.current = value
        savedRef.current = value
        loadedRef.current = true
        setNote(value)
        setLoaded(true)
        setStatus(result.has_note ? "已保存" : "自动保存")
        syncNoteMeta(entryId, result.has_note, result.updated_at)
        return result
      })
      .catch(() => {
        if (!active) {return null}
        setStatus("笔记加载失败")
        return null
      })
    return () => {
      active = false
      globalThis.clearTimeout(timerRef.current)
      if (loadedRef.current && noteRef.current !== savedRef.current) {
        enqueueSave(entryId, noteRef.current, true, false)
      }
    }
  }, [entryId])

  useEffect(() => {
    const onPageHide = () => {
      if (loadedRef.current && noteRef.current !== savedRef.current) {
        enqueueSave(entryId, noteRef.current, true, false)
      }
    }
    globalThis.addEventListener("pagehide", onPageHide)
    return () => globalThis.removeEventListener("pagehide", onPageHide)
  }, [entryId])

  const change = (value) => {
    noteRef.current = value
    setNote(value)
    setStatus("等待自动保存…")
    globalThis.clearTimeout(timerRef.current)
    timerRef.current = globalThis.setTimeout(
      () => enqueueSave(entryId, noteRef.current),
      SAVE_DELAY_MS,
    )
  }

  const saveNow = () => {
    globalThis.clearTimeout(timerRef.current)
    enqueueSave(entryId, noteRef.current)
  }

  return (
    <section aria-label="我的笔记" className="article-note">
      <div className="article-note-head">
        <div>
          <strong>📝 我的笔记</strong>
          <span>仅自己可见 · 跟随账号跨设备同步</span>
        </div>
        <small aria-live="polite">{status}</small>
      </div>
      <textarea
        aria-label="我的笔记"
        disabled={!loaded}
        maxLength={MAX_NOTE}
        placeholder="记下你的判断、可复用思路、和其他文章/项目的关联……"
        rows={5}
        value={note}
        onBlur={saveNow}
        onChange={(event) => change(event.target.value)}
        onKeyDown={(event) => {
          if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
            event.preventDefault()
            saveNow()
          }
        }}
      />
      <div className="article-note-foot">
        <span>{note.length.toLocaleString()} / {MAX_NOTE.toLocaleString()}</span>
        <span>支持换行 · Ctrl/⌘ + S 立即保存</span>
      </div>
    </section>
  )
}
