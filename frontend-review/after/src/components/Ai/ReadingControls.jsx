import {useEffect,useRef,useState} from 'react'
import {useStore} from '@nanostores/react'
import {articleDetailSettingsState,getDefaultSettings,updateSettings} from '@/store/settingsState'
import useScreenWidth from '@/hooks/useScreenWidth'
import './ReviewWorkflows.css'

export default function ReadingControls(){
 const settings=useStore(articleDetailSettingsState),[focus,setFocus]=useState(false),ref=useRef(null)
 const {isBelowMedium}=useScreenWidth()
 useEffect(()=>{const article=ref.current?.closest('.article-content');article?.classList.toggle('review-reading-focus',focus);return()=>article?.classList.remove('review-reading-focus')},[focus])
 return <div className="review-reading-bar" ref={ref}>
  <details className="review-reading-controls"><summary>阅读排版</summary><div className="review-reading-options">
   <label>字号 <output>{settings.fontSize.toFixed(2)} rem</output><input aria-label="正文字号" type="range" min="1" max="1.5" step="0.05" value={settings.fontSize} onChange={e=>updateSettings({fontSize:Number(e.target.value)})}/></label>
   <label>行距 <output>{(settings.articleLineHeight??1.8).toFixed(1)} 倍</output><input aria-label="正文行距" type="range" min="1.3" max="2.5" step="0.1" value={settings.articleLineHeight??1.8} onChange={e=>updateSettings({articleLineHeight:Number(e.target.value)})}/></label>
   <label>栏宽 <output>{settings.articleWidth} ch{isBelowMedium?' · 手机自动适配':''}</output><input aria-label="正文栏宽" disabled={isBelowMedium} type="range" min="50" max="100" step="5" value={settings.articleWidth} onChange={e=>updateSettings({articleWidth:Number(e.target.value)})}/></label>
   <button type="button" onClick={()=>{const defaults=getDefaultSettings();updateSettings({fontSize:defaults.fontSize,articleWidth:defaults.articleWidth,articleLineHeight:defaults.articleLineHeight??1.8})}}>恢复默认排版</button><small>排版保存在此浏览器，不改变收藏、已读或笔记。</small>
  </div></details>
  <button type="button" aria-pressed={focus} onClick={()=>setFocus(v=>!v)}>{focus?'退出专注正文':'专注正文'}</button>
  {focus&&<span role="status">已收起作者、日期等元信息，文章导航仍可使用。</span>}
 </div>
}
