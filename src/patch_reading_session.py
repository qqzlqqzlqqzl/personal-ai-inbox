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
for before,after in [
 ('const handleLoadMore = async (getEntries) => {', 'const handleLoadMore = async (getEntries, { prefetch = false } = {}) => {'),
 ('const updateEntries = (newEntries) => {', 'const updateEntries = (newEntries, prefetch = false) => {'),
 ('    markDuplicatesAsRead(duplicateEntries)', '    if (!prefetch) markDuplicatesAsRead(duplicateEntries)'),
 ('updateEntries(newEntries)', 'updateEntries(newEntries, prefetch)'),
]:
 if after in s:
  assert s.count(after)==1 and before not in s, 'ambiguous background prefetch hook'
 else:
  assert s.count(before)==1, 'background prefetch hook anchor missing'
  s=s.replace(before,after,1)
p.write_text(s)
