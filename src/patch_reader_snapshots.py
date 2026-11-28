"""Add verified account snapshots at the reader's generated integration points."""
import json
import os
from pathlib import Path


CHANGES = {
    "src/data/app-data-coordinator.js": [
        ('import { getRawUnreadTodayCount } from "@/apis/entries"',
         'import { takeReaderIdentity } from "@/utils/reader-startup-identity"\nimport { getRawUnreadTodayCount } from "@/apis/entries"'),
        ('  const currentUser = await getCurrentUser()',
         '''  const preloaded = takeReaderIdentity(getAuthSessionKey(authState.get()), getDataSessionRevision())
  const currentUser = await (preloaded ?? getCurrentUser())'''),
    ],
    "src/store/contentState.js": [
        ('  articleListResultOwner: null,',
         '  articleListResultOwner: null,\n  articleListCache: null,'),
    ],
    "src/components/HomePageManager.jsx": [
        ('import { dataState } from "@/store/dataState"',
         'import { dataState, visibleCategoriesState, visibleFeedsState } from "@/store/dataState"'),
        ('  const identityLoadState = loadState.identity',
         '''  const identityLoadState = loadState.identity
  const cachedReadingTarget = location.state?.cachedReadingTarget
  useEffect(() => {
    if (!cachedReadingTarget || !catalogSnapshotRevision ||
        location.pathname !== getHomeTargetPath(cachedReadingTarget)) return
    if (!isHomeTargetInCatalog(cachedReadingTarget, visibleFeedsState.get(), visibleCategoriesState.get())) {
      navigate("/all", { replace: true })
    }
  }, [cachedReadingTarget, catalogSnapshotRevision, location.pathname, navigate])
'''),
    ],
    "src/hooks/useArticleList.js": [
        ('import prepareEntry from "@/utils/entry-presentation"',
         '''import prepareEntry from "@/utils/entry-presentation"
import { articleSnapshots } from "@/utils/article-list-snapshot"
import { getArticleSnapshotContext, matchesArticleSnapshot } from "@/utils/article-list-snapshot-context"
import { getNoteRequestStamp } from "@/utils/note-session"'''),
        ('  const settingsSnapshot = useStore(articleListRequestSettingsState)',
         '''  const settingsSnapshot = useStore(articleListRequestSettingsState)
  const { removeDuplicates } = useStore(settingsState, { keys: ["removeDuplicates"] })'''),
        ('  }, [automaticRequestKey])',
         '''  }, [automaticRequestKey])

  useLayoutEffect(() => {
    const content = contentState.get()
    if (!content.articleListCache) return
    const context = getArticleSnapshotContext(content, settingsState.get(), { from: source, id: sourceId })
    if (!matchesArticleSnapshot(content.articleListCache, context)) {
      contentState.setKey("articleListCache", null)
    }
  }, [automaticRequestKey, getEntries, removeDuplicates, source, sourceId])'''),
        ('    const isRequestSessionCurrent = () => requestSessionRevision === getDataSessionRevision()',
         '''    const sessionStamp = getNoteRequestStamp()
    const isRequestSessionCurrent = () =>
      requestSessionRevision === getDataSessionRevision() && sessionStamp === getNoteRequestStamp()'''),
        ('''    setIsArticleListReady(false)

    try {''',
         '''    setIsArticleListReady(false)

    const context = getArticleSnapshotContext(content, settings, info)
    const isSnapshotCurrent = () => isLatestRequest() && isRequestSessionCurrent() &&
      matchesArticleSnapshot(context, getArticleSnapshotContext(contentState.get(), settingsState.get(), info))
    let liveCommitted = false
    let cacheRead
    if (matchesArticleSnapshot(content.articleListCache, context)) {
      contentState.setKey("articleListCache", { ...content.articleListCache, status: "refreshing" })
    } else if (context && content.articleListResultOwner?.requestKey !== requestKey) {
      cacheRead = articleSnapshots.read(context.owner, context.signature).then(snapshot => {
        if (snapshot && !liveCommitted && isSnapshotCurrent()) {
          contentState.setKey("articleListCache", { ...snapshot, status: "refreshing" })
        }
      })
    }

    try {'''),
        ('''      contentState.setKey("articleListResultOwner", { requestKey, sessionRevision: requestSessionRevision })''',
         '''      contentState.setKey("articleListResultOwner", { requestKey, sessionRevision: requestSessionRevision })
      liveCommitted = true
      if (isSnapshotCurrent()) {
        const firstPage = contentState.get()
        void articleSnapshots.save(context.owner, context, {
          entries: firstPage.entries,
          total: firstPage.total,
          offset: firstPage.articleListOffset,
          hasMore: firstPage.loadMoreVisible,
          aiRevision: firstPage.articleListAiRevision,
        })
      }'''),
        ('      console.error("Error fetching articles:", error)',
         '''      console.error("Error fetching articles:", error)
      if (cacheRead) await cacheRead
      if (!isLatestRequest() || !isRequestSessionCurrent()) return
      const cached = contentState.get().articleListCache
      if (matchesArticleSnapshot(cached, context) && isSnapshotCurrent()) {
        contentState.setKey("articleListCache", { ...cached, status: "offline" })
        return
      }'''),
        ('''      if (isLatestRequest() && isRequestSessionCurrent()) {
        setIsArticleListReady(true)
      }''',
         '''      if (isLatestRequest() && isRequestSessionCurrent()) {
        const current = contentState.get()
        if (!current.articleListCache) setIsArticleListReady(true)
        else if (liveCommitted) {
          contentState.set({ ...current, articleListCache: null, isArticleListReady: true })
        }
      }'''),
        ('  }, [automaticRequestKey, fetchArticleList])',
         '  }, [automaticRequestKey, fetchArticleList, removeDuplicates])'),
    ],
    "src/hooks/useLoadMore.js": [
        ('    if (loadingMoreState.get() || !getReadingCalendarSnapshot().ready || !contentState.get().isArticleListReady) {',
         '''    if (contentState.get().articleListCache) return
    if (loadingMoreState.get() || !getReadingCalendarSnapshot().ready || !contentState.get().isArticleListReady) {'''),
        ('    const isCurrentRequest = () =>\n      requestKey === getCurrentArticleListRequestKey()',
         '''    const isCurrentRequest = () =>
      !contentState.get().articleListCache &&
      requestKey === getCurrentArticleListRequestKey()'''),
    ],
    "src/components/Article/ArticleList.jsx": [
        ('    const emptyState = useStore(articleListEmptyState)',
         '''    const { articleListCache } = useStore(contentState, { keys: ["articleListCache"] })
    const emptyState = useStore(articleListEmptyState)'''),
        ('    const observeRead = useReadOnScroll(cardsRef)',
         '    const observeRead = useReadOnScroll(cardsRef, !articleListCache)'),
        ('    const canRenderResults = isArticleListReady && !articleListError && !isWaitingForCatalog',
         '''    const canRenderResults = articleListCache || (isArticleListReady && !articleListError && !isWaitingForCatalog)
    const displayEntries = articleListCache?.entries ?? filteredEntries'''),
        ('      entries: filteredEntries,',
         '''      entries: displayEntries,
      articleListCache,
      liveReady: isArticleListReady && !articleListError,'''),
        ('          loading={!isArticleListReady || isWaitingForCatalog}',
         '          loading={!articleListCache && (!isArticleListReady || isWaitingForCatalog)}'),
        ('{canRenderResults && showEmptyState && (',
         '{canRenderResults && !articleListCache && showEmptyState && ('),
        ('{canRenderResults && filteredEntries.length > 0 && (',
         '{canRenderResults && displayEntries.length > 0 && ('),
        ('        <ProgressiveLoadMore getEntries={getEntries} scrollRootRef={cardsRef} />',
         '        {!articleListCache && <ProgressiveLoadMore getEntries={getEntries} scrollRootRef={cardsRef} />}'),
    ],
    "src/hooks/useArticleListLayout.js": [
        ('const CARD_GRID_GAP = 12',
         'import { snapshotSwapAnchor } from "@/utils/article-list-snapshot"\n\nconst CARD_GRID_GAP = 12'),
        ('const useArticleListLayout = ({ entries, layout, scrollRootRef }) => {',
         'const useArticleListLayout = ({ entries, layout, scrollRootRef, articleListCache, liveReady }) => {'),
        ('  const previousStructureRef = useRef(null)',
         '''  const previousStructureRef = useRef(null)
  const previousCacheRef = useRef(null)
  const handleVirtualizerScroll = useCallback(() => {
    if (articleListCache) captureVisibleAnchor()
    else scheduleAnchorCapture()
  }, [articleListCache, captureVisibleAnchor, scheduleAnchorCapture])'''),
        ('''    previousStructureRef.current = currentStructure

    if (!previousStructure) {''',
         '''    previousStructureRef.current = currentStructure
    const previousCache = previousCacheRef.current
    previousCacheRef.current = articleListCache
    const cachedAnchor = visibleAnchorRef.current
    const swap = snapshotSwapAnchor(previousCache, liveReady, articleListCache, cachedAnchor,
      entries, layout === "card" ? cardColumnCount : 1)
    if (swap) {
      pendingAnchorRef.current = null
      if (restoreFrameRef.current !== null) cancelFrame(restoreFrameRef.current)
      restoreFrameRef.current = null
      const root = scrollRootElementRef.current ?? scrollRootRef.current
      if (swap.index === 0 && swap.offset === 0 && root) {
        root.scrollTop = 0
      } else {
        virtualizerRef.current?.scrollToIndex(swap.index, { align: "start", offset: swap.offset })
        restoreFrameRef.current = requestFrame(() => {
          restoreFrameRef.current = null
          if (!root || previousStructureRef.current !== currentStructure) return
          const entry = root.querySelector(`[data-entry-id="${cachedAnchor.entryId}"]`)
          if (entry) {
            root.scrollTop += entry.getBoundingClientRect().top - root.getBoundingClientRect().top - cachedAnchor.offset
          }
          scheduleAnchorCapture()
        })
      }
      scheduleAnchorCapture()
      return
    }
    if (articleListCache && !previousCache) {
      visibleAnchorRef.current = null
      captureVisibleAnchor()
    }

    if (!previousStructure) {'''),
        ('  }, [cardColumnCount, entries, layout, scheduleAnchorCapture, scrollRootRevision])',
         '''  }, [articleListCache, cardColumnCount, captureVisibleAnchor, entries, layout, liveReady,
    scheduleAnchorCapture, scrollRootRef, scrollRootRevision])'''),
        ('    handleVirtualizerScroll: scheduleAnchorCapture,',
         '    handleVirtualizerScroll,'),
    ],
    "src/hooks/useReadOnScroll.js": [
        ('import { markEntriesAsRead } from "@/hooks/useEntryActions"',
         '''import { markEntriesAsRead } from "@/hooks/useEntryActions"
import { contentState } from "@/store/contentState"
import { isShowingArticleSnapshot } from "@/utils/article-list-snapshot"'''),
        ('const useReadOnScroll = (rootRef) => {',
         'const useReadOnScroll = (rootRef, enabled = true) => {'),
        ('    markEntriesAsRead(entries)',
         '    if (!isShowingArticleSnapshot(contentState.get())) markEntriesAsRead(entries)'),
        ('      pendingEntriesRef.current.set(entry.id, entry)',
         '''      if (isShowingArticleSnapshot(contentState.get())) return
      pendingEntriesRef.current.set(entry.id, entry)'''),
        ('''    (element, entry) => {
      const observer = getObserver()''',
         '''    (element, entry) => {
      if (!enabled || isShowingArticleSnapshot(contentState.get())) return
      const observer = getObserver()'''),
        ('    [getObserver, queueEntry],', '    [enabled, getObserver, queueEntry],'),
    ],
    "src/components/Content/FooterPanel.jsx": [
        ('import { requireReadingCalendar, assertReadingCalendarCurrent } from "@/store/readingCalendarState"',
         'import { requireReadingCalendar, assertReadingCalendarCurrent } from "@/store/readingCalendarState"\nimport { isShowingArticleSnapshot } from "@/utils/article-list-snapshot"'),
        ('const MarkAllReadButton = ({ filtered, from, onConfirm }) => {',
         '''const MarkAllReadButton = ({ filtered, from, onConfirm }) => {
  const cached = isShowingArticleSnapshot(useStore(contentState, { keys: ["articleListCache"] }))'''),
        ('        icon={<IconCheck aria-hidden="true" />}',
         '        disabled={cached}\n        icon={<IconCheck aria-hidden="true" />}'),
        ('  if (skipMarkAllReadConfirmation) {', '  if (cached || skipMarkAllReadConfirmation) {'),
        ('      const calendar = requireReadingCalendar()',
         '      const calendar = requireReadingCalendar()\n      if (isShowingArticleSnapshot(contentState.get())) return'),
        ('  const refreshLabel = polyglot.t("article_list.refresh_tooltip")',
         '''  const refreshLabel = polyglot.t("article_list.refresh_tooltip")
  const { articleListCache } = useStore(contentState, { keys: ["articleListCache"] })'''),
        ('      <CustomTooltip mini content={refreshLabel}>',
         '''      {articleListCache && (
        <span role="status" style={{ color: "var(--color-text-2)", fontSize: 12 }}>
          {articleListCache.status === "offline" ? "离线 · 显示上次内容" : "更新中…"}
        </span>
      )}
      <CustomTooltip mini content={refreshLabel}>'''),
        ('          loading={!isArticleListReady}',
         '          loading={!isArticleListReady && articleListCache?.status !== "offline"}'),
    ],
    "src/components/Sidebar/Sidebar.jsx": [
        ('import { articleListResultReadyState } from "@/store/contentState"',
         'import { articleListResultReadyState } from "@/store/contentState"\nimport { isShowingArticleSnapshot } from "@/utils/article-list-snapshot"'),
        ('  const unreadCount = feedsGroupedById[category.id]?.reduce(',
         '''  const cached = isShowingArticleSnapshot(useStore(contentState, { keys: ["articleListCache"] }))
  const unreadCount = feedsGroupedById[category.id]?.reduce('''),
        ('  const { showFeedIcon } = useStore(settingsState, { keys: ["showFeedIcon"] })',
         '''  const cached = isShowingArticleSnapshot(useStore(contentState, { keys: ["articleListCache"] }))
  const { showFeedIcon } = useStore(settingsState, { keys: ["showFeedIcon"] })'''),
        ('          <MenuItem key="mark-all-as-read" onClick={() => onMarkAllAsRead(category)}>',
         '''          <MenuItem disabled={cached} key="mark-all-as-read" onClick={() => onMarkAllAsRead(category)}>'''),
        ('          <MenuItem key="mark-all-as-read" onClick={() => onMarkAllAsRead(feed)}>',
         '''          <MenuItem disabled={cached} key="mark-all-as-read" onClick={() => onMarkAllAsRead(feed)}>'''),
        ('  const handleMarkAllAsReadCategory = async (category) => {',
         '''  const handleMarkAllAsReadCategory = async (category) => {
    if (isShowingArticleSnapshot(contentState.get())) return'''),
        ('    await markFeedAsRead(feed)',
         '    if (isShowingArticleSnapshot(contentState.get())) return\n    await markFeedAsRead(feed)'),
    ],
}


def install(root):
    web = Path(root) / "upstream/reactflux"
    planned = []
    for name, replacements in CHANGES.items():
        path = web / name
        original = path.read_text(encoding="utf-8")
        updated = original
        for before, after in replacements:
            if updated.count(after) == 1:
                continue
            if updated.count(before) != 1 or after in updated:
                raise RuntimeError(f"Reader snapshot patch anchor mismatch: {name}")
            updated = updated.replace(before, after, 1)
        if updated != original:
            planned.append((path, updated))
    for path, updated in planned:
        path.write_text(updated, encoding="utf-8", newline="\n")
    return [path.relative_to(web).as_posix() for path, _ in planned]


if __name__ == "__main__":
    root = Path(os.environ.get("AI_NEWS_ROOT", "/home/ubuntu/ai-news"))
    print(json.dumps({"reader_snapshots_installed": install(root)}))
