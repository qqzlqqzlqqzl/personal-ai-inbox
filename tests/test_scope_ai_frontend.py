from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "upstream/reactflux/src"


def read(relative):
    return (ROOT / relative).read_text()


def test_toolbar_never_navigates_away_from_scope():
    toolbar = read("components/Ai/AiToolbar.jsx")
    assert "navigate(" not in toolbar
    assert "全部原始" in toolbar and "AI 精选" in toolbar
    assert 'aria-label="辅助筛选"' in toolbar
    assert "AI 设置 · 来源 · 工具" not in toolbar


def test_every_scope_carries_ai_query():
    entries = read("apis/entries.js")
    assert entries.count("getAiQuery()") >= 6
    assert "get24HoursAgoTimestamp" not in entries
    assert "getTimestamp(getStartOfToday())" in entries


def test_ai_pagination_and_count_are_not_all_only():
    loader = read("hooks/useLoadMore.js")
    content = read("store/contentState.js")
    assert 'infoFrom === "all" && aiFilterEnabled()' not in loader
    assert 'infoFrom === "all" && aiFilterEnabled()' not in content
    assert "const isAiPagination = aiFilterEnabled()" in loader
    assert "if (aiFilterEnabled()) return total" in content


def test_filtered_bulk_read_includes_search_ai_and_today():
    footer = read("components/Content/FooterPanel.jsx")
    assert "Boolean(filterString)" in footer and "aiFilterEnabled()" in footer
    assert 'showStatus === "starred"' in footer
    assert "today: (status, options) => getTodayEntries" in footer
    assert "search: filterString" in footer
    assert "markAllReadJumpToNext && !hasActiveFilter" in footer


def test_single_sorter_and_truthful_search_labels():
    toolbar = read("components/Ai/AiToolbar.jsx")
    search = read("components/Article/SearchAndSortBar.jsx")
    assert 'aria-label="AI 排序"' not in toolbar
    assert "笔记更新时间：新到旧" in search
    assert "搜索标题、AI摘要、理由和标签" in search
    assert "搜索标题、AI分析和个人笔记" in search


def test_tools_tab_removed_from_ai_panel():
    panel = read("components/Ai/AiPanel.jsx")
    assert "工具入口" not in panel
    assert "/v1/ai/tools" not in panel


def test_active_sidebar_count_uses_filtered_total_without_corrupting_native_counts():
    entries = read("apis/entries.js")
    article_list = read("hooks/useArticleList.js")
    sidebar = read("components/Sidebar/Sidebar.jsx")
    assert 'fetchTodayEntries("unread", { limit: 1 }, false, false)' in entries
    assert 'fetchStarredEntries(status, { limit: 1 }, false, false)' in entries
    assert '!content.filterString && !aiFilterEnabled()' in article_list
    assert "const resultCount = useStore(dynamicCountState)" in sidebar
    assert "const resultReady = useStore(articleListResultReadyState)" in sidebar
    assert "const activeScopeCount = resultReady && infoFrom === activeScope" in sidebar
    assert "infoFrom === scope ? activeScopeCount : nativeCountsMatch ? nativeCount : null" in sidebar
    assert 'hydrated === true && mode === "all" && auxiliary === "none"' in sidebar
    assert 'showStatus === "unread" && !filterDate && !filterString' in sidebar
    assert ": nativeCountsMatch ? unreadCount : null" in sidebar
    assert ": nativeCountsMatch ? feed.unreadCount : null" in sidebar
    assert 'contentState.setKey("articleListResultOwner", { requestKey, sessionRevision: requestSessionRevision })' in article_list
    assert 'if (filterString || filterDate)' in read("store/contentState.js")
    assert 'count={scopedCount("today", unreadTodayCount)}' in sidebar
    assert 'activeScope === "feed"' in sidebar
    assert 'activeScope === "category"' in sidebar


def test_ai_sort_direction_does_not_mutate_native_direction():
    search = read("components/Article/SearchAndSortBar.jsx")
    state = read("store/aiState.js")
    assert 'direction: "desc"' in state
    assert 'const sortDirection = aiList ? (ai.direction || "desc") : orderDirection' in search
    assert 'if (aiList) {' in search
    assert 'direction,' in search


def test_inbox_root_starts_today_with_ai_picks():
    home = read("components/HomeRedirect.jsx")
    home_utils = read("utils/home-page.js")
    settings = read("utils/settings-schema.js")
    ai_state = read("store/aiState.js")
    assert 'id: "today"' in home_utils.splitlines()[1]
    assert 'homePage: enumSetting("today"' in settings
    assert 'target.id === "all" ? "/today"' in home
    assert 'setCurrentHomeTarget(createViewHomeTarget("today"))' in home
    assert "resetInboxLandingView()" in home
    assert 'mode: "recommended"' in ai_state
    assert 'auxiliary: "none"' in ai_state
