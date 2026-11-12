"""Apply a small, pinned and reproducible ReactFlux overlay; refuse unknown bases."""
from pathlib import Path
import shutil, difflib
ROOT=Path('/home/ubuntu/ai-news'); WEB=ROOT/'upstream/reactflux'
EXPECTED='534eeb97723ac11025de4ec1ac56335072e3be52'
if (WEB/'UPSTREAM_REVISION').read_text().splitlines()[-1]!=EXPECTED: raise RuntimeError('Unexpected ReactFlux revision')
BACK=ROOT/'runtime/reactflux-original'
def backup(name):
 target=BACK/name
 if not target.exists(): target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(WEB/name,target)
def patch(name,old,new):
 path=WEB/name; text=path.read_text()
 if new in text: return
 if text.count(old)!=1: raise RuntimeError(f'Patch anchor mismatch: {name}')
 backup(name); path.write_text(text.replace(old,new,1))
def normalize(name,variants,new):
 path=WEB/name; text=path.read_text()
 if new in text: return
 for old in variants:
  if text.count(old)==1:
   backup(name); path.write_text(text.replace(old,new,1)); return
 raise RuntimeError(f'Normalize anchor mismatch: {name}')
for name in ['AiBadge.jsx','AiToolbar.jsx','AiPanel.jsx','SourceHistory.jsx','source-history.js','AiNews.css']:
 target=WEB/'src/components/Ai'/name; target.parent.mkdir(exist_ok=True)
 shutil.copy2(ROOT/'patches'/name,target)
shutil.copy2(ROOT/'patches/aiState.js',WEB/'src/store/aiState.js')
patch('src/components/Content/Content.jsx','import FooterPanel from "./FooterPanel"','import FooterPanel from "./FooterPanel"\nimport AiToolbar from "@/components/Ai/AiToolbar"')
patch('src/components/Content/Content.jsx','        <SearchAndSortBar fullWidth={isFullWidthLayout} />','        <AiToolbar source={source} />\n        <SearchAndSortBar fullWidth={isFullWidthLayout} />')
patch('src/apis/entries.js','import apiClient from "./ofetch"','import apiClient from "./ofetch"\nimport { getAiQuery } from "@/store/aiState"')
name='src/apis/entries.js'; text=(WEB/name).read_text(); start=text.index('export const getAllEntries ='); end=text.index('const fetchTodayEntries',start)
section=text[start:end]
if '...getAiQuery()' not in section:
 section=section.replace('...getEntryVisibilityParams(),','...getEntryVisibilityParams(),\n    ...getAiQuery(),',1)
 (WEB/name).write_text(text[:start]+section+text[end:])
patch('src/pages/All.jsx','import Content from "@/components/Content/Content"','import Content from "@/components/Content/Content"\nimport { aiFilterEnabled } from "@/store/aiState"')
patch('src/pages/All.jsx','isEntryScopeFullyVisible("global")\n    ?','isEntryScopeFullyVisible("global") && !aiFilterEnabled()\n    ?')
patch('src/pages/All.jsx','const getEntries = (status, _starred, filterParams) => getAllEntries(status, filterParams)','const getEntries = (status, starred, filterParams) => getAllEntries(status, { ...filterParams, ...(starred ? { starred: true } : {}) })')
patch('src/hooks/useLoadMore.js','import { settingsState } from "@/store/settingsState"','import { settingsState } from "@/store/settingsState"\nimport { aiFilterEnabled, AI_PAGE_SIZE } from "@/store/aiState"')
if 'articleListAiRevision' not in (WEB/'src/hooks/useLoadMore.js').read_text():
    normalize('src/hooks/useLoadMore.js',[
     '  const getFilterParams = (currentEntries) => {\n    if (currentEntries.length === 0) {',
     '  const getFilterParams = (currentEntries) => {\n    if (infoFrom === "all" && aiFilterEnabled()) {\n      return { offset: contentState.get().articleListOffset, limit: AI_PAGE_SIZE }\n    }\n    if (currentEntries.length === 0) {',
    ],'  const getFilterParams = (currentEntries) => {\n    if (aiFilterEnabled()) {\n      return { offset: contentState.get().articleListOffset, limit: AI_PAGE_SIZE }\n    }\n    if (currentEntries.length === 0) {')
normalize('src/hooks/useLoadMore.js',[
 '      if (response.total <= pageSize || response.entries.length < pageSize) {',
 '      const effectivePageSize = infoFrom === "all" && aiFilterEnabled() ? AI_PAGE_SIZE : pageSize\n      const loadedCount = isAiPagination ? contentState.get().articleListOffset : contentState.get().entries.length\n      if ((isAiPagination ? response.total <= loadedCount : response.total <= effectivePageSize) || response.entries.length < effectivePageSize) {',
],'      const effectivePageSize = aiFilterEnabled() ? AI_PAGE_SIZE : pageSize\n      const loadedCount = isAiPagination ? contentState.get().articleListOffset : contentState.get().entries.length\n      if ((isAiPagination ? response.total <= loadedCount : response.total <= effectivePageSize) || response.entries.length < effectivePageSize) {')
patch('src/store/contentState.js','import { computed, map } from "nanostores"','import { computed, map } from "nanostores"\nimport { aiState, aiFilterEnabled } from "./aiState"')
patch('src/store/contentState.js','[contentState, dataState, unreadTotalState, settingsState, feedsState, visibleFeedsState],','[contentState, dataState, unreadTotalState, settingsState, feedsState, visibleFeedsState, aiState],')
normalize('src/store/contentState.js',[
 '    const { showStatus } = settings\n    if (infoFrom === "all" && aiFilterEnabled()) return total\n',
 '    const { showStatus } = settings\n',
],'    const { showStatus } = settings\n    if (aiFilterEnabled()) return total\n')
for name in ['ArticleGridCard','ArticleCard','ArticleDetail']:
 path='src/components/Article/'+name+'.jsx'; text=(WEB/path).read_text()
 if 'import AiBadge ' not in text:
  backup(path); (WEB/path).write_text('import AiBadge from "@/components/Ai/AiBadge"\n'+text)
patch('src/components/Article/ArticleGridCard.jsx','const cardSummary = getCardSummary(previewContent)','const cardSummary = getCardSummary(entry.card?.summary || (entry.ai?.state === "done" ? entry.ai.summary : previewContent))')
patch('src/components/Article/ArticleGridCard.jsx','        <div className="grid-card-footer">','        <AiBadge entry={entry} />\n        <div className="grid-card-footer">')
patch('src/components/Article/ArticleCard.jsx','              {previewContent}','              {entry.card?.summary || (entry.ai?.state === "done" ? entry.ai.summary : previewContent)}')
patch('src/components/Article/ArticleCard.jsx','      </div>\n    </div>\n  )','      </div>\n      <AiBadge entry={entry} />\n    </div>\n  )')
patch('src/components/Article/ArticleDetail.jsx','            <Divider />\n          </div>','            <AiBadge entry={activeContent} detailed />\n            <Divider />\n          </div>')
patch('src/utils/entry-presentation.js','  const coverSource =\n    firstImage?.getAttribute("src") ||','  const coverSource =\n    entry.ai?.cover_url ||\n    firstImage?.getAttribute("src") ||')
def install_reader_entry_detail(root, web):
    """Install the exact native/reviewed loading blocks and owned helper only."""
    import hashlib
    name = 'src/components/Content/Content.jsx'
    path = web / name
    text = path.read_text()
    helper = (root / 'patches/reader-entry-detail.js').read_bytes()
    target = web / 'src/utils/reader-entry-detail.js'
    previous_helper_shas = {
        '6fd755d1c5723c70779580755cb19163ec9e8a1e798b6d05dac08d072da65016',
        '17bce4248c73d81574c82c6570edc18db786520b095785f7dc4eb1c484b6f206',
        '82cc620457d6ee140e90a1edc85523d3c39532d5b51c36d0b9d26bf1cecc9ee4',
        '4a68e34d40cc4e57d79d91cb39c89f05a06db083986232d73202ac4832ae1bb3',
        'e9c9eb1d88b683e66428bfd099cbd3fca9f91e27a0cc679a372f882c8e0e2098',
        '75b82cc3b4b40ce113d91725c4869971458be301cb6da8213125795efe455945',
        '5680f6975a030c29c80e918f9f409cf4e3e48d1552bd6110cf39db481bcf4f8f',
    }
    if target.exists() and target.read_bytes() != helper and hashlib.sha256(target.read_bytes()).hexdigest() not in previous_helper_shas:
        raise RuntimeError('Unreviewed reader detail helper')
    entries_name = 'src/apis/entries.js'
    entries_path = web / entries_name
    entries = entries_path.read_text()
    entry_before = 'export const getEntry = async (entryId) => apiClient.get(`/v1/entries/${entryId}`)'
    entry_after = 'export const getEntry = async (entryId, options = {}) => apiClient.get(`/v1/entries/${entryId}`, options)'
    if entries.count('export const getEntry =') != 1:
        raise RuntimeError('Unreviewed reader detail transport source')
    if entries.count(entry_after) == 1 and entry_before not in entries:
        pass
    elif entries.count(entry_before) == 1 and entry_after not in entries:
        entries = entries.replace(entry_before, entry_after, 1)
    else:
        raise RuntimeError('Unreviewed reader detail transport source')
    hook_import = 'import useReaderEntryDetail from "@/utils/reader-entry-detail"'
    legacy_hook_call = '  useReaderEntryDetail({ entryId, source, sourceId, activeContent, entryRequestIdRef, restoreEntryListFocus })'
    hook_call = legacy_hook_call.replace('restoreEntryListFocus })', 'restoreEntryListFocus, loadArticleDetail })')
    lazy_before = 'const ArticleDetail = lazy(() => import("@/components/Article/ArticleDetail"))'
    lazy_after = ('let articleDetailPromise\n'
                  'const loadArticleDetail = () => (articleDetailPromise ??= import("@/components/Article/ArticleDetail"))\n'
                  'const ArticleDetail = lazy(loadArticleDetail)')
    if lazy_after in text:
        if text.count(lazy_after) != 1 or text.count('const loadArticleDetail =') != 1 or text.count('const ArticleDetail = lazy(') != 1:
            raise RuntimeError('Unreviewed reader detail module loader')
    elif text.count(lazy_before) == 1 and 'loadArticleDetail' not in text:
        text = text.replace(lazy_before, lazy_after, 1)
    else:
        raise RuntimeError('Unreviewed reader detail module loader')
    if legacy_hook_call in text:
        if text.count(legacy_hook_call) != 1 or hook_call in text:
            raise RuntimeError('Unreviewed reader detail wiring')
        text = text.replace(legacy_hook_call, hook_call, 1)
    old_fetch = '  const fetchSingleEntry = useCallback(async (entryId) => {\n    const requestId = ++entryRequestIdRef.current\n    const isCurrentRequest = () => entryRequestIdRef.current === requestId\n    const numericEntryId = Number(entryId)\n    const existingEntry = contentState.get().entries.find((entry) => entry.id === numericEntryId)\n\n    if (existingEntry) {\n      setIsArticleLoading(false)\n      setActiveContent(existingEntry)\n      return\n    }\n\n    try {\n      setIsArticleLoading(true)\n      const entry = await getEntry(entryId)\n      if (isCurrentRequest()) {\n        setActiveContent(prepareEntry(entry))\n      }\n    } catch (error) {\n      if (isCurrentRequest()) {\n        console.error("Failed to fetch entry:", error)\n      }\n    } finally {\n      if (isCurrentRequest()) {\n        setIsArticleLoading(false)\n      }\n    }\n  }, [])\n'
    old_effect = '  useEffect(() => {\n    const currentActiveContent = contentState.get().activeContent\n\n    if (entryId) {\n      if (currentActiveContent?.id !== Number(entryId)) {\n        fetchSingleEntry(entryId)\n      }\n    } else {\n      entryRequestIdRef.current += 1\n      if (currentActiveContent) {\n        setActiveContent(null)\n        restoreEntryListFocus(currentActiveContent.id)\n      }\n      setIsArticleLoading(false)\n    }\n  }, [entryId, fetchSingleEntry, restoreEntryListFocus, source, sourceId])'
    reviewed_fetch = old_fetch.replace('if (existingEntry) {', 'if (existingEntry && !existingEntry.content_deferred) {')
    reviewed_effect = old_effect.replace('currentActiveContent?.id !== Number(entryId)', 'currentActiveContent?.id !== Number(entryId) || currentActiveContent?.content_deferred')
    if hook_call in text:
        if text.count(hook_call) != 1 or text.count(hook_import) != 1 or 'const fetchSingleEntry = useCallback(' in text:
            raise RuntimeError('Unreviewed reader detail wiring')
    else:
        variants = [(old_fetch, old_effect), (reviewed_fetch, reviewed_effect)]
        selected = next(((fetch, effect) for fetch, effect in variants if text.count(fetch) == 1 and text.count(effect) == 1), None)
        if selected is None or text.count('import { getEntry } from "@/apis"') != 1 or text.count('import prepareEntry from "@/utils/entry-presentation"') != 1:
            raise RuntimeError('Unreviewed reader detail loading source')
        fetch, effect = selected
        text = text.replace(fetch, '', 1).replace(effect, hook_call, 1)
        text = text.replace('import { getEntry } from "@/apis"', hook_import, 1)
        text = text.replace('import prepareEntry from "@/utils/entry-presentation"\n', '', 1)
    context_name = 'src/components/Content/ContentContext.jsx'
    context_path = web / context_name
    context = context_path.read_text()
    context_import = 'import { invalidateReaderEntryDetail } from "@/utils/reader-entry-detail"'
    close_before = '  const closeActiveContent = useCallback(() => {\n'
    close_after = close_before + '    invalidateReaderEntryDetail()\n'
    if context_import in context or close_after in context:
        if context.count(context_import) != 1 or context.count(close_after) != 1:
            raise RuntimeError('Unreviewed reader close intent wiring')
    else:
        if context.count(close_before) != 1:
            raise RuntimeError('Unreviewed reader close intent source')
        context = context_import + '\n' + context.replace(close_before, close_after, 1)
    backup(name)
    backup(context_name)
    backup(entries_name)
    path.write_text(text)
    context_path.write_text(context)
    entries_path.write_text(entries)
    target.write_bytes(helper)

install_reader_entry_detail(ROOT, WEB)
patch('src/utils/settings-schema.js','articleListLayout: enumSetting("column", ARTICLE_LIST_LAYOUTS)','articleListLayout: enumSetting("card", ARTICLE_LIST_LAYOUTS)')
patch('src/store/contentState.js','  articleListSnapshotRevision: 0,','  articleListSnapshotRevision: 0,\n  articleListOffset: 0,')
patch('src/hooks/useArticleList.js','  const preparedEntries = response.entries.map((entry) => prepareEntry(entry))','  contentState.setKey("articleListOffset", response.entries.length)\n  const preparedEntries = response.entries.map((entry) => prepareEntry(entry))')
if 'articleListAiRevision' not in (WEB/'src/hooks/useLoadMore.js').read_text():
    normalize('src/hooks/useLoadMore.js',[
     '      const isAiPagination = infoFrom === "all" && aiFilterEnabled()\n      if (isAiPagination) {\n        contentState.setKey("articleListOffset", contentState.get().articleListOffset + response.entries.length)\n      }\n      const progress = paginationProgressRef.current',
     '      const progress = paginationProgressRef.current',
    ],'      const isAiPagination = aiFilterEnabled()\n      if (isAiPagination) {\n        contentState.setKey("articleListOffset", contentState.get().articleListOffset + response.entries.length)\n      }\n      const progress = paginationProgressRef.current')
patch('src/hooks/useLoadMore.js','if (response.entries.length > 0 && !hasNewResponseEntries) {','if (!isAiPagination && response.entries.length > 0 && !hasNewResponseEntries) {')
def install_ai_pagination_revision(root, web):
    """Reject changed results with one automatic first-page refresh per view."""
    changes = {
        'src/store/contentState.js': [
            ('  articleListOffset: 0,', '''  articleListOffset: 0,
  articleListAiRevision: null,
  articleListAiRefreshes: 0,
  articleListAiRefreshRequired: false,'''),
            ('export const invalidateArticleList = () => {', '''export const invalidateArticleList = () => {
  contentState.setKey("articleListAiRefreshes", 0)
  contentState.setKey("articleListAiRefreshRequired", false)
  contentState.setKey("articleListAiRevision", null)
  if (aiFilterEnabled()) {
    contentState.setKey("isArticleListReady", false)
    incrementArticleListSnapshotRevision()
  }'''),
        ],
        'src/hooks/useArticleList.js': [
            ('  contentState.setKey("articleListOffset", response.entries.length)', '''  if (response.ai_revision !== undefined && (typeof response.ai_revision !== "string" || !/^[0-9a-f]{64}$/.test(response.ai_revision))) {
    throw new TypeError("Invalid AI result revision")
  }
  contentState.setKey("articleListAiRevision", response.ai_revision ?? null)
  contentState.setKey("articleListAiRefreshRequired", false)
  contentState.setKey("articleListOffset", response.entries.length)'''),
            ('    currentRequestKey.current = automaticRequestKey', '''    currentRequestKey.current = automaticRequestKey
    const recoveryViewKey = JSON.stringify([getDataSessionRevision(), createArticleListRequestKey({
      content: { ...contentSnapshot, articleListRevision: 0 },
      settings: settingsSnapshot, info: { from: source, id: sourceId },
    })])
    if (contentState.get().articleListAiRecoveryViewKey !== recoveryViewKey) {
      contentState.setKey("articleListAiRecoveryViewKey", recoveryViewKey)
      contentState.setKey("articleListAiRefreshes", 0)
    }
    contentState.setKey("articleListAiRefreshRequired", false)
    contentState.setKey("articleListAiRevision", null)'''),
            ('      const filterParams = content.filterString ? { search: content.filterString } : {}', '''      const filterParams = content.filterString ? { search: content.filterString } : {}
      if (aiFilterEnabled()) filterParams.ai_revision = "initial"'''),
        ],
        'src/hooks/useLoadMore.js': [
            ('      return { offset: contentState.get().articleListOffset, limit: AI_PAGE_SIZE }', '''      const content = contentState.get()
      return { offset: content.articleListOffset, limit: AI_PAGE_SIZE,
        ...(content.articleListAiRevision ? { ai_revision: content.articleListAiRevision } : {}) }'''),
            ('  const handleLoadMore = async (getEntries) => {', '''  const restartChangedList = () => {
    setLoadMoreError(false)
    contentState.setKey("articleListAiRefreshRequired", false)
    contentState.setKey("articleListAiRevision", null)
    contentState.setKey("isArticleListReady", false)
    contentState.setKey("articleListSnapshotRevision", (contentState.get().articleListSnapshotRevision ?? 0) + 1)
    contentState.setKey("articleListRevision", (contentState.get().articleListRevision ?? 0) + 1)
  }

  const handleLoadMore = async (getEntries) => {'''),
            ('    const requestKey = getCurrentArticleListRequestKey()\n    const requestSessionRevision', '''    if (aiFilterEnabled() && contentState.get().articleListAiRefreshRequired) {
      contentState.setKey("articleListAiRefreshes", 0)
      restartChangedList()
      return
    }

    const requestKey = getCurrentArticleListRequestKey()
    const requestSessionRevision'''),
            ('      const isAiPagination = aiFilterEnabled()\n      if (isAiPagination) {', '''      const isAiPagination = aiFilterEnabled()
      const content = contentState.get()
      if (isAiPagination && content.articleListAiRevision) {
        if (typeof response.ai_revision !== "string" || !/^[0-9a-f]{64}$/.test(response.ai_revision)) {
          throw new TypeError("Missing or invalid AI result revision")
        }
        if (response.ai_revision !== content.articleListAiRevision) {
          if ((content.articleListAiRefreshes ?? 0) >= 1) {
            contentState.setKey("articleListAiRefreshRequired", true)
            setLoadMoreError(true)
            Message.error("列表仍在变化，请点击重试刷新列表")
          } else {
            contentState.setKey("articleListAiRefreshes", 1)
            restartChangedList()
          }
          return
        }
      }
      if (isAiPagination) {'''),
        ],
    }
    updates = {}
    for name, replacements in changes.items():
        text = (web / name).read_text()
        for before, after in replacements:
            if name == 'src/hooks/useArticleList.js' and before == '    currentRequestKey.current = automaticRequestKey':
                legacy = '''    currentRequestKey.current = automaticRequestKey
    contentState.setKey("articleListAiRefreshes", 0)
    contentState.setKey("articleListAiRefreshRequired", false)
    contentState.setKey("articleListAiRevision", null)'''
                if after not in text:
                    if legacy in text:
                        before = legacy
                    elif 'articleListAiRefreshes' in text:
                        raise RuntimeError(f'Unreviewed AI pagination recovery source: {name}')
            if name == 'src/hooks/useLoadMore.js' and before == '  const handleLoadMore = async (getEntries) => {':
                # Reading-session applies later on a pristine install, but its
                # exact prefetch signature is already present on repeat/upgrade.
                prefetch = '  const handleLoadMore = async (getEntries, { prefetch = false } = {}) => {'
                signatures = [signature for signature in (before, prefetch) if signature in text]
                if len(signatures) != 1 or text.count(signatures[0]) != 1:
                    raise RuntimeError(f'Unreviewed AI pagination source: {name}')
                if signatures[0] == prefetch:
                    after = after.replace(before, prefetch, 1)
                    before = prefetch
            if after in text:
                continue
            if text.count(before) != 1:
                raise RuntimeError(f'Unreviewed AI pagination source: {name}')
            text = text.replace(before, after, 1)
        updates[name] = text
    for name, text in updates.items():
        original = root / 'runtime/reactflux-original' / name
        if not original.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(web / name, original)
        (web / name).write_text(text)

install_ai_pagination_revision(ROOT, WEB)
patch('src/components/Article/ArticleEntry.jsx','import useEntryActions from "@/hooks/useEntryActions"','import CardLanguage from "@/components/Ai/CardLanguage"\nimport useEntryActions from "@/hooks/useEntryActions"')
patch('src/components/Article/ArticleEntry.jsx','        <Presenter entry={entry} previewContent={previewContent} />','        <Presenter entry={{ ...entry, title: entry.card?.title || entry.title }} previewContent={entry.card?.summary || previewContent} />')
patch('src/components/Article/ArticleEntry.jsx','{ title: entry.title })\n    : entry.title','{ title: entry.card?.title || entry.title })\n    : (entry.card?.title || entry.title)')
shutil.copy2(ROOT/'patches/CardLanguage.jsx', WEB/'src/components/Ai/CardLanguage.jsx')

def install_bilingual_reading(root, web):
    """Install owned controls only; reviewed ArticleDetail wires the live view."""
    import hashlib
    previous = {
        'BilingualReading.jsx': {'a3c1da4485211039a62b2c8040337d180b1a8c8658aec709bc72aab8d3e84abf',
                                '24418fbeb90e4e37048db0c115384f0807ee06cb02b3dac377d6447a8d7762c3'},
        'BilingualReading.css': {'bd004e430ee619002a637548614010259c3ac661c194aac58a7dfd8062a7d666'},
    }
    planned = []
    for name, known in previous.items():
        data = (root / 'patches' / name).read_bytes()
        target = web / 'src/components/Ai' / name
        if target.exists():
            before = target.read_bytes()
            if before != data and hashlib.sha256(before).hexdigest() not in known:
                raise RuntimeError(f'Unreviewed bilingual reading helper: {name}')
        planned.append((target, data))
    for target, data in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)

install_bilingual_reading(ROOT, WEB)
diffs=[]
for original in BACK.rglob('*'):
 if original.is_file():
  relative=str(original.relative_to(BACK)); modified=WEB/relative
  diffs.extend(difflib.unified_diff(original.read_text().splitlines(True),modified.read_text().splitlines(True),fromfile='a/'+relative,tofile='b/'+relative))
(ROOT/'patches/reactflux.patch').write_text(''.join(diffs))
print('Pinned ReactFlux overlay applied; native reader retained; patch recorded.')
