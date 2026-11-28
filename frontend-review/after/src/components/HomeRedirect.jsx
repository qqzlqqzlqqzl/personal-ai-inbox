import { Button, Result, Spin } from "@arco-design/web-react"
import { useStore } from "@nanostores/react"
import { useEffect, useLayoutEffect, useState } from "react"
import { Navigate, useNavigate } from "react-router"

import useAppData from "@/hooks/useAppData"
import { polyglotState } from "@/hooks/useLanguage"
import { lastReading, restoreReading } from "@/store/readingLandingState"
import { contentState } from "@/store/contentState"
import { dataState, visibleCategoriesState, visibleFeedsState } from "@/store/dataState"
import { currentHomeTargetState, homeIdentityState } from "@/store/homePageState"
import { settingsState } from "@/store/settingsState"
import { articleSnapshots } from "@/utils/article-list-snapshot"
import { getArticleSnapshotContext, matchesArticleSnapshot } from "@/utils/article-list-snapshot-context"
import { getHomeTargetPath, isHomeTargetInCatalog } from "@/utils/home-page"
import { confirmDraftLogout } from "@/components/Ai/LogoutConfirm"

import "./HomeRedirect.css"

const LoadingState = ({ label }) => (
  <main aria-busy="true" className="home-route-state" role="status">
    <Spin aria-hidden="true" />
    <p>{label}</p>
  </main>
)

const ErrorState = ({ description, logoutLabel, retryLabel, title, onLogout, onRetry }) => (
  <main className="home-route-state" role="alert">
    <Result
      status="error"
      subTitle={description}
      title={title}
      extra={
        <div className="home-route-actions">
          <Button type="primary" onClick={onRetry}>
            {retryLabel}
          </Button>
          <Button onClick={onLogout}>{logoutLabel}</Button>
        </div>
      }
    />
  </main>
)

const HomeRedirect = () => {
  const identity = useStore(homeIdentityState)
  const homeTarget = useStore(currentHomeTargetState)
  const lastTarget = lastReading(identity)?.target
  const target = lastTarget || (homeTarget.type === "view" ? { type: "view", id: "all" } : homeTarget)
  const { loadState } = useStore(dataState, { keys: ["loadState"] })
  const categories = useStore(visibleCategoriesState)
  const feeds = useStore(visibleFeedsState)
  const { polyglot } = useStore(polyglotState)
  const { refreshFeedData, refreshIdentity } = useAppData()
  const navigate = useNavigate()
  const [cachedLanding, setCachedLanding] = useState(null)

  useLayoutEffect(() => { if (identity) restoreReading(identity) }, [identity])

  const cachedScope = lastTarget?.type
  const cachedId = lastTarget?.id
  const catalogReady = loadState.catalog.hasSnapshot
  useEffect(() => {
    if (!identity || catalogReady || !["feed", "category"].includes(cachedScope)) return
    const info = { from: cachedScope, id: cachedId }
    const context = getArticleSnapshotContext(contentState.get(), settingsState.get(), info)
    if (!context) return
    let active = true
    void articleSnapshots.read(context.owner, context.signature).then(snapshot => {
      if (active && snapshot) setCachedLanding(context)
    })
    return () => { active = false }
  }, [cachedId, cachedScope, catalogReady, identity, loadState.identity])

  const handleLogout = () => {
    confirmDraftLogout({
      title: polyglot.t("sidebar.logout_confirm"),
      description: polyglot.t("sidebar.logout_description"),
      onConfirm: () => navigate("/login", { replace: true, state: { from: "/" } }),
    })
  }

  if (!loadState.identity.hasSnapshot || !identity) {
    if (loadState.identity.error) {
      return (
        <ErrorState
          description={polyglot.t("home_page.identity_error_description")}
          logoutLabel={polyglot.t("sidebar.logout")}
          retryLabel={polyglot.t("actions.retry")}
          title={polyglot.t("home_page.identity_error_title")}
          onLogout={handleLogout}
          onRetry={() => refreshIdentity({ force: true }).catch(() => null)}
        />
      )
    }

    return <LoadingState label={polyglot.t("home_page.identity_loading")} />
  }

  if (target.type === "view") {
    return <Navigate replace to={getHomeTargetPath(target)} />
  }

  if (!loadState.catalog.hasSnapshot) {
    if (cachedLanding && matchesArticleSnapshot(cachedLanding, getArticleSnapshotContext(
      contentState.get(), settingsState.get(), { from: target.type, id: target.id },
    ))) {
      return <Navigate replace state={{ cachedReadingTarget: target }} to={getHomeTargetPath(target)} />
    }
    if (loadState.catalog.error) {
      return (
        <ErrorState
          description={polyglot.t("home_page.catalog_error_description")}
          logoutLabel={polyglot.t("sidebar.logout")}
          retryLabel={polyglot.t("actions.retry")}
          title={polyglot.t("home_page.catalog_error_title")}
          onLogout={handleLogout}
          onRetry={() => refreshFeedData({ force: true }).catch(() => null)}
        />
      )
    }

    return <LoadingState label={polyglot.t("home_page.catalog_loading")} />
  }

  const targetExists = isHomeTargetInCatalog(target, feeds, categories)
  return <Navigate replace to={targetExists ? getHomeTargetPath(target) : "/all"} />
}

export default HomeRedirect
