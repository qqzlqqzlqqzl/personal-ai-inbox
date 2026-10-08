// Bind detail hydration to the current reader activation, route and data session.
import { useCallback, useEffect, useLayoutEffect, useRef } from 'react'
import { useStore } from '@nanostores/react'
import { getEntry } from '@/apis'
import apiClient from '@/apis/ofetch'
import { contentState, setActiveContent, setIsArticleLoading } from '@/store/contentState'
import { dataState, getDataSessionRevision } from '@/store/dataState'
import { authState } from '@/store/authState'
import { getAuthSessionKey } from '@/utils/auth'
import prepareEntry from '@/utils/entry-presentation'

let closeIntentRevision = 0
const activeDetailRequests = new Set()
export const needsReaderBodyCheck = entry =>
  entry?.ai?.body_completeness?.policy_version === 'reader-body-completeness-v1' &&
  entry.ai.body_completeness.status !== 'verified'

// Notes and other presentation metadata clone the DTO. They do not create a
// new body version; a different account, native identity or body does.
const sameReaderBody = (left, right) => left === right || Boolean(left && right &&
  left.id === right.id && left.user_id === right.user_id && left.feed?.id === right.feed?.id &&
  left.url === right.url && left.title === right.title && left.content === right.content &&
  left.content_deferred === right.content_deferred && left.prepared_source === right.prepared_source)

export function invalidateReaderEntryDetail() {
  closeIntentRevision += 1
  for (const controller of activeDetailRequests) controller.abort()
  activeDetailRequests.clear()
  setIsArticleLoading(false)
}

export default function useReaderEntryDetail({ entryId, source, sourceId, activeContent, entryRequestIdRef, restoreEntryListFocus, loadArticleDetail }) {
  const { sessionRevision } = useStore(dataState, { keys: ['sessionRevision'] })
  const routeKey = JSON.stringify([source, sourceId ?? null, entryId ?? null])
  const currentRoute = useRef(routeKey)
  const previousRoute = useRef(null)
  const pendingOwner = useRef(null)
  const bodyCheckedFor = useRef(null)
  const selection = useRef({ generation: 0, active: contentState.get().activeContent })
  const cancelPending = useCallback(() => {
    const owner = pendingOwner.current
    if (!owner) return
    pendingOwner.current = null
    activeDetailRequests.delete(owner.controller)
    owner.controller.abort()
  }, [])
  useLayoutEffect(() => {
    if (currentRoute.current !== routeKey) cancelPending()
    currentRoute.current = routeKey
  }, [routeKey, cancelPending])

  useEffect(() => {
    const stop = contentState.listen(({ activeContent: next }) => {
      const previous = selection.current.active
      if (next !== previous) {
        if (!next || !previous || next.id !== previous.id) selection.current.generation += 1
        selection.current.active = next
        if (pendingOwner.current && !pendingOwner.current.isCurrent()) cancelPending()
      }
    })
    const cancelStale = () => {
      if (pendingOwner.current && !pendingOwner.current.isCurrent()) cancelPending()
    }
    const stopAuth = authState.listen(cancelStale)
    const stopSession = dataState.listen(cancelStale)
    return () => {
      stop()
      stopAuth()
      stopSession()
      cancelPending()
      entryRequestIdRef.current += 1
      previousRoute.current = null
      pendingOwner.current = null
    }
  }, [entryRequestIdRef, cancelPending])

  const fetchSingleEntry = useCallback(async (requestedId) => {
    cancelPending()
    const controller = new AbortController()
    const requestId = ++entryRequestIdRef.current
    const requestRoute = currentRoute.current
    const requestSession = getDataSessionRevision()
    const requestAuth = getAuthSessionKey(authState.get())
    const requestGeneration = selection.current.generation
    const requestCloseIntent = closeIntentRevision
    let requestActive = contentState.get().activeContent
    const isCurrent = () => !controller.signal.aborted && entryRequestIdRef.current === requestId &&
      currentRoute.current === requestRoute && getDataSessionRevision() === requestSession &&
      getAuthSessionKey(authState.get()) === requestAuth &&
      selection.current.generation === requestGeneration && closeIntentRevision === requestCloseIntent &&
      sameReaderBody(contentState.get().activeContent, requestActive)
    pendingOwner.current = { requestId, isCurrent, controller }
    const numericId = Number(requestedId)
    const existing = requestActive?.id === numericId && !requestActive.content_deferred ? requestActive :
      contentState.get().entries.find(entry => entry.id === numericId && !entry.content_deferred)
    if (existing && !existing.content_deferred && !needsReaderBodyCheck(existing)) {
      pendingOwner.current = null
      setIsArticleLoading(false)
      setActiveContent(existing)
      return
    }
    activeDetailRequests.add(controller)
    try {
      if (!existing) setIsArticleLoading(true)
      // Keep the loading view until both independently started resources settle.
      // The shared import promise stays rejected for React.lazy/ErrorBoundary.
      const moduleSettled = Promise.resolve().then(loadArticleDetail).then(() => {}, () => {})
      let [entry] = await Promise.all([
        existing || getEntry(requestedId, { signal: controller.signal }), moduleSettled,
      ])
      if (!isCurrent()) return
      if (entry?.id !== numericId || entry.content_deferred) throw new Error('Unexpected detail identity')
      const prepared = prepareEntry(entry)
      if (!isCurrent()) return
      const checkedFor = bodyCheckedFor.current
      const needsCheck = needsReaderBodyCheck(prepared) && !(checkedFor?.auth === requestAuth &&
        checkedFor.session === requestSession && sameReaderBody(checkedFor.entry, prepared))
      // Publish existing body/cached translation as soon as GET + module are
      // ready. The explicitly unverified state remains visible during the POST.
      requestActive = prepared
      // A null-active deep link becoming readable is this owner's publication,
      // not a user selection change that should retire its background check.
      selection.current.active = prepared
      if (!needsCheck) {
        pendingOwner.current = null
        activeDetailRequests.delete(controller)
      }
      contentState.set({ ...contentState.get(), activeContent: prepared, isArticleLoading: false })
      if (needsCheck && isCurrent()) {
        try {
          // Only this owned, activated detail path verifies one original. Hover,
          // list GETs and prefetch do not call this non-model POST.
          const checked = await apiClient.post(`/v1/ai/body/${encodeURIComponent(requestedId)}/verify`, {},
            { signal: controller.signal, retry: 0, timeout: 18000 })
          if (checked?.id !== numericId || checked.content_deferred || checked.user_id !== entry.user_id ||
              checked.feed?.id !== entry.feed?.id || checked.url !== entry.url) {
            throw new Error('Unexpected verified body identity')
          }
          entry = checked
        } catch (error) {
          if (controller.signal.aborted || !isCurrent()) return
          // Keep the original read available, with its explicit unverified state.
          entry = contentState.get().activeContent
          entry = { ...entry, ai: { ...entry.ai, body_completeness: {
            ...entry.ai.body_completeness, reason: 'original_check_failed', checked_at: Date.now() / 1000,
          } } }
        }
        if (!isCurrent()) return
        // Verification owns the body, not user actions made while it was slow.
        const latest = contentState.get().activeContent
        entry = { ...entry, ai: { ...entry.ai } }
        for (const field of ['status', 'starred']) {
          if (latest[field] !== prepared[field]) entry[field] = latest[field]
        }
        for (const field of ['has_note', 'note_updated_at']) {
          if (latest.ai?.[field] !== prepared.ai?.[field]) entry.ai[field] = latest.ai?.[field]
        }
        const checked = prepareEntry(entry)
        if (!isCurrent()) return
        // Detach the completed owner before notifying content observers.
        pendingOwner.current = null
        bodyCheckedFor.current = { auth: requestAuth, session: requestSession, entry: checked }
        activeDetailRequests.delete(controller)
        requestActive = checked
        contentState.set({ ...contentState.get(), activeContent: checked, isArticleLoading: false })
      }
    } catch (error) {
      if (isCurrent()) console.error('Failed to fetch entry:', error)
    } finally {
      activeDetailRequests.delete(controller)
      if (isCurrent()) setIsArticleLoading(false)
      if (pendingOwner.current?.requestId === requestId) pendingOwner.current = null
    }
  }, [entryRequestIdRef, cancelPending, loadArticleDetail])

  useEffect(() => {
    const routeChanged = previousRoute.current !== routeKey
    previousRoute.current = routeKey
    const current = contentState.get().activeContent
    if (!entryId) {
      cancelPending()
      entryRequestIdRef.current += 1
      if (routeChanged && current) {
        setActiveContent(null)
        restoreEntryListFocus(current.id)
      }
      setIsArticleLoading(false)
      return
    }
    if (!current && !routeChanged) {
      // A route/source change can clear the old DTO before its legitimate
      // null-active request renders. Preserve only that exact pending owner.
      if (pendingOwner.current?.isCurrent()) return
      // The user can close while React Router still has the detail URL.
      cancelPending()
      entryRequestIdRef.current += 1
      setIsArticleLoading(false)
      return
    }
    if (current?.id !== Number(entryId) && !routeChanged) return
    if (pendingOwner.current?.isCurrent()) return
    const checkedFor = bodyCheckedFor.current
    if (current?.id === Number(entryId) && !current.content_deferred &&
        (!needsReaderBodyCheck(current) || (checkedFor?.auth === getAuthSessionKey(authState.get()) &&
          checkedFor.session === getDataSessionRevision() && sameReaderBody(checkedFor.entry, current)))) return
    void fetchSingleEntry(entryId)
  }, [entryId, routeKey, activeContent, sessionRevision, fetchSingleEntry, entryRequestIdRef, restoreEntryListFocus, cancelPending])
}
