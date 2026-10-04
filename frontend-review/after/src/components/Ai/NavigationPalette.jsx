import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '@nanostores/react'
import { useNavigate } from 'react-router'
import { visibleFeedsState, visibleCategoriesState } from '@/store/dataState'
import { updateSettings } from '@/store/settingsState'
import { navigationCommands, matchCommands } from './navigation-commands'
import { COMPACT_READER_QUERY } from './reader-media'
import './ReviewWorkflows.css'

const anotherDialogOpen = own => Array.from(document.querySelectorAll('dialog[open], [role="dialog"][aria-modal="true"]'))
  .some(node => node !== own && node.getClientRects().length > 0)

const canReturnFocus = node => {
  if (!node?.isConnected || typeof node.focus !== 'function' || node.disabled || node.tabIndex < 0 ||
      node.closest('[inert], [aria-hidden="true"]') || !node.getClientRects().length) return false
  const style = getComputedStyle(node)
  return style.visibility !== 'hidden' && style.visibility !== 'collapse' && style.display !== 'none' && style.opacity !== '0'
}

export default function NavigationPalette({ onConsole, launchRef, beforeLaunch, returnFocusRef, compact = false, triggerClassName = "" }) {
  const feeds = useStore(visibleFeedsState), categories = useStore(visibleCategoriesState)
  const navigate = useNavigate(), dialog = useRef(null), input = useRef(null), opener = useRef(null)
  const trigger = useRef(null), pendingFocus = useRef(null)
  const [open, setOpen] = useState(false), [query, setQuery] = useState(''), [index, setIndex] = useState(0)
  const all = useMemo(() => navigationCommands(feeds, categories), [feeds, categories])
  const matched = useMemo(() => matchCommands(all, query), [all, query]), shown = matched.slice(0, 50)
  const launch = () => {
    pendingFocus.current = null
    beforeLaunch?.()
    if (anotherDialogOpen(dialog.current)) return
    opener.current = document.activeElement
    setQuery(''); setIndex(0); setOpen(true)
  }
  const close = () => {
    setOpen(false); dialog.current?.close()
    const expectedCompact = window.matchMedia?.(COMPACT_READER_QUERY)?.matches ?? compact
    const ticket = { opener: opener.current, target: null, expectedCompact }
    pendingFocus.current = ticket
    returnFocus(ticket)
    // Only a media event awaiting its React commit can keep restoration rights.
    if (expectedCompact === compact) pendingFocus.current = null
  }
  const returnFocus = ticket => {
    if (anotherDialogOpen(dialog.current)) { pendingFocus.current = null; return }
    for (const target of [ticket.opener, returnFocusRef?.current, trigger.current]) {
      if (!canReturnFocus(target)) continue
      ticket.target = target
      target.focus({ preventScroll: true })
      if (document.activeElement === target) return
    }
  }
  useLayoutEffect(() => {
    if (open) { pendingFocus.current = null; return }
    const ticket = pendingFocus.current
    // The close commit can precede the held media callback. Do not consume it.
    if (!ticket || compact !== ticket.expectedCompact) return
    const active = document.activeElement
    if (!canReturnFocus(ticket.target) && (active === ticket.target || active === document.body)) returnFocus(ticket)
    pendingFocus.current = null
  }, [open, compact])
  useEffect(() => {
    const cancel = () => { pendingFocus.current = null }
    const focus = event => {
      const ticket = pendingFocus.current
      if (ticket && event.target !== ticket.target && event.target !== document.body) cancel()
    }
    document.addEventListener('pointerdown', cancel, true)
    document.addEventListener('keydown', cancel, true)
    document.addEventListener('focusin', focus, true)
    return () => {
      cancel()
      document.removeEventListener('pointerdown', cancel, true)
      document.removeEventListener('keydown', cancel, true)
      document.removeEventListener('focusin', focus, true)
    }
  }, [])
  if (launchRef) launchRef.current = launch
  useEffect(() => { if (open) { dialog.current?.showModal(); input.current?.focus() } }, [open])
  useEffect(() => {
    const key = e => {
      if (e.isComposing || e.keyCode === 229 || e.repeat || e.altKey || !(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== 'k') return
      beforeLaunch?.()
      if (anotherDialogOpen(dialog.current)) return
      e.preventDefault()
      if (dialog.current?.open) { input.current?.focus(); return }
      launch()
    }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [])
  useEffect(() => { setIndex(0) }, [query])
  useEffect(() => { setIndex(i => Math.min(i, Math.max(0, shown.length - 1))) }, [shown.length])
  useEffect(() => { dialog.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' }) }, [index, open])
  const select = command => {
    if (!command) return
    close()
    if (command.console) { onConsole(); return }
    if (command.unread) updateSettings({ showStatus: 'unread' })
    navigate(command.path)
  }
  return <>
    <button type="button" ref={trigger} className={"review-navigation-trigger " + triggerClassName} onClick={launch}>快速跳转 <kbd>Ctrl/⌘ K</kbd></button>
    {open && <dialog className="review-navigation-dialog" ref={dialog} aria-label="快速跳转" onCancel={e => { e.preventDefault(); close() }}>
      <header><h2>快速跳转</h2><button type="button" onClick={close} aria-label="关闭快速跳转">×</button></header>
      <input ref={input} role="combobox" aria-label="搜索视图、分类或订阅" aria-expanded="true" aria-controls="review-command-list" aria-activedescendant={shown[index] ? 'review-command-' + index : undefined} placeholder="输入订阅名称、分类或今天、收藏…" value={query} onChange={e => setQuery(e.target.value)} onKeyDown={e => {
        if (e.isComposing || e.nativeEvent?.isComposing || e.keyCode === 229) return
        if (e.key === 'ArrowDown') { e.preventDefault(); setIndex(i => Math.min(i + 1, Math.max(0, shown.length - 1))) }
        if (e.key === 'ArrowUp') { e.preventDefault(); setIndex(i => Math.max(0, i - 1)) }
        if (e.key === 'Enter') { e.preventDefault(); select(shown[index]) }
      }} />
      <p role="status">{matched.length ? `找到 ${matched.length} 项${matched.length > 50 ? '，显示前50项，请缩小关键词' : ''}` : '没有匹配结果，请尝试其他名称。'}</p>
      <ul id="review-command-list" role="listbox" aria-label="可跳转位置">{shown.map((command, i) => <li key={command.id} id={'review-command-' + i} role="option" aria-selected={index === i} onMouseEnter={() => setIndex(i)}><button type="button" tabIndex={-1} onClick={() => select(command)}><strong>{command.label}</strong><span>{command.detail}</span></button></li>)}</ul>
      <footer>↑ ↓ 选择 · Enter 打开 · Esc 关闭，仅切换阅读位置</footer>
    </dialog>}
  </>
}
