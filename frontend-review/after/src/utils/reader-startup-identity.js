let pendingIdentity = null

export function preloadReaderIdentity(authKey, revision, request) {
  // The version gate can decline to open the authenticated app.
  void request.catch(() => {})
  const pending = { authKey, revision, request }
  pendingIdentity = pending
  return () => {
    if (pendingIdentity === pending) pendingIdentity = null
  }
}

export function takeReaderIdentity(authKey, revision) {
  if (!pendingIdentity || pendingIdentity.authKey !== authKey || pendingIdentity.revision !== revision) return null
  const { request } = pendingIdentity
  pendingIdentity = null
  return request
}
