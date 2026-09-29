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
for name in ['AiBadge.jsx','AiToolbar.jsx','AiPanel.jsx','AiNews.css']:
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
patch('src/components/Content/Content.jsx','    if (existingEntry) {\n      setIsArticleLoading(false)','    if (existingEntry && !existingEntry.content_deferred) {\n      setIsArticleLoading(false)')
patch('src/components/Content/Content.jsx','      if (currentActiveContent?.id !== Number(entryId)) {','      if (currentActiveContent?.id !== Number(entryId) || currentActiveContent?.content_deferred) {')
patch('src/utils/settings-schema.js','articleListLayout: enumSetting("column", ARTICLE_LIST_LAYOUTS)','articleListLayout: enumSetting("card", ARTICLE_LIST_LAYOUTS)')
patch('src/store/contentState.js','  articleListSnapshotRevision: 0,','  articleListSnapshotRevision: 0,\n  articleListOffset: 0,')
patch('src/hooks/useArticleList.js','  const preparedEntries = response.entries.map((entry) => prepareEntry(entry))','  contentState.setKey("articleListOffset", response.entries.length)\n  const preparedEntries = response.entries.map((entry) => prepareEntry(entry))')
normalize('src/hooks/useLoadMore.js',[
 '      const isAiPagination = infoFrom === "all" && aiFilterEnabled()\n      if (isAiPagination) {\n        contentState.setKey("articleListOffset", contentState.get().articleListOffset + response.entries.length)\n      }\n      const progress = paginationProgressRef.current',
 '      const progress = paginationProgressRef.current',
],'      const isAiPagination = aiFilterEnabled()\n      if (isAiPagination) {\n        contentState.setKey("articleListOffset", contentState.get().articleListOffset + response.entries.length)\n      }\n      const progress = paginationProgressRef.current')
patch('src/hooks/useLoadMore.js','if (response.entries.length > 0 && !hasNewResponseEntries) {','if (!isAiPagination && response.entries.length > 0 && !hasNewResponseEntries) {')
patch('src/components/Article/ArticleEntry.jsx','import useEntryActions from "@/hooks/useEntryActions"','import CardLanguage from "@/components/Ai/CardLanguage"\nimport useEntryActions from "@/hooks/useEntryActions"')
patch('src/components/Article/ArticleEntry.jsx','        <Presenter entry={entry} previewContent={previewContent} />','        <Presenter entry={{ ...entry, title: entry.card?.title || entry.title }} previewContent={entry.card?.summary || previewContent} />')
patch('src/components/Article/ArticleEntry.jsx','{ title: entry.title })\n    : entry.title','{ title: entry.card?.title || entry.title })\n    : (entry.card?.title || entry.title)')
shutil.copy2(ROOT/'patches/CardLanguage.jsx', WEB/'src/components/Ai/CardLanguage.jsx')
diffs=[]
for original in BACK.rglob('*'):
 if original.is_file():
  relative=str(original.relative_to(BACK)); modified=WEB/relative
  diffs.extend(difflib.unified_diff(original.read_text().splitlines(True),modified.read_text().splitlines(True),fromfile='a/'+relative,tofile='b/'+relative))
(ROOT/'patches/reactflux.patch').write_text(''.join(diffs))
print('Pinned ReactFlux overlay applied; native reader retained; patch recorded.')
