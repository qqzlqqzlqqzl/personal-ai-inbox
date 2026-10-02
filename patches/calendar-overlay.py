# Executed only by patch_scope_ai_filters, using its fail-closed anchors.
for source, destination in [
    ("reading-calendar.js", "src/utils/reading-calendar.js"),
    ("readingCalendarState.js", "src/store/readingCalendarState.js"),
]:
    shutil.copy2(ROOT / "patches" / source, WEB / destination)

patch("src/store/dataState.js", 'import { computed, map } from "nanostores"',
      'import { computed, map } from "nanostores"\nimport { authState } from "@/store/authState"\nimport { getAuthSessionKey } from "@/utils/auth"')
patch("src/store/dataState.js", '  currentUser: null,', '  currentUser: null,\n  identityAuthSessionKey: "",')
patch("src/store/dataState.js",
      'export const commitIdentityData = (currentUser) => commitResourceData("identity", { currentUser })',
      'export const commitIdentityData = (currentUser, identityAuthSessionKey = getAuthSessionKey(authState.get())) =>\n  commitResourceData("identity", { currentUser, identityAuthSessionKey })')
patch("src/utils/session.js", '  commitIdentityData(currentUser)', '  commitIdentityData(currentUser, getAuthSessionKey(auth))')

patch("src/utils/date.js", 'import dayjs from "dayjs"',
      'import dayjs from "dayjs"\nimport { requireReadingCalendar } from "@/store/readingCalendarState"\nimport { calendarStart, calendarEndTimestamp, matchesToday } from "@/utils/reading-calendar"')
patch("src/utils/date.js", 'export const getStartOfToday = () => dayjs().startOf("day")',
      """export const getStartOfToday = () => {
  const { day, zone } = requireReadingCalendar()
  return calendarStart(day, zone)
}

export const getCalendarStartTimestamp = (value) => calendarStart(value, requireReadingCalendar().zone).unix()

export const checkIsInToday = (value) => {
  try { return matchesToday(value, requireReadingCalendar().cutoff) }
  catch { return false }
}""")
patch("src/utils/date.js", 'export const getDayEndTimestamp = (dateString) => dayjs(dateString).endOf("day").unix()',
      'export const getDayEndTimestamp = (dateString) => calendarEndTimestamp(dateString, requireReadingCalendar().zone)')
patch("src/hooks/useEntryActions.js", 'import { checkIsInLast24Hours } from "@/utils/date"', 'import { checkIsInToday } from "@/utils/date"')
patch("src/hooks/useEntryActions.js", '    const isRecent = checkIsInLast24Hours(entry.published_at)', '    const isRecent = checkIsInToday(entry.published_at)')

patch("src/apis/entries.js", 'import { getAiQuery } from "@/store/aiState"',
      'import { getAiQuery } from "@/store/aiState"\nimport { getCalendarStartTimestamp } from "@/utils/date"\nimport { requireReadingCalendar, assertReadingCalendarCurrent } from "@/store/readingCalendarState"')
patch("src/apis/entries.js", 'queryParams.append(afterParam, getTimestamp(filterDate))', 'queryParams.append(afterParam, getCalendarStartTimestamp(filterDate))')
patch("src/apis/entries.js", 'export const markEntriesAsReadInBatches = async (fetchEntries) => {',
      'export const markEntriesAsReadInBatches = async (fetchEntries) => {\n  const calendar = requireReadingCalendar()')
patch("src/apis/entries.js", '    // Always fetch from offset zero because marking a batch read removes it from the result set.',
      '    assertReadingCalendarCurrent(calendar)\n    // Always fetch from offset zero because marking a batch read removes it from the result set.')
patch("src/apis/entries.js", '    const unreadEntries = response?.entries ?? []',
      '    assertReadingCalendarCurrent(calendar)\n    const unreadEntries = response?.entries ?? []')
patch("src/apis/entries.js", '    await updateEntriesStatus(unreadEntryIds, "read")',
      '    assertReadingCalendarCurrent(calendar)\n    await updateEntriesStatus(unreadEntryIds, "read")')
patch("src/apis/entries.js", 'const getAllEntryIds = async (filters) => {',
      'const getAllEntryIds = async (filters, calendar) => {')
patch("src/apis/entries.js", '  while (offset < total) {\n    const response = await getEntryIds({',
      '  while (offset < total) {\n    assertReadingCalendarCurrent(calendar)\n    const response = await getEntryIds({')
patch("src/apis/entries.js", '    const pageEntryIds = response?.entry_ids ?? []',
      '    assertReadingCalendarCurrent(calendar)\n    const pageEntryIds = response?.entry_ids ?? []')
patch("src/apis/entries.js", 'const updateEntryIdsInBatches = async (entryIds, updates) => {',
      'const updateEntryIdsInBatches = async (entryIds, updates, calendar) => {')
patch("src/apis/entries.js", 'export const markStarredEntriesAsRead = async () => {',
      'export const markStarredEntriesAsRead = async () => {\n  const calendar = requireReadingCalendar()')
patch("src/apis/entries.js", '    status: "unread",\n  })\n\n  return updateEntryIdsInBatches(unreadStarredEntryIds, { status: "read" })',
      '    status: "unread",\n  }, calendar)\n\n  assertReadingCalendarCurrent(calendar)\n  return updateEntryIdsInBatches(unreadStarredEntryIds, { status: "read" }, calendar)')
patch("src/apis/entries.js", '    await updateEntries(entryIds.slice(batchStart, batchStart + ENTRY_UPDATE_BATCH_SIZE), updates)',
      '    assertReadingCalendarCurrent(calendar)\n    await updateEntries(entryIds.slice(batchStart, batchStart + ENTRY_UPDATE_BATCH_SIZE), updates)')

patch("src/utils/article-list-request-key.js", 'const articleListContentKeyFields = [',
      'import { getReadingCalendarSnapshot } from "@/store/readingCalendarState"\n\nconst articleListContentKeyFields = [')
patch("src/utils/article-list-request-key.js", 'return JSON.stringify({ ...contentKey, ...settingsKey })',
      'return JSON.stringify({ ...contentKey, ...settingsKey, readingCalendar: getReadingCalendarSnapshot().key })')
patch("src/hooks/useArticleList.js", 'import { aiFilterEnabled } from "@/store/aiState"',
      'import { aiFilterEnabled } from "@/store/aiState"\nimport { readingCalendarKeyState, getReadingCalendarSnapshot } from "@/store/readingCalendarState"')
patch("src/hooks/useArticleList.js", '  const contentSnapshot = useStore(contentState, {',
      '  useStore(readingCalendarKeyState)\n  const contentSnapshot = useStore(contentState, {')
patch("src/hooks/useArticleList.js", '  const fetchArticleList = useCallback(async () => {',
      '  const fetchArticleList = useCallback(async () => {\n    if (!getReadingCalendarSnapshot().ready) return')
patch("src/hooks/useLoadMore.js", 'import { getTimestamp } from "@/utils/date"',
      'import { getTimestamp } from "@/utils/date"\nimport { getReadingCalendarSnapshot } from "@/store/readingCalendarState"')
patch("src/hooks/useLoadMore.js", '    if (loadingMoreState.get()) {', '    if (loadingMoreState.get() || !getReadingCalendarSnapshot().ready || !contentState.get().isArticleListReady) {')

patch("src/data/app-data-coordinator.js", 'const RESOURCE_NAMES = ["catalog", "counts", "identity", "serverInfo"]',
      'import { getReadingCalendarSnapshot, requireReadingCalendar } from "@/store/readingCalendarState"\nimport { authState } from "@/store/authState"\nimport { getAuthSessionKey } from "@/utils/auth"\n\nconst RESOURCE_NAMES = ["catalog", "counts", "identity", "serverInfo"]')
patch("src/data/app-data-coordinator.js", '  const requestStates = Object.fromEntries(',
      '  const authSessionKey = getAuthSessionKey(authState.get())\n  const requestStates = Object.fromEntries(')
patch("src/data/app-data-coordinator.js", '    const requestId = ++requestState.requestId',
      '    const calendarKey = resource === "counts" ? requireReadingCalendar().key : null\n    const requestId = ++requestState.requestId')
patch("src/data/app-data-coordinator.js", '      isActive &&\n      getDataSessionRevision() === sessionRevision &&',
      '      isActive &&\n      getAuthSessionKey(authState.get()) === authSessionKey &&\n      (resource !== "counts" || calendarKey === getReadingCalendarSnapshot().key) &&\n      getDataSessionRevision() === sessionRevision &&')
patch("src/data/app-data-coordinator.js", '  const refreshCounts = ({ includeEntrySummary = false, ...options } = {}) =>\n    runResource(',
      '  const refreshCounts = async ({ includeEntrySummary = false, ...options } = {}) => {\n    if (!getReadingCalendarSnapshot().ready) await refreshIdentity()\n    requireReadingCalendar()\n    return runResource(')
patch("src/data/app-data-coordinator.js", '  const refreshIdentity = (options) =>',
      '  }\n\n  const refreshIdentity = (options) =>')
patch("src/data/app-data-coordinator.js", '    runResource("identity", loadIdentity, commitIdentityData, options)',
      '    runResource("identity", loadIdentity, user => commitIdentityData(user, authSessionKey), options)')

patch("src/apis/entries.js", 'export const getTodayEntries = async (status = null, filterParams = {}) =>',
      'export const getRawUnreadTodayCount = () => fetchTodayEntries("unread", { limit: 1 }, false, false)\n\nexport const getTodayEntries = async (status = null, filterParams = {}) =>')
patch("src/data/app-data-coordinator.js", 'import { getReadingCalendarSnapshot, requireReadingCalendar } from "@/store/readingCalendarState"',
      'import { getRawUnreadTodayCount } from "@/apis/entries"\nimport { getReadingCalendarSnapshot, requireReadingCalendar } from "@/store/readingCalendarState"')
patch("src/data/app-data-coordinator.js", ': getTodayEntries("unread", { limit: 1 })', ': getRawUnreadTodayCount()')

patch("src/components/AppDataProvider.jsx", 'import { getAuthSessionKey } from "@/utils/auth"',
      """import { getAuthSessionKey } from "@/utils/auth"
import { readingCalendarKeyState, getReadingCalendarSnapshot, startReadingCalendarClock } from "@/store/readingCalendarState"
import { contentState, incrementArticleListSnapshotRevision, invalidateArticleList, setEntries, setTotal, setIsArticleListReady, setLoadMoreVisible } from "@/store/contentState"
import { setUnreadTodayCount } from "@/store/dataState" """.rstrip())
patch("src/components/AppDataProvider.jsx", '  return <AppDataContext.Provider value={coordinator.actions}>{children}</AppDataContext.Provider>',
      """  useEffect(() => {
    let previousKey = readingCalendarKeyState.get()
    const unlisten = readingCalendarKeyState.listen((key) => {
      if (key === previousKey) return
      previousKey = key
      setEntries([])
      contentState.setKey("articleListOffset", 0)
      incrementArticleListSnapshotRevision()
      setTotal(0)
      setIsArticleListReady(false)
      setLoadMoreVisible(false)
      setUnreadTodayCount(0)
      invalidateArticleList()
      if (getReadingCalendarSnapshot().ready)
        void coordinator.actions.refreshCounts({ force: true, includeEntrySummary: true }).catch(() => {})
    })
    const stopClock = startReadingCalendarClock()
    return () => { unlisten(); stopClock() }
  }, [coordinator])

  return <AppDataContext.Provider value={coordinator.actions}>{children}</AppDataContext.Provider>""")

patch("src/components/Content/FooterPanel.jsx", 'import { bulkReadLabel } from "@/utils/bulk-read-label"',
      'import { bulkReadLabel } from "@/utils/bulk-read-label"\nimport { requireReadingCalendar, assertReadingCalendarCurrent } from "@/store/readingCalendarState"')
patch("src/components/Content/FooterPanel.jsx", '  const handleMarkAllAsRead = async () => {\n    try {',
      '  const handleMarkAllAsRead = async () => {\n    try {\n      const calendar = requireReadingCalendar()')
patch("src/components/Content/FooterPanel.jsx", '      await updateUIAfterMarkAsRead()',
      '      assertReadingCalendarCurrent(calendar)\n      await updateUIAfterMarkAsRead()')

patch("src/components/Article/SearchAndSortBar.jsx", 'import { getStartOfToday } from "@/utils/date"',
      'import { getStartOfToday } from "@/utils/date"\nimport { readingCalendarState } from "@/store/readingCalendarState"')
patch("src/components/Article/SearchAndSortBar.jsx", '  const selectDateLabel = polyglot.t("search.select_date")',
      '  const selectDateLabel = polyglot.t("search.select_date")\n  const calendar = useStore(readingCalendarState)')
patch("src/components/Article/SearchAndSortBar.jsx", 'onClick={() => setDateAndClose(getStartOfToday())}',
      'disabled={!calendar.ready}\n            onClick={() => setDateAndClose(getStartOfToday().format("YYYY-MM-DD"))}')
patch("src/components/Article/SearchAndSortBar.jsx", 'const SearchAndSortBar = ({ fullWidth = false }) => {',
      'const SearchAndSortBar = ({ fullWidth = false }) => {\n  const calendar = useStore(readingCalendarState)')
patch("src/components/Article/SearchAndSortBar.jsx", '              {displayTitle}\n',
      '              {displayTitle}\n              {(infoFrom === "today" || filterDate) && <small className="reading-timezone" title="日期范围按账号阅读时区计算"> · {calendar.zone || "时区待确认"}</small>}\n')

# An authenticated invalid zone (or failed refresh with a prior snapshot) must expose recovery.
patch("src/components/HomePageManager.jsx", 'import useAppData from "@/hooks/useAppData"',
      'import useAppData from "@/hooks/useAppData"\nimport { readingCalendarState } from "@/store/readingCalendarState"')
patch("src/components/HomePageManager.jsx", '  const identity = useStore(homeIdentityState)',
      '  const calendar = useStore(readingCalendarState)\n  const identity = useStore(homeIdentityState)')
patch("src/components/HomePageManager.jsx", '  if (location.pathname === "/" || identityLoadState.hasSnapshot || !identityLoadState.error) {',
      '  if (location.pathname === "/" || (!identityLoadState.error && (!identityLoadState.hasSnapshot || calendar.ready))) {')
patch("src/components/HomePageManager.jsx", '<span>{polyglot.t("home_page.identity_error_inline")}</span>',
      '<span>{!calendar.ready ? "阅读时区尚未确认，请重试账号身份加载。" : polyglot.t("home_page.identity_error_inline")}</span>')
patch("src/components/HomePageManager.jsx", 'loading={identityLoadState.activity === "loading"}',
      'loading={identityLoadState.activity !== "idle"}')
