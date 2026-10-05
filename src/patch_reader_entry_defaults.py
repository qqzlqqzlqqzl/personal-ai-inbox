"""Make inbox entry Today + AI picks and align active sidebar counts with visible results."""
import os
from pathlib import Path

ROOT = Path(os.environ.get("AI_NEWS_ROOT", "/home/ubuntu/ai-news"))
WEB = ROOT / "upstream/reactflux"


def patch(name, old, new, previous=None):
    path = WEB / name
    text = path.read_text()
    if new in text:
        return
    if text.count(old) != 1 and previous is not None:
        candidates = previous if isinstance(previous, tuple) else (previous,)
        matches = [candidate for candidate in candidates if text.count(candidate) == 1]
        if len(matches) == 1:
            old = matches[0]
    if text.count(old) != 1:
        raise RuntimeError(f"reader-entry patch anchor mismatch: {name}")
    path.write_text(text.replace(old, new, 1))


# Fresh/reset home targets use Today instead of the full historical library.
patch(
    "src/utils/home-page.js",
    'export const DEFAULT_HOME_TARGET = Object.freeze({ type: "view", id: "all" })',
    'export const DEFAULT_HOME_TARGET = Object.freeze({ type: "view", id: "today" })',
)
patch(
    "src/utils/home-page.js",
    'export const getHomeTargetForIdentity = (homePages, identity, legacyHomePage = "all") => {',
    'export const getHomeTargetForIdentity = (homePages, identity, legacyHomePage = "today") => {',
)
patch(
    "src/utils/home-page.js",
    'export const ensureHomeIdentity = (homePages, identity, legacyHomePage = "all") => {',
    'export const ensureHomeIdentity = (homePages, identity, legacyHomePage = "today") => {',
)
patch(
    "src/utils/settings-schema.js",
    'homePage: enumSetting("all", ["all", "today", "starred", "history"])',
    'homePage: enumSetting("today", ["all", "today", "starred", "history"])',
)

# Opening the inbox root starts a new reading session in the primary AI-picks lens.
patch(
    "src/store/aiState.js",
    'export const getAiQuery = () => {',
    '''export const resetInboxLandingView = () => {
  const current = normalizeAiState(aiState.get())
  aiState.set(normalizeAiState({
    ...current,
    mode: "recommended",
    auxiliary: "none",
    sort: current.sort === "note_updated" ? "score" : current.sort,
  }))
}

export const getAiQuery = () => {''',
)
patch(
    "src/components/HomeRedirect.jsx",
    'import { Navigate, useNavigate } from "react-router"',
    'import { useLayoutEffect } from "react"\nimport { Navigate, useNavigate } from "react-router"',
)
patch(
    "src/components/HomeRedirect.jsx",
    'import { dataState, visibleCategoriesState, visibleFeedsState } from "@/store/dataState"',
    'import { resetInboxLandingView } from "@/store/aiState"\nimport { dataState, visibleCategoriesState, visibleFeedsState } from "@/store/dataState"',
)
patch(
    "src/components/HomeRedirect.jsx",
    'import { currentHomeTargetState, homeIdentityState } from "@/store/homePageState"',
    'import { currentHomeTargetState, homeIdentityState, setCurrentHomeTarget } from "@/store/homePageState"',
)
patch(
    "src/components/HomeRedirect.jsx",
    'import { getHomeTargetPath, isHomeTargetInCatalog } from "@/utils/home-page"',
    'import { createViewHomeTarget, getHomeTargetPath, isHomeTargetInCatalog } from "@/utils/home-page"',
)
patch(
    "src/components/HomeRedirect.jsx",
    '  const navigate = useNavigate()\n\n  const handleLogout = () => {',
    '''  const navigate = useNavigate()

  useLayoutEffect(() => {
    if (!identity) return
    resetInboxLandingView()
    if (target.type === "view" && target.id === "all") {
      setCurrentHomeTarget(createViewHomeTarget("today"))
    }
  }, [identity, target])

  const handleLogout = () => {''',
)
patch(
    "src/components/HomeRedirect.jsx",
    '''  if (target.type === "view") {
    return <Navigate replace to={getHomeTargetPath(target)} />
  }''',
    '''  if (target.type === "view") {
    return <Navigate replace to={target.id === "all" ? "/today" : getHomeTargetPath(target)} />
  }''',
)
patch(
    "src/components/HomeRedirect.jsx",
    '  return <Navigate replace to={targetExists ? getHomeTargetPath(target) : "/all"} />',
    '  return <Navigate replace to={targetExists ? getHomeTargetPath(target) : "/today"} />',
)

patch(
    "src/components/Sidebar/Sidebar.jsx",
    'import { settingsState, updateSettings } from "@/store/settingsState"',
    'import { settingsState, updateSettings } from "@/store/settingsState"\nimport { articleListResultReadyState } from "@/store/contentState"',
)

# Inactive native totals are valid only for the unfiltered raw-unread lens.
# Other scopes have no same-lens total yet: omit it rather than borrow today's
# total, expose a raw count or issue an expensive per-scope request.
patch(
    "src/components/Sidebar/Sidebar.jsx",
    'import {\n  contentState,',
    'import { aiState } from "@/store/aiState"\nimport {\n  contentState,',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    'const MenuItem = Menu.Item',
    '''const useNativeSidebarCounts = () => {
  const { showStatus } = useStore(settingsState, { keys: ["showStatus"] })
  const { filterDate, filterString } = useStore(contentState, { keys: ["filterDate", "filterString"] })
  const { mode, auxiliary, hydrated } = useStore(aiState)
  return hydrated === true && mode === "all" && auxiliary === "none" &&
    showStatus === "unread" && !filterDate && !filterString
}

const MenuItem = Menu.Item''',
)

# Keep the active query's original owner/readiness gate. Unknown is not zero.
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''  contentState,
  invalidateArticleList,''',
    '''  contentState,
  dynamicCountState,
  invalidateArticleList,''',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    'const SidebarMenuItems = () => {\n  const { infoFrom } = useStore(contentState, { keys: ["infoFrom"] })',
    'const SidebarMenuItems = ({ activeScopeCount, infoFrom }) => {',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''  const unreadTotal = useStore(unreadTotalState)

  return (''',
    '''  const unreadTotal = useStore(unreadTotalState)
  const nativeCountsMatch = useNativeSidebarCounts()
  const scopedCount = (scope, nativeCount) =>
    infoFrom === scope ? activeScopeCount : nativeCountsMatch ? nativeCount : null

  return (''',
    previous=('''  const unreadTotal = useStore(unreadTotalState)
  const scopedCount = (scope, nativeCount) =>
    infoFrom === scope ? activeScopeCount : nativeCount

  return (''', '''  const unreadTotal = useStore(unreadTotalState)
  const scopedCount = (scope, nativeCount) =>
    activeScopeCount !== null && infoFrom === scope ? activeScopeCount : nativeCount

  return ('''),
)
for scope, native in (
    ("all", "unreadTotal"),
    ("today", "unreadTodayCount"),
    ("starred", 'infoFrom === "starred" ? starredCount : 0'),
    ("history", 'infoFrom === "history" ? historyCount : 0'),
):
    patch(
        "src/components/Sidebar/Sidebar.jsx",
        f"        count={{{native}}}",
        f'        count={{scopedCount("{scope}", {native})}}',
    )

patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''const CategoryTitle = ({
  category,
  path,''',
    '''const CategoryTitle = ({
  activeScopeCount,
  activeScope,
  activeScopeId,
  category,
  path,''',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''  const categoryClassName = classNames("category-title", {
    "submenu-active": isCategoryActive,
    "submenu-inactive": !isCategoryActive,
  })

  return (''',
    '''  const categoryClassName = classNames("category-title", {
    "submenu-active": isCategoryActive,
    "submenu-inactive": !isCategoryActive,
  })
  const nativeCountsMatch = useNativeSidebarCounts()
  const displayCount =
    activeScope === "category" &&
    Number(activeScopeId) === Number(category.id)
      ? activeScopeCount
      : nativeCountsMatch ? unreadCount : null

  return (''',
    previous=('''  const categoryClassName = classNames("category-title", {
    "submenu-active": isCategoryActive,
    "submenu-inactive": !isCategoryActive,
  })
  const displayCount =
    activeScope === "category" &&
    Number(activeScopeId) === Number(category.id)
      ? activeScopeCount
      : unreadCount

  return (''', '''  const categoryClassName = classNames("category-title", {
    "submenu-active": isCategoryActive,
    "submenu-inactive": !isCategoryActive,
  })
  const displayCount =
    activeScopeCount !== null &&
    activeScope === "category" &&
    Number(activeScopeId) === Number(category.id)
      ? activeScopeCount
      : unreadCount

  return ('''),
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '            width: unreadCount ? "80%" : "100%",',
    '            width: displayCount ? "80%" : "100%",',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''        {unreadCount > 0 && (
          <Typography.Ellipsis''',
    '''        {displayCount > 0 && (
          <Typography.Ellipsis''',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''            {unreadCount}
          </Typography.Ellipsis>''',
    '''            {displayCount}
          </Typography.Ellipsis>''',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''const FeedMenuItem = ({
  feed,''',
    '''const FeedMenuItem = ({
  activeScopeCount,
  activeScope,
  activeScopeId,
  feed,''',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''  const feedTarget = createEntityHomeTarget("feed", feed.id)
  const isHomePage = isSameHomeTarget(homeTarget, feedTarget)

  return (''',
    '''  const feedTarget = createEntityHomeTarget("feed", feed.id)
  const isHomePage = isSameHomeTarget(homeTarget, feedTarget)
  const nativeCountsMatch = useNativeSidebarCounts()
  const displayCount =
    activeScope === "feed" &&
    Number(activeScopeId) === Number(feed.id)
      ? activeScopeCount
      : nativeCountsMatch ? feed.unreadCount : null

  return (''',
    previous=('''  const feedTarget = createEntityHomeTarget("feed", feed.id)
  const isHomePage = isSameHomeTarget(homeTarget, feedTarget)
  const displayCount =
    activeScope === "feed" &&
    Number(activeScopeId) === Number(feed.id)
      ? activeScopeCount
      : feed.unreadCount

  return (''', '''  const feedTarget = createEntityHomeTarget("feed", feed.id)
  const isHomePage = isSameHomeTarget(homeTarget, feedTarget)
  const displayCount =
    activeScopeCount !== null &&
    activeScope === "feed" &&
    Number(activeScopeId) === Number(feed.id)
      ? activeScopeCount
      : feed.unreadCount

  return ('''),
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '              width: feed.unreadCount ? "80%" : "100%",',
    '              width: displayCount ? "80%" : "100%",',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''          {feed.unreadCount !== 0 && (
            <Typography.Ellipsis className="item-count" expandable={false}>
              {feed.unreadCount}
            </Typography.Ellipsis>
          )}''',
    '''          {displayCount > 0 && (
            <Typography.Ellipsis className="item-count" expandable={false}>
              {displayCount}
            </Typography.Ellipsis>
          )}''',
    previous='          {displayCount !== 0 && (\n            <Typography.Ellipsis className="item-count" expandable={false}>\n              {displayCount}\n            </Typography.Ellipsis>\n          )}',
)

patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''const FeedMenuGroup = ({
  categoryId,''',
    '''const FeedMenuGroup = ({
  activeScopeCount,
  activeScope,
  activeScopeId,
  categoryId,''',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''          <FeedMenuItem
            key={feed.id}
            feed={feed}''',
    '''          <FeedMenuItem
            key={feed.id}
            activeScopeCount={activeScopeCount}
            activeScope={activeScope}
            activeScopeId={activeScopeId}
            feed={feed}''',
)

patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''const CategoryGroup = ({
  homePageReady,''',
    '''const CategoryGroup = ({
  activeScopeCount,
  activeScope,
  activeScopeId,
  homePageReady,''',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''          <CategoryTitle
            category={category}''',
    '''          <CategoryTitle
            activeScopeCount={activeScopeCount}
            activeScope={activeScope}
            activeScopeId={activeScopeId}
            category={category}''',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''        <FeedMenuGroup
          categoryId={category.id}''',
    '''        <FeedMenuGroup
          activeScopeCount={activeScopeCount}
          activeScope={activeScope}
          activeScopeId={activeScopeId}
          categoryId={category.id}''',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''  const { infoFrom, infoId } = useStore(contentState, { keys: ["infoFrom", "infoId"] })
  const {''',
    r'''  const { infoFrom, infoId } = useStore(contentState, { keys: ["infoFrom", "infoId"] })
  const resultCount = useStore(dynamicCountState)
  const resultReady = useStore(articleListResultReadyState)
  // React Router selects a route before Content commits its store scope.
  const routeScope = currentPath.match(/^\/(all|today|starred|history|feed|category)(?:\/(\d+))?(?:\/|$)/)
  const activeScope = routeScope?.[1] ?? null
  const activeScopeId = ["feed", "category"].includes(activeScope) ? routeScope?.[2] ?? null : null
  const activeScopeCount = resultReady && infoFrom === activeScope && String(infoId ?? "") === String(activeScopeId ?? "")
    ? resultCount : null
  const {''',
    previous='  const { infoFrom, infoId } = useStore(contentState, { keys: ["infoFrom", "infoId"] })\n  const activeScopeCount = useStore(dynamicCountState)\n  const {',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '          <SidebarMenuItems />',
    '          <SidebarMenuItems activeScopeCount={activeScopeCount} infoFrom={activeScope} />',
    previous='          <SidebarMenuItems activeScopeCount={activeScopeCount} infoFrom={infoFrom} />',
)
patch(
    "src/components/Sidebar/Sidebar.jsx",
    '''                <CategoryGroup
                  homePageReady={homePageReady}''',
    '''                <CategoryGroup
                  activeScopeCount={activeScopeCount}
                  activeScope={activeScope}
                  activeScopeId={activeScopeId}
                  homePageReady={homePageReady}''',
    previous='                <CategoryGroup\n                  activeScopeCount={activeScopeCount}\n                  activeScope={infoFrom}\n                  activeScopeId={infoId}\n                  homePageReady={homePageReady}',
)

print("Reader entry defaults and active sidebar counts applied.")
