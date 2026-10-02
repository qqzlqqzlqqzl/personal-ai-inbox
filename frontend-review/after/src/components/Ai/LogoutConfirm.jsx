import { Modal } from "@arco-design/web-react"
import { useEffect, useState } from "react"
import { confirmDialogProps, destructiveConfirmButtonProps } from "@/utils/confirm-dialog"
import { clearSession } from "@/utils/session"
import { noteSession } from "@/utils/note-session"
import "./LogoutConfirm.css"

function DraftNotice({ expected, description }) {
  const [, refresh] = useState(0), [backup, setBackup] = useState(""), [status, setStatus] = useState("")
  useEffect(() => noteSession.subscribe(() => refresh(value => value + 1)), [])
  const snapshot = noteSession.inspect(expected)
  if (!snapshot.current) return <p>会话已变化，请重新确认当前账号。</p>
  const exportDrafts = () => {
    const text = noteSession.exportText(expected)
    if (text === null) return
    try {
      const url = URL.createObjectURL(new Blob([text], { type: "application/json;charset=utf-8" }))
      const link = document.createElement("a")
      link.href = url; link.download = "reader-unsynced-notes.json"
      document.body.append(link); link.click(); link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      setStatus("已请求本地下载；请确认文件保存后再退出。")
    } catch {
      setBackup(text); setStatus("下载不可用，请复制下方备份文本后再退出。")
    }
  }
  return <div className="logout-draft-notice">
    <p>{description}</p>
    {snapshot.count > 0 && <p role="status">本标签页还有 {snapshot.count} 篇未同步笔记草稿，包括已经关闭的文章。确认退出会丢弃这些本地草稿。</p>}
    {!snapshot.scopeKnown && <p role="alert">当前账号身份尚未确认，无法判断哪些旧版草稿属于本会话，退出不能保证删除这些本地草稿。可取消后重试身份验证；仍退出会让旧会话失效，但未识别草稿可能留在浏览器中。</p>}
    {snapshot.scopeKnown && !snapshot.storageOK && <p role="alert">浏览器未允许完整读取本地草稿。可先导出当前可用备份，或取消退出；若清理被拒绝，旧会话草稿会失效，但无法保证浏览器物理删除。</p>}
    {snapshot.unreadableCount > 0 && <p role="status">另有无法读取的本地草稿；导出仅包含可读取内容。</p>}
    {snapshot.count > 0 && <button type="button" onClick={exportDrafts}>导出本地草稿</button>}
    {status && <p role="status">{status}</p>}
    {backup && <textarea aria-label="退出前草稿备份文本" readOnly rows={7} value={backup} onFocus={event => event.target.select()} />}
  </div>
}

export function confirmDraftLogout({ title, description, onConfirm }) {
  const expected = noteSession.context()
  if (!expected) return
  const snapshot = noteSession.inspect(expected)
  let modal, unsubscribe = () => {}
  modal = Modal.confirm({
    ...confirmDialogProps,
    className: "note-logout-modal",
    style: { width: "min(520px, calc(100vw - 32px))", maxHeight: "calc(100dvh - 32px)", display: "inline-flex", flexDirection: "column" },
    title: !snapshot.scopeKnown ? "退出前确认草稿清理限制" : snapshot.count || !snapshot.storageOK ? "退出前处理未同步笔记" : title,
    content: <DraftNotice expected={expected} description={description} />,
    okText: !snapshot.scopeKnown ? "仍退出账号" : snapshot.count || !snapshot.storageOK ? "丢弃本地草稿并退出" : "确认退出",
    cancelText: "取消，保留会话",
    okButtonProps: { ...destructiveConfirmButtonProps, status: "danger" },
    onOk: () => {
      const result = clearSession({ expected })
      if (result.retired) onConfirm?.(result)
    },
    afterClose: () => unsubscribe(),
  })
  unsubscribe = noteSession.subscribe(() => {
    if (!noteSession.matchesContext(expected)) { unsubscribe(); modal.close() }
  })
  return modal
}
