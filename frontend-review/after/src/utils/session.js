import { authState, resetAuth, setAuth } from "@/store/authState"
import { resetContent } from "@/store/contentState"
import { commitIdentityData, dataState, getDataSessionRevision, resetData, setVerifiedServer } from "@/store/dataState"
import { resetFeedIcons } from "@/store/feedIconsState"
import { articleSnapshots, snapshotOwnerKey } from "@/utils/article-list-snapshot"
import { getAuthSessionKey } from "@/utils/auth"
import { createHomeIdentity } from "@/utils/home-page"
import { createNoteSessionAuth, noteSession } from "@/utils/note-session"

let pendingClear = null

export const isSessionClearing = () => Boolean(pendingClear &&
  pendingClear.authKey === getAuthSessionKey(authState.get()) &&
  pendingClear.revision === getDataSessionRevision())

export const resetSessionData = ({ invalidate = true } = {}) => {
  if (invalidate) noteSession.end()
  resetContent()
  resetData()
  resetFeedIcons()
  if (invalidate) noteSession.resumeData()
}

export const clearSession = async ({ expected } = {}) => {
  const auth = authState.get()
  const authKey = getAuthSessionKey(auth)
  const revision = getDataSessionRevision()
  if (!expected && pendingClear?.authKey === authKey && pendingClear.revision === revision) {
    return pendingClear.promise
  }
  const { currentUser, identityAuthSessionKey } = dataState.get()
  const marker = auth.readerNoteSession
  // A 401 during verification can still retire the previous verified owner.
  // The marker is used for deletion only. Snapshot reads require live identity.
  const ownerId = identityAuthSessionKey === authKey ? currentUser?.id
    : marker?.version === 1 && marker.server === auth.server ? Number(marker.owner) : null
  const owner = snapshotOwnerKey(createHomeIdentity(auth.server, ownerId))
  const result = noteSession.end(expected)
  if (!result.retired) return result
  const clear = async () => {
    await articleSnapshots.removeOwner(owner)
    if (getAuthSessionKey(authState.get()) !== authKey || getDataSessionRevision() !== revision) {
      return { ...result, retired: false }
    }
    resetAuth()
    resetSessionData({ invalidate: false })
    return result
  }
  const promise = clear()
  pendingClear = { authKey, revision, promise }
  try {
    return await promise
  } finally {
    if (pendingClear?.promise === promise) pendingClear = null
  }
}

export const startSession = (auth, serverVersion, currentUser) => {
  noteSession.end()
  resetSessionData({ invalidate: false })
  commitIdentityData(currentUser, getAuthSessionKey(auth))
  setVerifiedServer({ authSessionKey: getAuthSessionKey(auth), version: serverVersion })
  setAuth(createNoteSessionAuth(auth, currentUser))
}
