import { Button, DatePicker, Input, Tooltip, Typography } from "@arco-design/web-react"
import {
  IconCalendar,
  IconQuestionCircle,
  IconSearch,
} from "@arco-design/web-react/icon"
import { useStore } from "@nanostores/react"
import { Fragment, memo, useMemo, useRef, useState } from "react"
import { useHotkeys } from "react-hotkeys-hook"
import { useParams } from "react-router"

import SidebarTrigger from "./SidebarTrigger.jsx"

import AccessibleModal from "@/components/ui/AccessibleModal"
import CustomTooltip from "@/components/ui/CustomTooltip"
import useContentContext from "@/hooks/useContentContext"
import { polyglotState } from "@/hooks/useLanguage"
import useScreenWidth from "@/hooks/useScreenWidth"
import {
  contentState,
  dynamicCountState,
  invalidateArticleList,
  setFilterDate,
  setFilterString,
} from "@/store/contentState"
import { catalogCategoriesState, catalogFeedsState } from "@/store/dataState"
import { duplicateHotkeysState, hotkeysState } from "@/store/hotkeysState"
import { settingsState, updateSettings } from "@/store/settingsState"
import { aiFilterEnabled, aiState } from "@/store/aiState"
import { getStartOfToday } from "@/utils/date"
import { readingCalendarState } from "@/store/readingCalendarState"
import "./SearchAndSortBar.css"

const SearchModal = memo(({ aiSearch, notesSearch, value, visible, onCancel, onConfirm, onChange, returnFocusRef }) => {
  const { polyglot } = useStore(polyglotState)
  const aiSearchLabel = notesSearch
    ? "搜索标题、AI分析和个人笔记"
    : "搜索标题、AI摘要、理由和标签"
  const tooltipLines = aiSearch ? [aiSearchLabel] : polyglot.t("search.article_tooltip").split("\n")
  const inputLabel = aiSearch ? aiSearchLabel : polyglot.t("search.article_input_label")
  const placeholder = aiSearch ? `${aiSearchLabel}...` : polyglot.t("search.article_placeholder")
  const searchInputRef = useRef(null)

  const handleAfterOpen = () => searchInputRef.current?.focus()

  const handleConfirm = () => onConfirm(value)

  const handleKeyDown = (event) => {
    if (event.isComposing || event.nativeEvent?.isComposing || event.keyCode === 229) {
      return
    }
    if (event.key === "Enter") {
      handleConfirm()
    }
  }

  const modalTitle = polyglot.t("search.search")

  return (
    <AccessibleModal
      afterOpen={handleAfterOpen}
      className="search-modal"
      closeLabel={polyglot.t("actions.close_dialog", { name: modalTitle })}
      fallbackFocusSelector=".reader-search-trigger"
      returnFocusRef={returnFocusRef}
      title={modalTitle}
      visible={visible}
      footer={
        <>
          <Button onClick={onCancel}>{polyglot.t("search.cancel")}</Button>
          <Button type="primary" onClick={handleConfirm}>
            {polyglot.t("search.confirm")}
          </Button>
        </>
      }
      onCancel={onCancel}
    >
      <div className="search-modal-content">
        <Input.Search
          ref={searchInputRef}
          allowClear
          aria-label={inputLabel}
          placeholder={placeholder}
          value={value}
          prefix={
            <Tooltip
              mini
              trigger={["hover", "click"]}
              content={
                <div>
                  {tooltipLines.map((line, index) => (
                    <Fragment key={`tooltip-line-${index}`}>
                      {line}
                      {index < tooltipLines.length - 1 && <br />}
                    </Fragment>
                  ))}
                </div>
              }
            >
              <Button
                aria-label={polyglot.t("search.syntax_help")}
                className="search-syntax-help"
                icon={<IconQuestionCircle aria-hidden="true" />}
                shape="circle"
                size="mini"
                type="text"
              />
            </Tooltip>
          }
          onChange={onChange}
          onKeyDown={handleKeyDown}
        />
      </div>
    </AccessibleModal>
  )
})
SearchModal.displayName = "SearchModal"

const ActiveButton = ({ active, expanded, icon, tooltip, onClick }) => (
  <>
    <Button
      aria-expanded={expanded}
      aria-haspopup="dialog"
      aria-label={tooltip}
      className="reader-search-trigger"
      icon={icon}
      shape="circle"
      size="small"
      title={tooltip}
      style={{
        backgroundColor: active ? "rgb(var(--primary-6))" : "inherit",
      }}
      onClick={onClick}
    />
  </>
)

const DateFilter = ({ filterDate, polyglot }) => {
  const [calendarVisible, setCalendarVisible] = useState(false)
  const selectDateLabel = polyglot.t("search.select_date")
  const calendar = useStore(readingCalendarState)

  const setDateAndClose = (date) => {
    setFilterDate(date)
    setCalendarVisible(false)
  }

  return (
    <DatePicker
      popupVisible={calendarVisible}
      position="bottom"
      showNowBtn={false}
      value={filterDate}
      extra={
        <div className="calendar-actions">
          <Button
            long
            size="mini"
            type="primary"
            disabled={!calendar.ready}
            onClick={() => setDateAndClose(getStartOfToday().format("YYYY-MM-DD"))}
          >
            {polyglot.t("search.today")}
          </Button>
          <Button long size="mini" onClick={() => setDateAndClose(null)}>
            {polyglot.t("search.clear_date")}
          </Button>
        </div>
      }
      triggerElement={
        <CustomTooltip mini content={selectDateLabel}>
          <Button
            aria-expanded={calendarVisible}
            aria-haspopup="dialog"
            aria-label={selectDateLabel}
            icon={<IconCalendar aria-hidden="true" />}
            shape="circle"
            size="small"
            style={{
              backgroundColor: filterDate ? "rgb(var(--primary-6))" : "inherit",
            }}
          />
        </CustomTooltip>
      }
      triggerProps={{
        boundaryDistance: { bottom: 8, left: 8, right: 8, top: 8 },
        className: "mobile-date-picker-popup",
      }}
      onChange={setFilterDate}
      onVisibleChange={setCalendarVisible}
    />
  )
}

const SearchAndSortBar = ({ fullWidth = false }) => {
  const calendar = useStore(readingCalendarState)
  const { filterDate, filterString, infoFrom, isArticleListReady } = useStore(contentState, {
    keys: ["filterDate", "filterString", "infoFrom", "isArticleListReady"],
  })
  const { orderBy, orderDirection } = useStore(settingsState, { keys: ["orderBy", "orderDirection"] })
  const ai = useStore(aiState)
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
      : (notesList && (!recommendedList || ai.sort === "note_updated"))
        ? "note_updated"
        : recommendedList && ai.sort !== "time" ? ai.sort : "published_at")
    : (activityList ? "changed_at" : orderBy)
  const sortDirection = aiList ? (ai.direction || "desc") : orderDirection
  const sortValue = `${sortField}_${sortDirection}`
  const { polyglot } = useStore(polyglotState)
  const feeds = useStore(catalogFeedsState)
  const categories = useStore(catalogCategoriesState)
  const dynamicCount = useStore(dynamicCountState)
  const duplicateHotkeys = useStore(duplicateHotkeysState)
  const hotkeys = useStore(hotkeysState)

  const { id } = useParams()
  const { closeActiveContent, entryListRef } = useContentContext()
  const { isBelowMedium } = useScreenWidth()

  const [searchModalVisible, setSearchModalVisible] = useState(false)
  const [modalInputValue, setModalInputValue] = useState("")
  const searchOpenerRef = useRef(null)

  const searchLabel = filterString
    ? polyglot.t("search.active_query", { query: filterString })
    : polyglot.t("search.search")
  const openSearchModalHotkeys = hotkeys.openSearchModal.filter(
    (key) => !duplicateHotkeys.includes(key),
  )

  const { title, count } = useMemo(() => {
    if (id) {
      if (infoFrom === "category") {
        const category = categories.find((c) => c.id === Number(id))
        return { title: category?.title, count: dynamicCount }
      }
      if (infoFrom === "feed") {
        const feed = feeds.find((f) => f.id === Number(id))
        return { title: feed?.title, count: dynamicCount }
      }
    }

    const infoMap = {
      all: { key: "sidebar.all", count: dynamicCount },
      today: { key: "sidebar.today", count: dynamicCount },
      starred: { key: "sidebar.starred", count: dynamicCount },
      history: { key: "sidebar.history", count: dynamicCount },
    }

    const info = infoMap[infoFrom] || { key: "", count: 0 }
    return { title: info.key ? polyglot.t(info.key) : "", count: info.count }
  }, [infoFrom, id, categories, feeds, dynamicCount, polyglot])
  const aiModeLabel = pendingList
    ? "待处理 / 异常"
    : notesList
      ? (recommendedList ? "AI精选 · 有笔记" : "有笔记")
      : recommendedList ? "AI精选" : ""
  const displayTitle = title && aiModeLabel ? `${title} · ${aiModeLabel}` : title

  const changeSort = (event) => {
    const value = event.target.value
    const split = value.lastIndexOf("_")
    const field = value.slice(0, split)
    const direction = value.slice(split + 1)
    if (aiList) {
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
    }
    closeActiveContent()
    entryListRef.current?.getScrollElement()?.scroll({ top: 0 })
    invalidateArticleList()
  }

  const openSearchModal = () => {
    searchOpenerRef.current = document.activeElement
    setModalInputValue(filterString)
    setSearchModalVisible(true)
  }

  useHotkeys(openSearchModalHotkeys, openSearchModal, {
    preventDefault: true,
    useKey: true,
  })

  const closeSearchModal = () => {
    setSearchModalVisible(false)
  }

  const handleConfirmSearch = (value) => {
    const normalizedQuery = value.trim()
    if (normalizedQuery !== filterString) {
      closeActiveContent()
      entryListRef.current?.getScrollElement()?.scroll({ top: 0 })
      setFilterString(normalizedQuery)
    }
    closeSearchModal()
  }

  return (
    <div
      className="search-and-sort-bar"
      style={{ width: isBelowMedium || fullWidth ? "100%" : 370 }}
    >
      <SidebarTrigger />
      <div className="page-info">
        <div className="title-container">
          {title ? (
            <Typography.Ellipsis
              expandable={false}
              showTooltip={!isBelowMedium}
              style={{ fontWeight: 500 }}
            >
              {displayTitle}
              {(infoFrom === "today" || filterDate) && <small className="reading-timezone" title="日期范围按账号阅读时区计算"> · {calendar.zone || "时区待确认"}</small>}
            </Typography.Ellipsis>
          ) : (
            <div className="placeholder-title"></div>
          )}
        </div>
        {isArticleListReady && count > 0 && (
          <Typography.Text className="count-label">({count})</Typography.Text>
        )}
      </div>
      <div className="button-group">
        <ActiveButton
          active={!!filterString}
          expanded={searchModalVisible}
          icon={<IconSearch aria-hidden="true" />}
          tooltip={searchLabel}
          onClick={openSearchModal}
        />
        {infoFrom !== "today" && <DateFilter filterDate={filterDate} polyglot={polyglot} />}
        <select className="ai-sort-select" aria-label="排序方式" value={sortValue} onChange={changeSort}>
          {sortOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>
      <SearchModal
        aiSearch={aiList}
        notesSearch={notesList}
        returnFocusRef={searchOpenerRef}
        value={modalInputValue}
        visible={searchModalVisible}
        onCancel={closeSearchModal}
        onChange={setModalInputValue}
        onConfirm={handleConfirmSearch}
      />
    </div>
  )
}

export default SearchAndSortBar
