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
patch('src/store/contentState.js','import { computed, map } from "nanostores"','import { computed, map } from "nanostores"\nimport { aiState, aiFilterEnabled } from "./aiState"')
patch('src/store/contentState.js','[contentState, dataState, unreadTotalState, settingsState, feedsState, visibleFeedsState],','[contentState, dataState, unreadTotalState, settingsState, feedsState, visibleFeedsState, aiState],')
patch('src/store/contentState.js','    const { showStatus } = settings\n','    const { showStatus } = settings\n    if (infoFrom === "all" && aiFilterEnabled()) return total\n')
for name in ['ArticleGridCard','ArticleCard','ArticleDetail']:
 path='src/components/Article/'+name+'.jsx'; text=(WEB/path).read_text()
 if 'import AiBadge ' not in text:
  backup(path); (WEB/path).write_text('import AiBadge from "@/components/Ai/AiBadge"\n'+text)
patch('src/components/Article/ArticleGridCard.jsx','const cardSummary = getCardSummary(previewContent)','const cardSummary = getCardSummary(entry.ai?.state === "done" ? entry.ai.summary : previewContent)')
patch('src/components/Article/ArticleGridCard.jsx','        <div className="grid-card-footer">','        <AiBadge entry={entry} />\n        <div className="grid-card-footer">')
patch('src/components/Article/ArticleCard.jsx','              {previewContent}','              {entry.ai?.state === "done" ? entry.ai.summary : previewContent}')
patch('src/components/Article/ArticleCard.jsx','      </div>\n    </div>\n  )','      </div>\n      <AiBadge entry={entry} />\n    </div>\n  )')
patch('src/components/Article/ArticleDetail.jsx','            <Divider />\n          </div>','            <AiBadge entry={activeContent} detailed />\n            <Divider />\n          </div>')
patch('src/pages/Login.jsx','Object.fromEntries(searchParams).username ? "user" : "token"','"user"')
patch('src/pages/Login.jsx','    hideSpinner()\n  }, [])','    hideSpinner()\n    loginForm.setFieldsValue({ server: globalThis.location.origin + "/mf" })\n  }, [loginForm])')
patch('src/utils/settings-schema.js','articleListLayout: enumSetting("column", ARTICLE_LIST_LAYOUTS)','articleListLayout: enumSetting("card", ARTICLE_LIST_LAYOUTS)')
diffs=[]
for original in BACK.rglob('*'):
 if original.is_file():
  relative=str(original.relative_to(BACK)); modified=WEB/relative
  diffs.extend(difflib.unified_diff(original.read_text().splitlines(True),modified.read_text().splitlines(True),fromfile='a/'+relative,tofile='b/'+relative))
(ROOT/'patches/reactflux.patch').write_text(''.join(diffs))
print('Pinned ReactFlux overlay applied; native reader retained; patch recorded.')
