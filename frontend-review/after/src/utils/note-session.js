import { authState } from "@/store/authState"
import { dataState } from "@/store/dataState"
import isValidAuth, { getAuthSessionKey } from "@/utils/auth"
import { createNoteSessionAuth, createNoteSessionManager } from "@/components/Ai/note-session-core"

export { createNoteSessionAuth }
export const noteSession = createNoteSessionManager({
  auth: authState, data: dataState, authKey: getAuthSessionKey, validAuth: isValidAuth,
})
export const getNoteRequestStamp = () => noteSession.requestStamp()
