"""Reviewed status overlay and bounded route loading; no publish operation."""
import hashlib
import os
import shutil
from pathlib import Path

PIN='534eeb97723ac11025de4ec1ac56335072e3be52'
ROUTES_BEFORE='90eb803fcf6feeaa32038dce6e8e8e6a14c09db6d37d51a930e3d348e39a2a34'
PANEL_BEFORE='06d5e523f593193302f73573f3ef37d068c73f93ec93e4e443084d91a64b9039'
TOOLBAR_BEFORE='f4f433120afe9fce94f1ca816b8bc49211b53cb4db1aca1aa32e20e6efe89e62'
LINK_IMPORT='import { Link } from "react-router";\n'
PANEL_ANCHOR='    <section className="review-resource-freshness" aria-label="各资源读取状态">'
LINK='    <p><Link to="/agent-status" style={{display:"inline-flex",alignItems:"center",minHeight:44,padding:"8px 12px"}} onClick={e => {if(dirty.current||busyRef.current){e.preventDefault();setMessage("请先保存或还原设置，再打开任务状态。")}else onClose()}}>任务状态</Link></p>\n'
ANCHOR='            ...routes,\n'
ADDITION=ANCHOR+'            { path: "agent-status", lazy: lazyRoute(() => import("./pages/AgentStatus")) },\n'

AUTHENTICATED_BEFORE='5a10a06fc73935f978c9b57e45a8a151251d4b617642e959d047b2426c4d583f'
CONTENT_PAGES_BEFORE='2e56638e1bf094ce43fdd36c0c620eb005b62afc1069f401c0e6b569a5db1ccf'
MAIN_BEFORE='e73ea0f9e51097e2b288dbcf5b8dc2368fb43cf92ec1d8aac9822ab1df1b13e3'
ROUTE_IMPORT='import { createBrowserRouter } from "react-router"\n'
PREVIOUS_STARTUP_ROUTE_IMPORT=ROUTE_IMPORT+'''import deferComponent from "./components/DeferredComponent"
import contentPageComponents from "./pages/ContentPages"

const HomeRedirectRoute = deferComponent(() => import("./components/HomeRedirect"))
const AgentStatusRoute = deferComponent(() => import("./pages/AgentStatus"))
'''
STARTUP_ROUTE_IMPORT=PREVIOUS_STARTUP_ROUTE_IMPORT.replace(
    'import contentPageComponents from "./pages/ContentPages"',
    'import contentPageComponents, { loadContentPages } from "./pages/ContentPages"')
CONTENT_LOADER='''const loadContentPage = (pageKey) => async () => {
  const { default: contentPageComponents } = await import("./pages/ContentPages")
  return { Component: contentPageComponents[pageKey] }
}

'''
STARTUP_CONTENT_LOADER='''const loadContentPage = (pageKey) => async () => {
  // Start only the matched content route while auth/bootstrap can proceed.
  // React.lazy consumes the same promise and reports failures to ErrorBoundary.
  void loadContentPages().catch(() => null)
  return { Component: contentPageComponents[pageKey] }
}

'''
STARTUP_ADDITION=ANCHOR+'            { path: "agent-status", Component: AgentStatusRoute },\n'
HOME_ROUTE='{ index: true, lazy: lazyRoute(() => import("./components/HomeRedirect")) }'
STARTUP_HOME_ROUTE='{ index: true, Component: HomeRedirectRoute }'
AUTH_APP_IMPORT='import App from "@/App"\n'
STARTUP_APP_IMPORT='import deferComponent from "@/components/DeferredComponent"\n'
APP_CONSTANT='const App = deferComponent(() => import("@/App"))\n\n'
SETTINGS_IMPORT='import SettingsModalContent from "@/components/Settings/SettingsModalContent"\n'
STARTUP_SETTINGS_IMPORT='import deferComponent from "@/components/DeferredComponent"\n'
SETTINGS_CONSTANT='''const SettingsModalContent = deferComponent(() => import("@/components/Settings/SettingsModalContent"))

'''
SETTINGS_CONTENT='''      <SettingsModalContent
        activeTab={settingsTabsActiveTab}
        onClose={handleClose}
        onTabChange={setSettingsTabsActiveTab}
      />'''
STARTUP_SETTINGS_CONTENT='''      {settingsModalVisible && (
        <SettingsModalContent
          activeTab={settingsTabsActiveTab}
          onClose={handleClose}
          onTabChange={setSettingsTabsActiveTab}
        />
      )}'''
DEFERRED_COMPONENT='''import { lazy, Suspense } from "react"

// The router can commit the authentication gate without resolving business
// modules. A boundary per surface keeps the mounted app shell during loading.
const LoadingSurface = () => (
  <div aria-busy="true" aria-live="polite" role="status" style={{ padding: 24 }}>
    正在加载…
  </div>
)

const deferComponent = (loadComponent) => {
  const Deferred = lazy(loadComponent)
  return function DeferredSurface(props) {
    return (
      <Suspense fallback={<LoadingSurface />}>
        <Deferred {...props} />
      </Suspense>
    )
  }
}

export default deferComponent
'''
LEGACY_CONTENT_PAGES='''import deferComponent from "@/components/DeferredComponent"

const contentPageComponents = {
  all: deferComponent(() => import("./All")),
  category: deferComponent(() => import("./Category")),
  feed: deferComponent(() => import("./Feed")),
  history: deferComponent(() => import("./History")),
  starred: deferComponent(() => import("./Starred")),
  today: deferComponent(() => import("./Today")),
}

export default contentPageComponents
'''
LOADED_CONTENT_PAGES='''import All from "./All"
import Category from "./Category"
import Feed from "./Feed"
import History from "./History"
import Starred from "./Starred"
import Today from "./Today"

const contentPageComponents = {
  all: All,
  category: Category,
  feed: Feed,
  history: History,
  starred: Starred,
  today: Today,
}

export default contentPageComponents
'''
PREVIOUS_CONTENT_PAGES='''import deferComponent from "@/components/DeferredComponent"

// This single lazy identity loads the original, bounded page map once. After
// any content page mounts, switching scope does not suspend on another module.
// Keep real page types/params: All -> Today changes Page, while a detail route
// within one page keeps Page's identity and the original route/context lifecycle.
const SharedContentPage = deferComponent(async () => {
  const { default: pages } = await import("./LoadedContentPages")
  const ContentPageSelection = ({ pageKey, ...props }) => {
    const Page = pages[pageKey]
    return <Page {...props} />
  }
  return { default: ContentPageSelection }
})

const contentRoute = (pageKey) => function ContentRoute(props) {
  return <SharedContentPage {...props} pageKey={pageKey} />
}

const contentPageComponents = {
  all: contentRoute("all"),
  category: contentRoute("category"),
  feed: contentRoute("feed"),
  history: contentRoute("history"),
  starred: contentRoute("starred"),
  today: contentRoute("today"),
}

export default contentPageComponents
'''
CONTENT_PAGES=PREVIOUS_CONTENT_PAGES.replace(
    '''const SharedContentPage = deferComponent(async () => {
  const { default: pages } = await import("./LoadedContentPages")
  const ContentPageSelection = ({ pageKey, ...props }) => {
    const Page = pages[pageKey]
    return <Page {...props} />
  }
  return { default: ContentPageSelection }
})''',
    '''let contentPagesPromise

export const loadContentPages = () => {
  if (!contentPagesPromise) {
    contentPagesPromise = import("./LoadedContentPages").then(({ default: pages }) => {
      const ContentPageSelection = ({ pageKey, ...props }) => {
        const Page = pages[pageKey]
        return <Page {...props} />
      }
      return { default: ContentPageSelection }
    })
  }
  return contentPagesPromise
}

const SharedContentPage = deferComponent(loadContentPages)''')


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _reviewed_base(text, expected_sha, apply, undo, label, legacy=()):
    """Accept the pinned source or an exact generated result; reject other drift."""
    if _sha(text)==expected_sha:
        return text
    base=undo(text)
    if _sha(base)==expected_sha and text in (apply(base), *(version(base) for version in legacy)):
        return base
    raise RuntimeError(f'Unreviewed {label}; refusing startup overlay')


def _routes_apply(base):
    return (base.replace(ROUTE_IMPORT,STARTUP_ROUTE_IMPORT,1)
            .replace(CONTENT_LOADER,STARTUP_CONTENT_LOADER,1)
            .replace(HOME_ROUTE,STARTUP_HOME_ROUTE,1)
            .replace(ANCHOR,STARTUP_ADDITION,1))


def _routes_apply_previous(base):
    return (base.replace(ROUTE_IMPORT,PREVIOUS_STARTUP_ROUTE_IMPORT,1)
            .replace(CONTENT_LOADER,'',1)
            .replace('lazy: loadContentPage(pageKey)','Component: contentPageComponents[pageKey]')
            .replace(HOME_ROUTE,STARTUP_HOME_ROUTE,1)
            .replace(ANCHOR,STARTUP_ADDITION,1))


def _routes_undo(text):
    text=text.replace(STARTUP_ADDITION,ANCHOR,1).replace(ADDITION,ANCHOR,1)
    if STARTUP_ROUTE_IMPORT in text:
        return (text.replace(STARTUP_ROUTE_IMPORT,ROUTE_IMPORT,1)
                .replace(STARTUP_CONTENT_LOADER,CONTENT_LOADER,1)
                .replace(STARTUP_HOME_ROUTE,HOME_ROUTE,1))
    if PREVIOUS_STARTUP_ROUTE_IMPORT not in text:
        return text
    text=(text.replace(PREVIOUS_STARTUP_ROUTE_IMPORT,ROUTE_IMPORT,1)
          .replace('Component: contentPageComponents[pageKey]','lazy: loadContentPage(pageKey)')
          .replace(STARTUP_HOME_ROUTE,HOME_ROUTE,1))
    return text.replace('const routes = Object.entries(pageRoutes)',CONTENT_LOADER+'const routes = Object.entries(pageRoutes)',1)


def _authenticated_apply(base):
    # App belongs to the authenticated route graph, not a second render-time hop.
    return base


def _authenticated_apply_previous(base):
    return (base.replace(AUTH_APP_IMPORT,STARTUP_APP_IMPORT,1)
            .replace('const AuthenticatedApp =',APP_CONSTANT+'const AuthenticatedApp =',1))


def _authenticated_undo(text):
    return text.replace(STARTUP_APP_IMPORT,AUTH_APP_IMPORT,1).replace(APP_CONSTANT,'',1)


def _main_apply(base):
    return (base.replace(SETTINGS_IMPORT,STARTUP_SETTINGS_IMPORT,1)
            .replace('const urlRule =',SETTINGS_CONSTANT+'const urlRule =',1)
            .replace(SETTINGS_CONTENT,STARTUP_SETTINGS_CONTENT,1))


def _main_undo(text):
    return (text.replace(STARTUP_SETTINGS_IMPORT,SETTINGS_IMPORT,1)
            .replace(SETTINGS_CONSTANT,'',1)
            .replace(STARTUP_SETTINGS_CONTENT,SETTINGS_CONTENT,1))

def install(root):
    root=Path(root).resolve();web=root/'upstream/reactflux';source=root/'patches/agent-status'
    if (web/'UPSTREAM_REVISION').read_text().strip()!=PIN:raise RuntimeError('Unexpected Reader revision')
    routes=web/'src/routes.jsx';before=routes.read_text()
    base=_reviewed_base(before,ROUTES_BEFORE,_routes_apply,_routes_undo,'authenticated routes',
                        legacy=(lambda text:text.replace(ANCHOR,ADDITION,1), _routes_apply_previous))
    if base.count(ANCHOR)!=1:raise RuntimeError('Unreviewed authenticated routes; refusing status overlay')
    toolbar=web/'src/components/Ai/AiToolbar.jsx';toolbar_base=toolbar.read_text()
    if hashlib.sha256(toolbar_base.encode()).hexdigest()!=TOOLBAR_BEFORE:raise RuntimeError('Unreviewed toolbar; refusing status entry')
    panel=web/'src/components/Ai/AiPanel.jsx';panel_base=panel.read_text().replace(LINK_IMPORT,'').replace(LINK,'')
    if hashlib.sha256(panel_base.encode()).hexdigest()!=PANEL_BEFORE or panel_base.count(PANEL_ANCHOR)!=1:raise RuntimeError('Unreviewed settings panel; refusing status entry')
    authenticated=web/'src/pages/AuthenticatedApp.jsx'
    auth_base=_reviewed_base(authenticated.read_text(),AUTHENTICATED_BEFORE,_authenticated_apply,
                             _authenticated_undo,'authenticated shell', legacy=(_authenticated_apply_previous,))
    content_pages=web/'src/pages/ContentPages.jsx';content_before=content_pages.read_text()
    if _sha(content_before)!=CONTENT_PAGES_BEFORE and content_before not in (CONTENT_PAGES,PREVIOUS_CONTENT_PAGES,LEGACY_CONTENT_PAGES):
        raise RuntimeError('Unreviewed content pages; refusing startup overlay')
    loaded_pages=web/'src/pages/LoadedContentPages.jsx'
    if _sha(LOADED_CONTENT_PAGES)!=CONTENT_PAGES_BEFORE:
        raise RuntimeError('Unreviewed loaded page authoring; refusing startup overlay')
    if loaded_pages.exists() and loaded_pages.read_text()!=LOADED_CONTENT_PAGES:
        raise RuntimeError('Unreviewed loaded content pages; refusing startup overlay')
    main=web/'src/components/Main/Main.jsx'
    main_base=_reviewed_base(main.read_text(),MAIN_BEFORE,_main_apply,_main_undo,'settings modal')
    helper=web/'src/components/DeferredComponent.jsx'
    if helper.exists() and helper.read_text()!=DEFERRED_COMPONENT:
        raise RuntimeError('Unreviewed deferred component; refusing startup overlay')
    # Validate all inputs before writing any file. Static test fixtures are excluded.
    names=['AgentStatus.jsx','status-controller.mjs','status-view.mjs','status-cache.mjs','status-contract.mjs','status-fingerprint.mjs','reader-status-client.mjs','status.css']
    for name in names:
        if not (source/name).is_file():raise RuntimeError('Missing status authoring file')
    target=web/'src/components/AgentStatus';target.mkdir(parents=True,exist_ok=True)
    for name in names:
        destination=web/'src/pages/AgentStatus.jsx' if name=='AgentStatus.jsx' else target/name
        shutil.copy2(source/name,destination)
    routes.write_text(_routes_apply(base))
    authenticated.write_text(_authenticated_apply(auth_base))
    content_pages.write_text(CONTENT_PAGES)
    loaded_pages.write_text(LOADED_CONTENT_PAGES)
    main.write_text(_main_apply(main_base))
    helper.write_text(DEFERRED_COMPONENT)
    panel.write_text(LINK_IMPORT+panel_base.replace(PANEL_ANCHOR,LINK+PANEL_ANCHOR,1))

if __name__=='__main__':install(Path(os.environ.get('AI_NEWS_ROOT',Path(__file__).resolve().parents[1])))
