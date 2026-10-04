// Bind detail hydration to the current reader activation, route and data session.
import { useCallback, useEffect, useRef } from 'react'
import { useStore } from '@nanostores/react'
import { getEntry } from '@/apis'
import { contentState, setActiveContent, setIsArticleLoading } from '@/store/contentState'
import { dataState, getDataSessionRevision } from '@/store/dataState'
import { authState } from '@/store/authState'
import { getAuthSessionKey } from '@/utils/auth'
import prepareEntry from '@/utils/entry-presentation'

export default function useReaderEntryDetail({ entryId, source, sourceId, activeContent, entryRequestIdRef, restoreEntryListFocus }) {
  const { sessionRevision } = useStore(dataState, { keys: ['sessionRevision'] })
  const routeKey = JSON.stringify([source, sourceId ?? null, entryId ?? null])
  const currentRoute = useRef(routeKey)
  currentRoute.current = routeKey
  const previousRoute = useRef(null)
  const pendingOwner = useRef(null)
  const selection = useRef({ generation: 0, active: contentState.get().activeContent })

  useEffect(() => {
    const stop = contentState.listen(({ activeContent: next }) => {
    const previous = selection.current.active
    if (next !== previous) {
      if (!next || !previous || next.id !== previous.id) selection.current.generation += 1
      selection.current.active = next
    }
    })
    return () => {
      stop()
      entryRequestIdRef.current += 1
      previousRoute.current = null
      pendingOwner.current = null
    }
  }, [entryRequestIdRef])

  const fetchSingleEntry = useCallback(async (requestedId) => {
    const requestId = ++entryRequestIdRef.current
    const requestRoute = currentRoute.current
    const requestSession = getDataSessionRevision()
    const requestAuth = getAuthSessionKey(authState.get())
    const requestGeneration = selection.current.generation
    const requestActive = contentState.get().activeContent
    const isCurrent = () => entryRequestIdRef.current === requestId &&
      currentRoute.current === requestRoute && getDataSessionRevision() === requestSession &&
      getAuthSessionKey(authState.get()) === requestAuth &&
      selection.current.generation === requestGeneration && contentState.get().activeContent === requestActive
    pendingOwner.current = { requestId, isCurrent }
    const numericId = Number(requestedId)
    const existing = contentState.get().entries.find(entry => entry.id === numericId)
    if (existing && !existing.content_deferred) {
      pendingOwner.current = null
      setIsArticleLoading(false)
      setActiveContent(existing)
      return
    }
    try {
      setIsArticleLoading(true)
      const entry = await getEntry(requestedId)
      if (isCurrent()) {
        if (entry?.id !== numericId || entry.content_deferred) throw new Error('Unexpected detail identity')
        // Publishing complete content changes active identity. Finish this
        // request's loading state first; a stale finally must not clear a newer one.
        setIsArticleLoading(false)
        setActiveContent(prepareEntry(entry))
      }
    } catch (error) {
      if (isCurrent()) console.error('Failed to fetch entry:', error)
    } finally {
      if (isCurrent()) setIsArticleLoading(false)
      if (pendingOwner.current?.requestId === requestId) pendingOwner.current = null
    }
  }, [entryRequestIdRef])

  useEffect(() => {
    const routeChanged = previousRoute.current !== routeKey
    previousRoute.current = routeKey
    const current = contentState.get().activeContent
    if (!entryId) {
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
      entryRequestIdRef.current += 1
      setIsArticleLoading(false)
      return
    }
    if (current?.id !== Number(entryId) && !routeChanged) return
    if (current?.id === Number(entryId) && !current.content_deferred) return
    void fetchSingleEntry(entryId)
  }, [entryId, routeKey, activeContent, sessionRevision, fetchSingleEntry, entryRequestIdRef, restoreEntryListFocus])
}
