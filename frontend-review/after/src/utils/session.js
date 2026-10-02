import { Notification } from "@arco-design/web-react"
import { resetAuth, setAuth } from "@/store/authState"
import { resetContent } from "@/store/contentState"
import { commitIdentityData, resetData, setVerifiedServer } from "@/store/dataState"
import { resetFeedIcons } from "@/store/feedIconsState"
import { getAuthSessionKey } from "@/utils/auth"
import { createNoteSessionAuth, noteSession } from "@/utils/note-session"

export const resetSessionData = ({ invalidate = true } = {}) => {
  if (invalidate) noteSession.end()
  resetContent()
  resetData()
  resetFeedIcons()
  if (invalidate) noteSession.resumeData()
}

export const clearSession = ({ expected } = {}) => {
  const result = noteSession.end(expected)
  if (!result.retired) return result
  resetAuth()
  resetSessionData({ invalidate: false })
  if (!result.ok) Notification.warning({ title: "已退出账号", content: "浏览器拒绝部分本地草稿清理。旧会话草稿已失效，但无法保证物理删除。" })
  return result
}

export const startSession = (auth, serverVersion, currentUser) => {
  noteSession.end()
  resetSessionData({ invalidate: false })
  commitIdentityData(currentUser, getAuthSessionKey(auth))
  setVerifiedServer({ authSessionKey: getAuthSessionKey(auth), version: serverVersion })
  setAuth(createNoteSessionAuth(auth, currentUser))
}
