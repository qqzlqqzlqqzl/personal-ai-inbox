"""Reproducible fixes at the native reader / personal overlay boundary."""
from pathlib import Path
import difflib
import shutil

ROOT = Path('/home/ubuntu/ai-news')
WEB = ROOT / 'upstream/reactflux'
BACK = ROOT / 'runtime/reactflux-original'


def patch(name, old, new):
    path = WEB / name
    text = path.read_text()
    if new in text:
        return
    if text.count(old) != 1:
        raise RuntimeError('UI review patch anchor mismatch: ' + name)
    original = BACK / name
    if not original.exists():
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, original)
    path.write_text(text.replace(old, new, 1))


# A status must live inside a presenter, never beside its flex content.
p = WEB / 'src/components/Article/ArticleEntry.jsx'
s = p.read_text().replace('        <CardLanguage entry={entry} />\n', '').replace('import CardLanguage from "@/components/Ai/CardLanguage"\n', '')
p.write_text(s)
for name in ('ArticleGridCard', 'ArticleCard', 'ArticleListItem'):
    path = 'src/components/Article/' + name + '.jsx'
    patch(path, 'import FeedIcon from "@/components/ui/FeedIcon"',
          'import CardLanguage from "@/components/Ai/CardLanguage"\nimport FeedIcon from "@/components/ui/FeedIcon"')
patch('src/components/Article/ArticleGridCard.jsx',
      '        <div className="grid-card-footer">',
      '        <div className="grid-card-footer">\n          <CardLanguage entry={entry} />')
patch('src/components/Article/ArticleCard.jsx',
      '        <h3 className="article-entry-title card-title">{entry.title}</h3>',
      '        <h3 className="article-entry-title card-title">{entry.title}</h3>\n        <CardLanguage entry={entry} />')
patch('src/components/Article/ArticleListItem.jsx',
      '      <div className="list-entry-meta">',
      '      <div className="list-entry-meta">\n        <CardLanguage entry={entry} />')
# Compact AI lists have no body; list layout must use the already available summary too.
patch('src/components/Article/ArticleListItem.jsx',
      'const listSummary = getListSummary(previewContent)',
      'const listSummary = getListSummary(entry.card?.summary || entry.ai?.summary || previewContent)')

search_sort = (WEB / 'src/components/Article/SearchAndSortBar.jsx').read_text()
if 'const aiList = infoFrom === "all" && ai.mode !== "all"' not in search_sort:
    patch('src/components/Article/SearchAndSortBar.jsx',
          'import { settingsState, updateSettings } from "@/store/settingsState"',
          'import { settingsState, updateSettings } from "@/store/settingsState"\nimport { aiState } from "@/store/aiState"')
    patch('src/components/Article/SearchAndSortBar.jsx',
          '  const { orderDirection } = useStore(settingsState, { keys: ["orderDirection"] })',
          '  const { orderDirection } = useStore(settingsState, { keys: ["orderDirection"] })\n  const ai = useStore(aiState)\n  const scoreOrder = infoFrom === "all" && ai.mode === "recommended" && ai.sort !== "time"')
    patch('src/components/Article/SearchAndSortBar.jsx',
          '''  const sortLabel =
    orderDirection === "desc"
      ? polyglot.t("article_list.sort_direction_desc")
      : polyglot.t("article_list.sort_direction_asc")''',
          '''  const sortLabel = scoreOrder
    ? (orderDirection === "desc" ? "高分优先" : "低分优先")
    : (orderDirection === "desc"
      ? polyglot.t("article_list.sort_direction_desc")
      : polyglot.t("article_list.sort_direction_asc"))''')

# Source entry below the actual body and attachments; the existing title link stays intact.
patch('src/components/Article/ArticleDetail.jsx',
      '                {hasOpenedPhotoSlider && (',
      '''                {sourceUrl && (
                  <footer className="article-source-footer">
                    <a href={sourceUrl} target="_blank" rel="noopener noreferrer">
                      打开原始网页 ↗
                    </a>
                    <span>在原站查看评论、互动及最新内容</span>
                  </footer>
                )}
                {hasOpenedPhotoSlider && (''')

# Success must be explicit. On failure or a navigation race, leave existing body untouched.
path = 'src/hooks/useEntryActions.js'
p = WEB / path
s = p.read_text()
start = s.index('  const handleFetchContent = async () => {')
end = s.index('  const handleSaveToThirdPartyServices', start)
block = s[start:end]
if 'return true' not in block:
    block = block.replace('      return\n', '      return false\n')
    block = block.replace('      const currentActiveContent = contentState.get().activeContent',
        '''      if (typeof newContent !== "string" || !newContent.trim()) {
        throw new Error("Empty extracted article body")
      }
      const currentActiveContent = contentState.get().activeContent''', 1)
    block = block.replace('      })\n    } catch', '      })\n      return true\n    } catch', 1)
    block = block.replace('      Message.error(polyglot.t("actions.fetched_content_error"))',
                          '      Message.error(polyglot.t("actions.fetched_content_error"))\n      return false', 1)
    original = BACK / path
    if not original.exists():
        original.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, original)
    p.write_text(s[:start] + block + s[end:])

patch('src/components/Article/ActionButtons.jsx',
      '''  const [isFetchedOriginal, setIsFetchedOriginal] = useState(false)
  const [lastActiveContentId, setLastActiveContentId] = useState(activeContent?.id)''',
      '''  const [fetchedEntryId, setFetchedEntryId] = useState(null)
  const [fetchingEntryId, setFetchingEntryId] = useState(null)
  const fetchRequestRef = useRef(null)
  const isFetchedOriginal = fetchedEntryId === activeContent?.id
  const fetchBusy = fetchingEntryId === activeContent?.id''')
p = WEB / 'src/components/Article/ActionButtons.jsx'
s = p.read_text()
s = s.replace('''  if (activeContent?.id !== lastActiveContentId) {
    setLastActiveContentId(activeContent?.id)
    setIsFetchedOriginal(false)
  }
''', '')
anchor = '  const { exitDetailView, navigateToNextArticle, navigateToPreviousArticle } = useKeyHandlers()'
if 'const fetchOriginalForCurrentEntry' not in s:
    s = s.replace(anchor, '''  const fetchOriginalForCurrentEntry = async () => {
    const id = contentState.get().activeContent?.id
    if (!id || fetchRequestRef.current?.id === id) return
    const request = { id }
    fetchRequestRef.current = request
    setFetchingEntryId(id)
    try {
      const succeeded = await handleFetchContent()
      if (succeeded && contentState.get().activeContent?.id === id && fetchRequestRef.current === request) {
        setFetchedEntryId(id)
      }
    } finally {
      if (fetchRequestRef.current === request) {
        fetchRequestRef.current = null
        setFetchingEntryId(null)
      }
    }
  }

''' + anchor, 1)
    import re
    s, count = re.subn(r'onClick=\{async \(\) => \{\s+await handleFetchContent\(\)\s+setIsFetchedOriginal\(true\)\s+\}\}',
                       'onClick={fetchOriginalForCurrentEntry}', s)
    if count != 2:
        raise RuntimeError('Expected both desktop and menu fetch handlers')
s = s.replace('disabled={isFetchedOriginal}', 'disabled={isFetchedOriginal || fetchBusy}')
p.write_text(s)
patch('src/components/Article/ActionButtons.jsx',
      '          aria-label={actionLabels.fetch}\n          disabled={isFetchedOriginal || fetchBusy}',
      '          aria-label={actionLabels.fetch}\n          loading={fetchBusy}\n          disabled={isFetchedOriginal || fetchBusy}')

patch('src/locales/zh-CN.json', '"fetch_original_tooltip": "获取原文"',
      '"fetch_original_tooltip": "重新抓取正文"')
patch('src/locales/zh-CN.json', '"update_content_on_fetch_label": "获取原文时自动保存"',
      '"update_content_on_fetch_label": "重新抓取正文后保存到服务器"')
patch('src/locales/zh-CN.json', '"update_content_on_fetch_description": "获取原文时自动更新并保存到数据库"',
      '"update_content_on_fetch_description": "关闭时仅更新本次阅读内容；开启后覆盖服务器保存的正文，不影响原站"')

# ID cursor order is not a timestamp bucket: imported entries often share times.
patch('src/hooks/useLoadMore.js',
      '    const referenceEntry = getReferenceEntry(currentEntries)',
      '''    if (sortProperty === "created_at") {
      const boundary = currentEntries.reduce((value, entry) =>
        orderDirection === "desc" ? Math.min(value, entry.id) : Math.max(value, entry.id),
        currentEntries[0].id)
      return orderDirection === "desc" ? { before_entry_id: boundary } : { after_entry_id: boundary }
    }

    const referenceEntry = getReferenceEntry(currentEntries)''')

# Record the final overlay, after every patch stage, not only the first stage.
diffs = []
for original in BACK.rglob('*'):
    if original.is_file():
        relative = str(original.relative_to(BACK))
        modified = WEB / relative
        if modified.is_file():
            diffs.extend(difflib.unified_diff(original.read_text().splitlines(True),
                modified.read_text().splitlines(True), fromfile='a/' + relative, tofile='b/' + relative))
(ROOT / 'patches/reactflux.patch').write_text(''.join(diffs))
print('Card/sort/source and fetch-state fixes applied')
