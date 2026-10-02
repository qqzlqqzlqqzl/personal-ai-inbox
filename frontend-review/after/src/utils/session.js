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
  return result
}

export const startSession = (auth, serverVersion, currentUser) => {
  noteSession.end()
  resetSessionData({ invalidate: false })
  commitIdentityData(currentUser, getAuthSessionKey(auth))
  setVerifiedServer({ authSessionKey: getAuthSessionKey(auth), version: serverVersion })
  setAuth(createNoteSessionAuth(auth, currentUser))
}
