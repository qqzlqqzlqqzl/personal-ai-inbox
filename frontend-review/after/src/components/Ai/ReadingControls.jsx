import {useLayoutEffect,useRef,useState} from 'react'
import {useStore} from '@nanostores/react'
import {articleDetailSettingsState,getDefaultSettings,updateSettings} from '@/store/settingsState'
import useScreenWidth from '@/hooks/useScreenWidth'
import './ReviewWorkflows.css'

// The native mobile node and desktop SimpleBar node come from the same ref.
// Keep the first visible body block at the same reading offset when chrome shrinks.
export function captureReadingAnchor(scroll, bar, article) {
 if (!scroll || !article) return null
 const body=article.querySelector('.article-body')
 if (!body || body.getAttribute('aria-busy')==='true') return null
 const top=scroll.getBoundingClientRect().top+(bar?.getBoundingClientRect().height??0)
 const bottom=scroll.getBoundingClientRect().bottom
 const element=[...body.querySelectorAll('h1,h2,h3,h4,p,pre,ul,ol,blockquote,figure,.article-source-footer')]
  .find(node=>{const rect=node.getBoundingClientRect();return rect.height>0&&rect.bottom>top&&rect.top<bottom})
 return {element:element??body,offset:element?element.getBoundingClientRect().top-top:0}
}

export function restoreReadingAnchor(scroll, anchor, bar) {
 if (!scroll?.isConnected || !anchor?.element?.isConnected || !scroll.contains(anchor.element)) return
 const delta=anchor.element.getBoundingClientRect().top-scroll.getBoundingClientRect().top-(bar?.getBoundingClientRect().height??0)-anchor.offset
 scroll.scrollTop=Math.max(0,Math.min(scroll.scrollHeight-scroll.clientHeight,scroll.scrollTop+delta))
}

export default function ReadingControls({scrollContainerRef,maxWidth}){
 const settings=useStore(articleDetailSettingsState),[focus,setFocus]=useState(false),ref=useRef(null)
 const {isBelowMedium}=useScreenWidth()
 const anchor=useRef(null),focusButton=useRef(null)
 useLayoutEffect(()=>{
  const article=ref.current?.closest('.article-content')
  article?.classList.toggle('review-reading-focus',focus)
  if(anchor.current){
   restoreReadingAnchor(scrollContainerRef?.current?.getScrollElement(),anchor.current,ref.current)
   anchor.current=null
   focusButton.current?.focus({preventScroll:true})
  }
  return()=>article?.classList.remove('review-reading-focus')
 },[focus,scrollContainerRef])
 const toggleFocus=()=>{
  anchor.current=captureReadingAnchor(scrollContainerRef?.current?.getScrollElement(),ref.current,ref.current?.closest('.article-content'))
  setFocus(value=>!value)
 }
 return <div className="review-reading-bar" ref={ref} style={{maxWidth}}>
  <details className="review-reading-controls"><summary>阅读排版</summary><div className="review-reading-options">
   <label>字号 <output>{settings.fontSize.toFixed(2)} rem</output><input aria-label="正文字号" type="range" min="1" max="1.5" step="0.05" value={settings.fontSize} onChange={e=>updateSettings({fontSize:Number(e.target.value)})}/></label>
   <label>行距 <output>{(settings.articleLineHeight??1.8).toFixed(1)} 倍</output><input aria-label="正文行距" type="range" min="1.3" max="2.5" step="0.1" value={settings.articleLineHeight??1.8} onChange={e=>updateSettings({articleLineHeight:Number(e.target.value)})}/></label>
   <label>栏宽 <output>{settings.articleWidth} ch{isBelowMedium?' · 手机自动适配':''}</output><input aria-label="正文栏宽" disabled={isBelowMedium} type="range" min="50" max="100" step="5" value={settings.articleWidth} onChange={e=>updateSettings({articleWidth:Number(e.target.value)})}/></label>
   <button type="button" onClick={()=>{const defaults=getDefaultSettings();updateSettings({fontSize:defaults.fontSize,articleWidth:defaults.articleWidth,articleLineHeight:defaults.articleLineHeight??1.8})}}>恢复默认排版</button><small>排版保存在此浏览器，不改变收藏、已读或笔记。</small>
  </div></details>
  <button ref={focusButton} type="button" aria-pressed={focus} onClick={toggleFocus}>{focus?'退出专注正文':'专注正文'}</button>
  {focus&&<span role="status">正文专注中：元信息与 AI 摘要已收起，标题、导航及原文链接仍保留。</span>}
 </div>
}
