import { Notification } from "@arco-design/web-react"
import { authState } from "@/store/authState"
import { dataState } from "@/store/dataState"
import isValidAuth, { getAuthSessionKey } from "@/utils/auth"
import { createNoteSessionAuth, createNoteSessionManager } from "@/components/Ai/note-session-core"

export { createNoteSessionAuth }
export const noteSession = createNoteSessionManager({
  auth: authState, data: dataState, authKey: getAuthSessionKey, validAuth: isValidAuth,
  onRetire(result) {
    if (result.ok) return
    Notification.warning({
      title: "本地笔记草稿清理未完成",
      content: result.reason === "unverified_owner"
        ? "尚未确认旧会话账号，无法安全识别并删除其本地草稿。旧会话已失效，但未识别草稿可能仍留在浏览器中。"
        : "浏览器拒绝部分本地草稿清理。旧会话草稿已失效，但无法保证物理删除。",
    })
  },
})
export const getNoteRequestStamp = () => noteSession.requestStamp()
