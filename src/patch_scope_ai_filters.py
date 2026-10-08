"""Make AI a filter over the current ReactFlux scope instead of a special /all page."""
from pathlib import Path
import difflib
import os
import shutil

ROOT = Path(os.environ.get("AI_NEWS_ROOT", "/home/ubuntu/ai-news"))
WEB = ROOT / "upstream/reactflux"
BACK = ROOT / "runtime/reactflux-original"


def backup(name):
    original = BACK / name
    if not original.exists():
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(WEB / name, original)


def patch(name, old, new):
    path = WEB / name
    text = path.read_text()
    if new and new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError(f"scope/AI patch anchor mismatch: {name}")
    backup(name)
    path.write_text(text.replace(old, new, 1))


def normalize(name, variants, new):
    text = (WEB / name).read_text()
    if new in text:
        return
    for old in variants:
        if text.count(old) == 1:
            patch(name, old, new)
            return
    raise RuntimeError(f"scope/AI normalize anchor mismatch: {name}")


# Keep custom components authoritative even when this stage is run on an already-patched tree.
for name in ("AiToolbar.jsx", "AiPanel.jsx", "SourceHistory.jsx", "source-history.js"):
    target = WEB / "src/components/Ai" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "patches" / name, target)
shutil.copy2(ROOT / "patches/aiState.js", WEB / "src/store/aiState.js")

# AI pagination is offset based in every content scope, not only /all.
load_more = WEB / "src/hooks/useLoadMore.js"
text = load_more.read_text()
old = 'infoFrom === "all" && aiFilterEnabled()'
if old in text:
    backup("src/hooks/useLoadMore.js")
    text = text.replace(old, "aiFilterEnabled()")
    load_more.write_text(text)

patch(
    "src/store/contentState.js",
    '    if (infoFrom === "all" && aiFilterEnabled()) return total',
    '    if (aiFilterEnabled()) return total',
)

# Apply the AI query to every scope. Feed/category keep their native URL so the
# gateway can infer scope without duplicating routing logic in the client.
entries = WEB / "src/apis/entries.js"
text = entries.read_text()
if 'getStartOfToday' not in text:
    old_import = 'import { get24HoursAgoTimestamp, getDayEndTimestamp, getTimestamp } from "@/utils/date"'
    new_import = 'import { getDayEndTimestamp, getStartOfToday, getTimestamp } from "@/utils/date"'
    if old_import not in text:
        raise RuntimeError("entries date import anchor missing")
    backup("src/apis/entries.js")
    text = text.replace(old_import, new_import, 1)
text = text.replace('  const timestamp = get24HoursAgoTimestamp()', '  const timestamp = getTimestamp(getStartOfToday())')

# Add getAiQuery to all remaining list fetchers exactly once.
anchors = [
    ('const fetchTodayEntries', '    ...getEntryVisibilityParams(),\n    published_after: timestamp,',
     '    ...getEntryVisibilityParams(),\n    ...getAiQuery(),\n    published_after: timestamp,'),
    ('const fetchStarredEntries', '    ...getEntryVisibilityParams(),\n    starred: true,',
     '    ...getEntryVisibilityParams(),\n    ...getAiQuery(),\n    starred: true,'),
    ('export const getHistoryEntries', '    ...getEntryVisibilityParams(),\n    ...filterParams,',
     '    ...getEntryVisibilityParams(),\n    ...getAiQuery(),\n    ...filterParams,'),
    ('export const getCategoryEntries', '    ...getEntryVisibilityParams(),\n    ...filterParams,',
     '    ...getEntryVisibilityParams(),\n    ...getAiQuery(),\n    ...filterParams,'),
    ('export const getFeedEntries', '  const extraParams = { ...filterParams }',
     '  const extraParams = { ...getAiQuery(), ...filterParams }'),
]
for start_marker, old_snippet, new_snippet in anchors:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError("entries scope anchor missing: " + start_marker)
    end = text.find('\nexport const ', start + len(start_marker))
    if end < 0:
        end = len(text)
    section = text[start:end]
    if new_snippet in section:
        continue
    if start_marker in {"const fetchTodayEntries", "const fetchStarredEntries"} and "applyAiFilter ? getAiQuery()" in section:
        continue
    if old_snippet not in section:
        raise RuntimeError("entries query anchor mismatch: " + start_marker)
    section = section.replace(old_snippet, new_snippet, 1)
    text = text[:start] + section + text[end:]
entries.write_text(text)

# Count-summary calls must not inherit the persistent AI lens.
patch(
    "src/apis/entries.js",
    'const fetchTodayEntries = async (status, filterParams, applyDateFilter) => {',
    'const fetchTodayEntries = async (status, filterParams, applyDateFilter, applyAiFilter = true) => {',
)
patch(
    "src/apis/entries.js",
    '  const extraParams = {\n    ...getEntryVisibilityParams(),\n    ...getAiQuery(),\n    published_after: timestamp,\n    ...filterParams,\n  }',
    '  const extraParams = {\n    ...getEntryVisibilityParams(),\n    ...(applyAiFilter ? getAiQuery() : {}),\n    published_after: timestamp,\n    ...filterParams,\n  }',
)
patch(
    "src/apis/entries.js",
    'const fetchStarredEntries = async (status, filterParams, applyDateFilter = true) => {',
    'const fetchStarredEntries = async (status, filterParams, applyDateFilter = true, applyAiFilter = true) => {',
)
patch(
    "src/apis/entries.js",
    '  const extraParams = {\n    ...getEntryVisibilityParams(),\n    ...getAiQuery(),\n    starred: true,\n    ...filterParams,\n  }',
    '  const extraParams = {\n    ...getEntryVisibilityParams(),\n    ...(applyAiFilter ? getAiQuery() : {}),\n    starred: true,\n    ...filterParams,\n  }',
)
patch(
    "src/apis/entries.js",
    '    : fetchStarredEntries(status, { limit: 1 }, false)',
    '    : fetchStarredEntries(status, { limit: 1 }, false, false)',
)
patch(
    "src/apis/entries.js",
    '    fetchTodayEntries("unread", { limit: 1 }, false),',
    '    fetchTodayEntries("unread", { limit: 1 }, false, false),',
)

# Native sidebar counters remain raw counts; the current page title uses the AI total.
patch(
    "src/hooks/useArticleList.js",
    'import { articleListRequestSettingsState, settingsState } from "@/store/settingsState"',
    'import { articleListRequestSettingsState, settingsState } from "@/store/settingsState"\nimport { aiFilterEnabled } from "@/store/aiState"',
)
patch(
    "src/hooks/useArticleList.js",
    '      if (!content.filterDate && !content.filterString) {',
    '      if (!content.filterDate && !content.filterString && !aiFilterEnabled()) {',
)

# Today uses the authenticated account reading calendar; see the final calendar overlay.
patch(
    "src/pages/Today.jsx",
    'const getEntries = (status, _starred, filterParams) => getTodayEntries(status, filterParams)',
    'const getEntries = (status, starred, filterParams) => getTodayEntries(status, { ...filterParams, ...(starred ? { starred: true } : {}) })',
)

# The checked-in earlier stage installs only a score-order toggle. Older
# deployments already have the selector. Normalize the former explicitly so a
# pristine pinned build does not depend on an untracked intermediate patch.
search_path = "src/components/Article/SearchAndSortBar.jsx"
# The selector changes persistent AI state; the list hook refetches only when
# its revision/settings request key changes. Bind the existing invalidator.
patch(
    search_path,
    '  dynamicCountState,\n  setFilterDate,',
    '  dynamicCountState,\n  invalidateArticleList,\n  setFilterDate,',
)
# Enter can confirm an IME candidate without submitting the article search.
# React carries isComposing on nativeEvent; keyCode 229 covers legacy IMEs.
patch(
    search_path,
    '''  const handleKeyDown = (event) => {
    if (event.key === "Enter") {''',
    '''  const handleKeyDown = (event) => {
    if (event.isComposing || event.nativeEvent?.isComposing || event.keyCode === 229) {
      return
    }
    if (event.key === "Enter") {''',
)
if '  const scoreOrder =' in (WEB / search_path).read_text():
    patch(search_path,
          '  const { orderDirection } = useStore(settingsState, { keys: ["orderDirection"] })',
          '  const { orderBy, orderDirection } = useStore(settingsState, { keys: ["orderBy", "orderDirection"] })')
    patch(search_path,
          '''  const sortLabel = scoreOrder
    ? (orderDirection === "desc" ? "高分优先" : "低分优先")
    : (orderDirection === "desc"
      ? polyglot.t("article_list.sort_direction_desc")
      : polyglot.t("article_list.sort_direction_asc"))
''', "")
    patch(search_path,
          '''  const toggleOrderDirection = () => {
    const newOrderDirection = orderDirection === "desc" ? "asc" : "desc"
    updateSettings({ orderDirection: newOrderDirection })
  }''',
          '''  const changeSort = (event) => {
    const value = event.target.value
    const split = value.lastIndexOf("_")
    const field = value.slice(0, split)
    const direction = value.slice(split + 1)
    if (aiList) aiState.setKey("sort", field === "published_at" ? "time" : field)
    updateSettings({ orderDirection: direction, ...(!aiList && !activityList ? { orderBy: field } : {}) })
    closeActiveContent()
    entryListRef.current?.getScrollElement()?.scroll({ top: 0 })
    invalidateArticleList()
  }''')
    patch(search_path,
          '''        <CustomTooltip mini content={sortLabel}>
          <Button
            aria-label={sortLabel}
            shape="circle"
            size="small"
            icon={
              orderDirection === "desc" ? (
                <IconSortDescending aria-hidden="true" />
              ) : (
                <IconSortAscending aria-hidden="true" />
              )
            }
            onClick={toggleOrderDirection}
          />
        </CustomTooltip>''',
          '''        <select className="ai-sort-select" aria-label="排序方式" value={sortValue} onChange={changeSort}>
          {sortOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>''')
    patch(search_path, '  IconSortAscending,\n', '')
    patch(search_path, '  IconSortDescending,\n', '')

# da5's recorded aggregate patch retained this earlier selector spelling. An
# existing selector skips the creation branch above, so normalize its exact
# reviewed opening tag too. The final installer still verifies the whole file.
normalize(
    search_path,
    ['<select className="article-sort-select" aria-label="文章排序" value={sortValue} onChange={changeSort}>'],
    '<select className="ai-sort-select" aria-label="排序方式" value={sortValue} onChange={changeSort}>',
)

# Search/sort is the single sorting surface; the AI toolbar no longer owns a second sorter.
patch(
    "src/components/Article/SearchAndSortBar.jsx",
    'import { aiState } from "@/store/aiState"',
    'import { aiFilterEnabled, aiState } from "@/store/aiState"',
)
normalize(
    search_path,
    [
    '''const SearchModal = memo(({ value, visible, onCancel, onConfirm, onChange }) => {
  const { polyglot } = useStore(polyglotState)
  const tooltipLines = polyglot.t("search.article_tooltip").split("\\n")''',
    '''const SearchModal = memo(({ aiSearch, notesSearch, value, visible, onCancel, onConfirm, onChange }) => {
  const { polyglot } = useStore(polyglotState)
  const aiSearchLabel = notesSearch
    ? "搜索标题、AI分析和个人笔记"
    : "搜索标题、AI摘要、理由和标签"
  const tooltipLines = aiSearch ? [aiSearchLabel] : polyglot.t("search.article_tooltip").split("\\n")
  const inputLabel = aiSearch ? aiSearchLabel : polyglot.t("search.article_input_label")
  const placeholder = aiSearch ? `${aiSearchLabel}...` : polyglot.t("search.article_placeholder")''',
    ],
    '''const SearchModal = memo(({ aiSearch, notesSearch, value, visible, onCancel, onConfirm, onChange, returnFocusRef }) => {
  const { polyglot } = useStore(polyglotState)
  const aiSearchLabel = notesSearch
    ? "搜索标题、AI分析和个人笔记"
    : "搜索标题、AI摘要、理由和标签"
  const tooltipLines = aiSearch ? [aiSearchLabel] : polyglot.t("search.article_tooltip").split("\\n")
  const inputLabel = aiSearch ? aiSearchLabel : polyglot.t("search.article_input_label")
  const placeholder = aiSearch ? `${aiSearchLabel}...` : polyglot.t("search.article_placeholder")''',
)
patch(
    "src/components/Article/SearchAndSortBar.jsx",
    '          aria-label={polyglot.t("search.article_input_label")}\n          placeholder={polyglot.t("search.article_placeholder")}',
    '          aria-label={inputLabel}\n          placeholder={placeholder}',
)
normalize(
    "src/components/Article/SearchAndSortBar.jsx",
    [
    '''  const ai = useStore(aiState)
  const aiList = infoFrom === "all" && ai.mode !== "all"
  const activityList = infoFrom === "history" || infoFrom === "starred"
  const sortOptions = aiList
    ? [...(ai.mode === "recommended" ? [
      ["score_desc", "推荐分：高到低"], ["score_asc", "推荐分：低到高"],
      ["technical_desc", "技术价值：高到低"], ["technical_asc", "技术价值：低到高"],
      ["business_desc", "商业启发：高到低"], ["business_asc", "商业启发：低到高"],
    ] : []), ["published_at_desc", "发布时间：新到旧"], ["published_at_asc", "发布时间：旧到新"]]
    : activityList
      ? [["changed_at_desc", "操作时间：新到旧"], ["changed_at_asc", "操作时间：旧到新"]]
      : [["published_at_desc", "发布时间：新到旧"], ["published_at_asc", "发布时间：旧到新"],
        ["created_at_desc", "收录时间：新到旧"], ["created_at_asc", "收录时间：旧到新"]]
  const sortField = aiList
    ? (ai.mode === "pending" || ai.sort === "time" ? "published_at" : ai.sort)
    : (activityList ? "changed_at" : orderBy)''',
    '''  const ai = useStore(aiState)
  const scoreOrder = infoFrom === "all" && ai.mode === "recommended" && ai.sort !== "time"''',
    ],
    '''  const ai = useStore(aiState)
  const aiList = aiFilterEnabled()
  const pendingList = ai.auxiliary === "pending"
  const notesList = ai.auxiliary === "notes"
  const recommendedList = ai.mode === "recommended" && !pendingList
  const activityList = infoFrom === "history" || infoFrom === "starred"
  const sortOptions = aiList
    ? [
        ...(recommendedList ? [
          ["score_desc", "推荐分：高到低"], ["score_asc", "推荐分：低到高"],
          ["technical_desc", "技术价值：高到低"], ["technical_asc", "技术价值：低到高"],
          ["business_desc", "商业启发：高到低"], ["business_asc", "商业启发：低到高"],
        ] : []),
        ...(notesList ? [
          ["note_updated_desc", "笔记更新时间：新到旧"],
          ["note_updated_asc", "笔记更新时间：旧到新"],
        ] : []),
        ["published_at_desc", "发布时间：新到旧"],
        ["published_at_asc", "发布时间：旧到新"],
      ]
    : activityList
      ? [["changed_at_desc", "操作时间：新到旧"], ["changed_at_asc", "操作时间：旧到新"]]
      : [["published_at_desc", "发布时间：新到旧"], ["published_at_asc", "发布时间：旧到新"],
        ["created_at_desc", "收录时间：新到旧"], ["created_at_asc", "收录时间：旧到新"]]
  const sortField = aiList
    ? (pendingList
      ? "published_at"
      : (notesList && ((!recommendedList && ai.sort !== "time") || ai.sort === "note_updated"))
        ? "note_updated"
        : recommendedList && ai.sort !== "time" ? ai.sort : "published_at")
    : (activityList ? "changed_at" : orderBy)''',
)
if '  const sortValue =' not in (WEB / search_path).read_text():
    patch(search_path,
          '    : (activityList ? "changed_at" : orderBy)\n  const { polyglot }',
          '    : (activityList ? "changed_at" : orderBy)\n  const sortValue = `${sortField}_${orderDirection}`\n  const { polyglot }')

patch(
    "src/components/Article/SearchAndSortBar.jsx",
    '  const sortValue = `${sortField}_${orderDirection}`',
    '  const sortDirection = aiList ? (ai.direction || "desc") : orderDirection\n  const sortValue = `${sortField}_${sortDirection}`',
)
patch(
    "src/components/Article/SearchAndSortBar.jsx",
    '    if (aiList) aiState.setKey("sort", field === "published_at" ? "time" : field)\n    updateSettings({ orderDirection: direction, ...(!aiList && !activityList ? { orderBy: field } : {}) })',
    '''    if (aiList) {
      aiState.set({
        ...aiState.get(),
        sort: field === "published_at" ? "time" : field,
        direction,
      })
    } else {
      updateSettings({
        orderDirection: direction,
        ...(!activityList ? { orderBy: field } : {}),
      })
    }''',
)
patch(
    "src/components/Article/SearchAndSortBar.jsx",
    '''    return { title: info.key ? polyglot.t(info.key) : "", count: info.count }
  }, [infoFrom, id, categories, feeds, dynamicCount, polyglot])

  const changeSort = (event) => {''',
    '''    return { title: info.key ? polyglot.t(info.key) : "", count: info.count }
  }, [infoFrom, id, categories, feeds, dynamicCount, polyglot])
  const aiModeLabel = pendingList
    ? "待处理 / 异常"
    : notesList
      ? (recommendedList ? "AI精选 · 有笔记" : "有笔记")
      : recommendedList ? "AI精选" : ""
  const displayTitle = title && aiModeLabel ? `${title} · ${aiModeLabel}` : title

  const changeSort = (event) => {''',
)
patch(
    "src/components/Article/SearchAndSortBar.jsx",
    '              {title}\n',
    '              {displayTitle}\n',
)
normalize(
    search_path,
    [
    '''      <SearchModal
        value={modalInputValue}''',
    '''      <SearchModal
        aiSearch={aiList}
        notesSearch={notesList}
        value={modalInputValue}''',
    ],
    '''      <SearchModal
        aiSearch={aiList}
        notesSearch={notesList}
        returnFocusRef={searchOpenerRef}
        value={modalInputValue}''',
)

# Capture the search opener before a retained modal's child focus lock runs.
# AccessibleModal's optional returnFocusRef is installed by the reviewed overlay.
patch(
    search_path,
    '''      closeLabel={polyglot.t("actions.close_dialog", { name: modalTitle })}
      title={modalTitle}''',
    '''      closeLabel={polyglot.t("actions.close_dialog", { name: modalTitle })}
      fallbackFocusSelector=".reader-search-trigger"
      returnFocusRef={returnFocusRef}
      title={modalTitle}''',
)
patch(
    search_path,
    '''      aria-label={tooltip}
      icon={icon}''',
    '''      aria-label={tooltip}
      className="reader-search-trigger"
      icon={icon}''',
)
# The search hint duplicates the button's accessible name. A native title keeps
# that hover label without a body portal mixing scaled/unscaled zoom coordinates.
# Only ActiveButton's search tooltip matches these anchors; other tooltips stay.
patch(
    search_path,
    'const ActiveButton = ({ active, expanded, icon, tooltip, onClick }) => (\n  <CustomTooltip mini content={tooltip}>',
    'const ActiveButton = ({ active, expanded, icon, tooltip, onClick }) => (\n  <>',
)
patch(search_path, '    />\n  </CustomTooltip>\n)', '    />\n  </>\n)')
patch(
    search_path,
    '''      size="small"
      style={{''',
    '''      size="small"
      title={tooltip}
      style={{''',
)
patch(
    search_path,
    '  const [modalInputValue, setModalInputValue] = useState("")',
    '  const [modalInputValue, setModalInputValue] = useState("")\n  const searchOpenerRef = useRef(null)',
)
patch(
    search_path,
    '  const openSearchModal = () => {\n    setModalInputValue(filterString)',
    '  const openSearchModal = () => {\n    searchOpenerRef.current = document.activeElement\n    setModalInputValue(filterString)',
)

# Bulk mark-read must use exactly the visible result set.
shutil.copy2(ROOT / "patches/bulk-read-label.js", WEB / "src/utils/bulk-read-label.js")
patch(
    "src/components/Content/FooterPanel.jsx",
    'import findAdjacentItem from "@/utils/navigation"',
    'import findAdjacentItem from "@/utils/navigation"\nimport { bulkReadLabel } from "@/utils/bulk-read-label"',
)
patch(
    "src/components/Content/FooterPanel.jsx",
    '''  getFeedEntries,
  getStarredEntries,
  markEntriesAsReadInBatches,''',
    '''  getFeedEntries,
  getStarredEntries,
  getTodayEntries,
  markEntriesAsReadInBatches,''',
)
patch(
    "src/components/Content/FooterPanel.jsx",
    'import { settingsState, updateSettings } from "@/store/settingsState"',
    'import { settingsState, updateSettings } from "@/store/settingsState"\nimport { aiFilterEnabled } from "@/store/aiState"',
)
patch(
    "src/components/Content/FooterPanel.jsx",
    '  const { markAllReadJumpToNext, showStatus } = useStore(settingsState, {\n    keys: ["markAllReadJumpToNext", "showStatus"],\n  })',
    '  const { markAllReadJumpToNext, showStatus } = useStore(settingsState, {\n    keys: ["markAllReadJumpToNext", "showStatus"],\n  })\n  const hasActiveFilter =\n    Boolean(filterString) ||\n    aiFilterEnabled() ||\n    showStatus === "starred" ||\n    Boolean(filterDate && source !== "today")',
)
patch(
    "src/components/Content/FooterPanel.jsx",
    'const MarkAllReadButton = ({ from, onConfirm }) => {',
    'const MarkAllReadButton = ({ filtered, from, onConfirm }) => {',
)
normalize(
    "src/components/Content/FooterPanel.jsx",
    ['  const markAllReadLabel = polyglot.t("article_list.mark_all_as_read_tooltip")',
     '''  const markAllReadLabel = filtered
    ? "标记当前筛选结果为已读"
    : polyglot.t("article_list.mark_all_as_read_tooltip")'''],
    '  const markAllReadLabel = bulkReadLabel(from, filtered)',
)
normalize(
    "src/components/Content/FooterPanel.jsx",
    ['      title={polyglot.t("article_list.mark_all_as_read_confirm")}',
     '      title={filtered ? "标记当前筛选结果为已读？" : polyglot.t("article_list.mark_all_as_read_confirm")}'],
    '      title={`${markAllReadLabel}？`}',
)
patch(
    "src/components/Content/FooterPanel.jsx",
    '''  const { filterDate, isArticleListReady } = useStore(contentState, {
    keys: ["filterDate", "isArticleListReady"],
  })''',
    '''  const { filterDate, filterString, isArticleListReady } = useStore(contentState, {
    keys: ["filterDate", "filterString", "isArticleListReady"],
  })
''',
)
patch(
    "src/components/Content/FooterPanel.jsx",
    '      await (filterDate && source !== "today" ? handleFilteredMarkAsRead() : markAllAsRead())',
    '      await (hasActiveFilter ? handleFilteredMarkAsRead() : markAllAsRead())',
)
patch(
    "src/components/Content/FooterPanel.jsx",
    '''      if (markAllReadJumpToNext) {
        jumpToNext()
      }''',
    '''      if (markAllReadJumpToNext && !hasActiveFilter) {
        jumpToNext()
      }''',
)
patch(
    "src/components/Content/FooterPanel.jsx",
    '''  const handleFilteredMarkAsRead = async () => {
    const starred = showStatus === "starred"
    const includeEntireScope = isEntryScopeFullyVisible(source, sourceId)
    const withScopeVisibility = (options) =>
      includeEntireScope ? { ...options, globally_visible: false } : options

    const entryFetchers = {
      all: getAllEntries,
      feed: (status, options) =>
        getFeedEntries(sourceId, status, starred, withScopeVisibility(options)),
      category: (status, options) =>
        getCategoryEntries(sourceId, status, starred, withScopeVisibility(options)),
      starred: getStarredEntries,
    }

    const fetchEntries = entryFetchers[source]
    return markEntriesAsReadInBatches(fetchEntries)
  }''',
    '''  const handleFilteredMarkAsRead = async () => {
    const starred = showStatus === "starred"
    const includeEntireScope = isEntryScopeFullyVisible(source, sourceId)
    const withFilters = (options) => ({
      ...(filterString ? { search: filterString } : {}),
      ...(includeEntireScope ? { ...options, globally_visible: false } : options),
    })

    const entryFetchers = {
      all: (status, options) => getAllEntries(status, withFilters(options)),
      today: (status, options) => getTodayEntries(status, withFilters(options)),
      feed: (status, options) =>
        getFeedEntries(sourceId, status, starred, withFilters(options)),
      category: (status, options) =>
        getCategoryEntries(sourceId, status, starred, withFilters(options)),
      starred: (status, options) => getStarredEntries(status, withFilters(options)),
    }

    const fetchEntries = entryFetchers[source]
    if (!fetchEntries) throw new Error("当前视图不支持批量标记")
    return markEntriesAsReadInBatches(fetchEntries)
  }''',
)
patch(
    "src/components/Content/FooterPanel.jsx",
    '      <MarkAllReadButton from={source} onConfirm={handleMarkAllAsRead} />',
    '      <MarkAllReadButton filtered={hasActiveFilter} from={source} onConfirm={handleMarkAllAsRead} />',
)

# Empty states should explain the AI condition, not claim the scope has no unread articles.
patch(
    "src/components/Article/ArticleList.jsx",
    'import { articleListEmptyState } from "@/store/articleListEmptyState"',
    'import { articleListEmptyState } from "@/store/articleListEmptyState"\nimport { aiFilterEnabled, aiState } from "@/store/aiState"',
)
patch(
    "src/components/Article/ArticleList.jsx",
    '''    const emptyState = useStore(articleListEmptyState)
    const filteredEntries = useStore(filteredEntriesState)''',
    '''    const emptyState = useStore(articleListEmptyState)
    const ai = useStore(aiState)
    const filteredEntries = useStore(filteredEntriesState)''',
)
patch(
    "src/components/Article/ArticleList.jsx",
    '''    const emptyMessageKey = EMPTY_MESSAGE_KEYS[emptyState?.reason] ?? "article_list.no_articles"
    const emptySummary = showEmptyState ? getEmptyStateSummary(emptyState.filters, polyglot) : null''',
    '''    const aiEmptyMessageKey = aiFilterEnabled() && !emptyState?.filters?.query && !emptyState?.filters?.date
      ? (ai.auxiliary === "pending"
        ? "article_list.no_pending_articles"
        : ai.auxiliary === "notes"
          ? "article_list.no_noted_articles"
          : "article_list.no_ai_recommendations")
      : null
    const emptyMessageKey =
      aiEmptyMessageKey ?? EMPTY_MESSAGE_KEYS[emptyState?.reason] ?? "article_list.no_articles"
    const emptySummary = showEmptyState ? getEmptyStateSummary(emptyState.filters, polyglot) : null''',
)

# Personal Chinese UI copy; English fallback is supplied for the upstream locale.
for locale, anchor, additions in [
    ("zh-CN.json", '"no_articles": "没有文章",',
     '"no_articles": "没有文章",\n    "no_ai_recommendations": "当前范围暂无达到筛选条件的 AI 精选",\n    "no_noted_articles": "当前范围暂无符合条件的笔记文章",\n    "no_pending_articles": "当前范围暂无待处理或异常文章",'),
    ("en-US.json", '"no_articles": "No articles",',
     '"no_articles": "No articles",\n    "no_ai_recommendations": "No AI picks match the current scope and filters",\n    "no_noted_articles": "No noted articles match the current scope and filters",\n    "no_pending_articles": "No pending or failed articles match the current scope",'),
]:
    path = f"src/locales/{locale}"
    locale_file = WEB / path
    locale_text = locale_file.read_text()
    if additions not in locale_text:
        if anchor not in locale_text:
            raise RuntimeError("locale empty-state anchor missing: " + locale)
        backup(path)
        locale_file.write_text(locale_text.replace(anchor, additions, 1))

# Calendar inputs are checked in; the helper uses the same strict/idempotent anchors.
exec(compile((ROOT / "patches/calendar-overlay.py").read_text(), str(ROOT / "patches/calendar-overlay.py"), "exec"))

# One authenticated, same-lens batch supplies every sidebar scope on first open.
# Navigation does not start another request; changed identity/filters cancel it.
SIDEBAR_SCOPE_COUNTS = '''import { atom, computed } from "nanostores"
import apiClient from "@/apis/ofetch"
import { aiState, getAiQuery } from "@/store/aiState"
import { authState } from "@/store/authState"
import { contentState } from "@/store/contentState"
import { dataState } from "@/store/dataState"
import { readingCalendarKeyState, getReadingCalendarSnapshot } from "@/store/readingCalendarState"
import { settingsState } from "@/store/settingsState"
import { getCalendarStartTimestamp, getDayEndTimestamp } from "@/utils/date"

const result = atom({ key: null, counts: null })
const requestSnapshot = () => {
  const calendar = getReadingCalendarSnapshot()
  const ai = aiState.get()
  const settings = settingsState.get()
  const content = contentState.get()
  const query = { ...getAiQuery() }
  delete query.limit
  const ready = calendar.ready && ai.hydrated === true && Boolean(query.ai_view)
  if (ready) {
    query.today_after = calendar.cutoff
    query.globally_visible = !settings.showHiddenFeeds
    if (settings.showStatus === "unread") query.status = "unread"
    if (settings.showStatus === "starred") query.starred = true
    if (content.filterString) query.search = content.filterString
    if (content.filterDate) {
      query.date_after = getCalendarStartTimestamp(content.filterDate)
      query.date_before = getDayEndTimestamp(content.filterDate)
      query.date_field = settings.orderBy
    }
  }
  return { ready, query, key: JSON.stringify([calendar.key, ai.hydrated, query,
    settings.showStatus, settings.showHiddenFeeds, content.filterDate, content.filterString,
    content.articleListRevision]) }
}
const requestState = computed(
  [authState, dataState, aiState, contentState, settingsState, readingCalendarKeyState],
  requestSnapshot,
)
export const sidebarScopeCountsState = computed([result, requestState], (value, current) =>
  current.ready && value.key === current.key ? value.counts : null,
)

export const validateScopeCounts = (response) => {
  const counts = response?.scope_counts
  const validCount = value => Number.isSafeInteger(value) && value >= 0
  if (!counts || !["all", "today", "starred", "history"].every(scope => validCount(counts[scope])))
    throw new TypeError("Invalid sidebar scope counts")
  const validated = Object.fromEntries(["all", "today", "starred", "history"].map(scope => [scope, counts[scope]]))
  for (const scope of ["category", "feed"]) {
    const entries = counts[scope]
    if (!entries || typeof entries !== "object" || Array.isArray(entries) || Object.keys(entries).length > 10000)
      throw new TypeError("Invalid sidebar entity counts")
    validated[scope] = Object.fromEntries(Object.entries(entries).map(([id, count]) => {
      if (!Number.isSafeInteger(Number(id)) || Number(id) <= 0 || String(Number(id)) !== id || !validCount(count))
        throw new TypeError("Invalid sidebar entity count")
      return [id, count]
    }))
  }
  return validated
}

export const startSidebarScopeCounts = () => {
  let active = true
  let previousKey
  let controller
  const load = current => {
    if (current.key === previousKey) return
    previousKey = current.key
    controller?.abort()
    result.set({ key: null, counts: null })
    if (!current.ready) return
    const pending = new AbortController()
    controller = pending
    void apiClient.get(`/v1/ai/scope-counts?${new URLSearchParams(current.query)}`, {
      retry: 0, timeout: 15000, signal: pending.signal,
    }).then(response => {
      if (active && !pending.signal.aborted && requestSnapshot().key === current.key)
        result.set({ key: current.key, counts: validateScopeCounts(response) })
    }).catch(() => {
      // A failed/incomplete batch stays unknown; it never becomes a false zero.
    })
  }
  let queued = false
  const unlisten = requestState.subscribe(() => {
    if (queued) return
    queued = true
    // Hydrating preferences can invalidate the list in the same turn.
    queueMicrotask(() => { queued = false; if (active) load(requestSnapshot()) })
  })
  return () => { active = false; controller?.abort(); unlisten(); result.set({ key: null, counts: null }) }
}
'''
scope_counts_path = WEB / "src/store/sidebarScopeCountsState.js"
if scope_counts_path.exists() and scope_counts_path.read_text() != SIDEBAR_SCOPE_COUNTS:
    raise RuntimeError("Unreviewed sidebar scope counts module")
scope_counts_path.write_text(SIDEBAR_SCOPE_COUNTS)
patch("src/components/AppDataProvider.jsx", 'import { getAuthSessionKey } from "@/utils/auth"',
      'import { startSidebarScopeCounts } from "@/store/sidebarScopeCountsState"\nimport { getAuthSessionKey } from "@/utils/auth"')
patch("src/components/AppDataProvider.jsx", '  const [coordinator] = useState(createAppDataCoordinator)',
      '  const [coordinator] = useState(createAppDataCoordinator)\n\n  useEffect(startSidebarScopeCounts, [])')

# A retained total belongs to the query/session that produced it, not to whichever
# route happens to be selected while the next request is pending. Keep the existing
# request-id, mutation and pagination guards; publish ownership only after those
# guards and response validation have accepted an initial result.
patch("src/store/contentState.js", '  articleListError: false,',
      '  articleListError: false,\n  articleListResultOwner: null,')
patch("src/store/contentState.js", 'export const dynamicCountState = computed(',
      '''export const isArticleListResultCurrent = (content, settings, sessionRevision) =>
  Boolean(content.isArticleListReady && !content.articleListError &&
    content.articleListResultOwner?.sessionRevision === sessionRevision &&
    content.articleListResultOwner?.requestKey === createArticleListRequestKey({ content, settings }))

export const dynamicCountState = computed(''')
patch("src/store/contentState.js", 'import { settingsState } from "./settingsState"',
      '''import { settingsState } from "./settingsState"
import createArticleListRequestKey from "@/utils/article-list-request-key"
import { readingCalendarKeyState } from "@/store/readingCalendarState"''')
patch("src/store/contentState.js", 'export const activeEntryIndexState = computed(',
      '''export const articleListResultReadyState = computed(
  [contentState, settingsState, dataState, aiState, readingCalendarKeyState],
  (content, settings, data) => isArticleListResultCurrent(content, settings, data.sessionRevision),
)

export const activeEntryIndexState = computed(''')
normalize("src/store/contentState.js", [
      '''    if (!isArticleListResultCurrent(content, settings, data.sessionRevision)) return null
    const { filterString, infoFrom, total } = content''',
      '    const { filterString, infoFrom, total } = content'],
      '''    if (!isArticleListResultCurrent(content, settings, data.sessionRevision)) return null
    const { filterDate, filterString, infoFrom, total } = content''')
patch("src/store/contentState.js", '    if (filterString) {', '    if (filterString || filterDate) {')
patch("src/hooks/useArticleList.js", '      handleResponses(response)',
      '''      handleResponses(response)
      contentState.setKey("articleListResultOwner", { requestKey, sessionRevision: requestSessionRevision })''')
patch("src/hooks/useArticleList.js", '      setArticleListError(true)',
      '''      contentState.setKey("articleListResultOwner", null)
      setArticleListError(true)''')

# Rebuild the tracked patch after the final overlay stage.
diffs = []
for original in BACK.rglob("*"):
    if original.is_file():
        relative = str(original.relative_to(BACK))
        modified = WEB / relative
        if modified.is_file():
            diffs.extend(difflib.unified_diff(
                original.read_text().splitlines(True),
                modified.read_text().splitlines(True),
                fromfile="a/" + relative,
                tofile="b/" + relative,
            ))
(ROOT / "patches/reactflux.patch").write_text("".join(diffs))
print("Scope × AI filter overlay applied")
