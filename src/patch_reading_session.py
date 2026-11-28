"""Pinned reader overlay: fix periodic reset; attach per-batch early prefetch."""
from pathlib import Path
import shutil
ROOT=Path('/home/ubuntu/ai-news')
WEB=ROOT/'upstream/reactflux/src'
shutil.copy2(ROOT/'patches/reading-session.js', WEB/'utils/reading-session.js')
shutil.copy2(ROOT/'patches/ProgressiveLoadMore.jsx', WEB/'components/Article/ProgressiveLoadMore.jsx')
p=WEB/'components/Article/ArticleList.jsx';s=p.read_text()
if 'import ProgressiveLoadMore' not in s:
 s='import ProgressiveLoadMore from "./ProgressiveLoadMore"\n'+s
start=s.find('const isElementVisibleInRoot =')
end=s.find('const ArticleList = forwardRef(',start)
if start>=0 and end>start: s=s[:start]+s[end:]
s=s.replace('<LoadMoreComponent getEntries={getEntries} scrollRootRef={cardsRef} />','<ProgressiveLoadMore getEntries={getEntries} scrollRootRef={cardsRef} />')
s=s.replace('forwardRef, useCallback, useEffect, useRef, useState','forwardRef')
s=s.replace('import { useInView } from "react-intersection-observer"\n','')
s=s.replace('import useLoadMore from "@/hooks/useLoadMore"\n','')
assert 'LoadMoreComponent' not in s
old_buffer='bufferSize={300}'
new_buffer='bufferSize={1000}'
if old_buffer in s:
 assert s.count(old_buffer)==1 and new_buffer not in s, 'unreviewed virtual buffer'
 s=s.replace(old_buffer,new_buffer,1)
else: assert s.count(new_buffer)==1, 'virtual buffer patch anchor missing'
p.write_text(s)

print('Reading snapshot, ten-page automatic prefetch and scroll overlay applied')

# AI offset totals and native cursor totals have different meanings.
p=WEB/'hooks/useLoadMore.js';s=p.read_text()
old="if (response.total <= loadedCount || response.entries.length < effectivePageSize) {"
new="if ((isAiPagination ? response.total <= loadedCount : response.total <= effectivePageSize) || response.entries.length < effectivePageSize) {"
if old in s: s=s.replace(old,new,1)
else: assert new in s, 'native pagination patch anchor missing'
p.write_text(s)

# Background lookahead must not perform the legacy duplicate-to-read mutation.
# Preserve all existing request owner/epoch and cursor checks in this hook.
p=WEB/'hooks/useLoadMore.js';s=p.read_text()
prefetch_signature = '  const handleLoadMore = async (getEntries, { prefetch = false } = {}) => {'
retry_signature = '  const handleLoadMore = async (getEntries, { prefetch = false, onError } = {}) => {'
for before,after in [
 ('const updateEntries = (newEntries) => {', 'const updateEntries = (newEntries, prefetch = false) => {'),
 ('    markDuplicatesAsRead(duplicateEntries)', '    if (!prefetch) markDuplicatesAsRead(duplicateEntries)'),
 ('updateEntries(newEntries)', 'updateEntries(newEntries, prefetch)'),
]:
 if after in s:
  assert s.count(after)==1 and before not in s, 'ambiguous background prefetch hook'
 else:
  assert s.count(before)==1, 'background prefetch hook anchor missing'
  s=s.replace(before,after,1)

for before,after in [
 ('        const filterParams = getFilterParams(contentState.get().entries)\n        if (filterString) {',
  '        const filterParams = getFilterParams(contentState.get().entries)\n        if (onError) filterParams.loadMoreRequest = true\n        if (filterString) {'),
 ('      Message.error(polyglot.t("article_list.load_more_error"))',
  '      if (!onError?.(error)) Message.error(polyglot.t("article_list.load_more_error"))'),
]:
 if after in s:
  assert s.count(after)==1 and before not in s, 'ambiguous load-more retry hook'
 else:
  assert s.count(before)==1, 'load-more retry hook anchor missing'
  s=s.replace(before,after,1)

# A retired view must not retain the current view's pagination slot. Keep the
# existing boolean guard intact for the calendar overlay applied later.
native_signature = '  const handleLoadMore = async (getEntries) => {'
if retry_signature not in s:
 assert s.count(native_signature) + s.count(prefetch_signature) == 1, 'load-more retry signature anchor missing'
 s=s.replace(native_signature,retry_signature,1).replace(prefetch_signature,retry_signature,1)
new_request_owner = retry_signature + '\n    const requestOwner = getLoadMoreOwner()'
# The signature replacement also upgrades an installation with the old owner guard.
for old_owner,new_owner in [
 ('const loadingMoreState = atom(false)', '''const getLoadMoreOwner = () => JSON.stringify([
  createArticleListRequestKey({ content: contentState.get(), settings: settingsState.get() }),
  getDataSessionRevision(), contentState.get().articleListSnapshotRevision,
])
let loadingMoreOwner = null
const loadingMoreState = atom(false)'''),
 (retry_signature, new_request_owner + '''
    if (loadingMoreOwner !== requestOwner) {
      loadingMoreOwner = requestOwner
      setLoadingMore(false)
    }'''),
 ('    } finally {\n      setLoadingMore(false)\n    }', '''    } finally {
      if (loadingMoreOwner === requestOwner) setLoadingMore(false)
    }'''),
 ('  return { handleLoadMore, loadMoreError, loadingMore }',
  '  return { handleLoadMore, loadMoreError, loadingMore: loadingMore && loadingMoreOwner === getLoadMoreOwner() }'),
]:
 if new_owner not in s:
  assert s.count(old_owner)==1, 'pagination owner hook anchor missing'
  s=s.replace(old_owner,new_owner,1)
p.write_text(s)

# Let the progressive list own its retry budget, including network failures
# that ofetch would otherwise retry internally. Keep the marker out of the URL.
p=WEB/'apis/entries.js';s=p.read_text()
helper = '''const getEntriesPage = (baseParams, { loadMoreRequest, ...extraParams }, applyDateFilter) =>
  apiClient.get(buildEntriesUrl(baseParams, extraParams, applyDateFilter), loadMoreRequest ? { retry: 0 } : undefined)

'''
for arguments, count in [('baseParams, extraParams', 4), ('baseParams, extraParams, applyDateFilter', 2)]:
 before = f'  return apiClient.get(buildEntriesUrl({arguments}))'
 after = f'  return getEntriesPage({arguments})'
 if after in s:
  assert s.count(after)==count and before not in s, 'unreviewed list transport for load-more retry'
 else:
  assert s.count(before)==count, 'list transport anchor missing'
  s=s.replace(before,after)
if helper not in s:
 assert s.count('export const getAllEntries =')==1 and 'const getEntriesPage =' not in s, 'list transport helper anchor missing'
 s=s.replace('export const getAllEntries =',helper+'export const getAllEntries =',1)
p.write_text(s)
