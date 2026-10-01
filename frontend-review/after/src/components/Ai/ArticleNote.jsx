import { useEffect, useRef, useState } from "react"
import { useStore } from "@nanostores/react"
import apiClient from "@/apis/ofetch"
import { authState } from "@/store/authState"
import { dataState, getDataSessionRevision } from "@/store/dataState"
import { contentState, setActiveContent, setEntries } from "@/store/contentState"
import { draftKey,MAX_NOTE,readDraft,storeDraft,removeDraft } from "./note-drafts"
import "./AiNews.css"
import "./ReviewWorkflows.css"

const withNoteMeta=(entry,hasNote,updatedAt)=>entry?{...entry,ai:{...entry.ai,has_note:hasNote,note_updated_at:updatedAt||null}}:entry
const syncNoteMeta=(id,hasNote,at)=>{const state=contentState.get();if(state.activeContent?.id===id)setActiveContent(withNoteMeta(state.activeContent,hasNote,at));if(state.entries?.some(e=>e.id===id))setEntries(state.entries.map(e=>e.id===id?withNoteMeta(e,hasNote,at):e))}

function NoteEditor({entryId,storageKey,session}){
 const [note,setNote]=useState(''),[loaded,setLoaded]=useState(false),[loading,setLoading]=useState(true),[status,setStatus]=useState('正在加载…'),[recovery,setRecovery]=useState(null),[storageOK,setStorageOK]=useState(true),[failed,setFailed]=useState(false),[backup,setBackup]=useState('')
 const state=useRef({note:'',saved:'',loaded:false,pending:false,queued:false,recovering:false,needsConfirmation:false,alive:true,timer:null,controller:null})
 const textarea=useRef(null)
 let draftStorage=null;try{draftStorage=window.sessionStorage}catch{}
 const currentSession=()=>getDataSessionRevision()===session
 const cache=(value,base)=>{const ok=storeDraft(draftStorage,storageKey,value,base);if(state.current.alive)setStorageOK(ok);return ok}
 const flush=(keepalive=false,manual=false)=>{
  const s=state.current;if(!currentSession()||!s.loaded||s.recovering||(s.needsConfirmation&&!manual)||s.note===s.saved)return
  if(manual)s.needsConfirmation=false
  if(s.pending){s.queued=true;return}
  const value=s.note;s.pending=true;s.queued=false;if(s.alive){setFailed(false);setStatus('正在保存…')}
  apiClient.put(`/v1/ai/notes/${entryId}`,{note:value},{retry:0,keepalive,timeout:15000}).then(result=>{
   if(!currentSession())return
   s.saved=value;syncNoteMeta(entryId,!!value.trim(),result.updated_at)
   if(s.note===value){removeDraft(draftStorage,storageKey,value);if(s.alive)setStatus(value.trim()?'已保存':'已清空')}
   else{cache(s.note,value);s.queued=true;if(s.alive)setStatus('还有新修改，等待保存…')}
  }).catch(()=>{s.queued=false;if(s.alive&&currentSession()){setFailed(true);setStatus('保存未完成 · 草稿尚未同步到服务器')}}).finally(()=>{
   s.pending=false;if(s.queued&&currentSession())flush(keepalive)
  })
 }
 const load=()=>{
  const s=state.current;s.controller?.abort();const controller=new AbortController();s.controller=controller
  setLoading(true);setFailed(false);setStatus('正在加载…')
  apiClient.get(`/v1/ai/notes/${entryId}`,{retry:0,signal:controller.signal,timeout:15000}).then(result=>{
   if(!s.alive||!currentSession()||s.controller!==controller)return
   const value=typeof result.note==='string'?result.note.slice(0,MAX_NOTE):'',draft=readDraft(draftStorage,storageKey)
   s.saved=value;s.note=value;s.loaded=true;setLoaded(true);setNote(value);setBackup('')
   if(draft&&draft.note!==value){s.recovering=true;setRecovery(draft);setStatus('发现此标签页未同步草稿，请选择恢复或放弃')}
   else{s.recovering=false;setRecovery(null);removeDraft(draftStorage,storageKey);setStatus(value.trim()?'已保存':'自动保存')}
   syncNoteMeta(entryId,!!value.trim(),result.updated_at)
  }).catch(()=>{
   if(!s.alive||!currentSession()||controller.signal.aborted)return
   setStatus('笔记加载失败，可以重试；不会用空内容覆盖服务器。');setFailed(true)
   const draft=readDraft(draftStorage,storageKey);if(draft)setBackup(draft.note)
  }).finally(()=>{if(s.alive&&s.controller===controller)setLoading(false)})
 }
 useEffect(()=>{
  const s=state.current;s.alive=true;load()
  const pagehide=()=>{if(currentSession())flush(true)}
  const beforeunload=e=>{if(currentSession()&&s.loaded&&s.note!==s.saved){e.preventDefault();e.returnValue=''}}
  window.addEventListener('pagehide',pagehide);window.addEventListener('beforeunload',beforeunload)
  return ()=>{s.alive=false;clearTimeout(s.timer);s.controller?.abort();window.removeEventListener('pagehide',pagehide);window.removeEventListener('beforeunload',beforeunload);if(!currentSession())removeDraft(draftStorage,storageKey);else if(!s.recovering)flush(true)}
 },[])
 const change=value=>{const s=state.current;s.note=value;setNote(value);cache(value,s.saved);setStatus(s.needsConfirmation?'恢复的草稿尚未确认，请点击立即保存笔记':'等待自动保存…');clearTimeout(s.timer);s.timer=setTimeout(()=>flush(),700)}
 const saveNow=()=>{clearTimeout(state.current.timer);flush(false,true)}
 const restore=()=>{if(!recovery)return;const s=state.current;s.recovering=false;s.needsConfirmation=true;s.note=recovery.note;setNote(recovery.note);setRecovery(null);cache(s.note,s.saved);setStatus('已恢复本地草稿；请确认后保存到服务器。')}
 const discard=()=>{state.current.recovering=false;removeDraft(draftStorage,storageKey);setRecovery(null);setStatus('已放弃本地草稿，保留服务器笔记')}
 const copy=async()=>{const value=recovery?.note??(loaded?note:backup);try{await navigator.clipboard.writeText(value);setStatus('笔记已复制；复制不表示已同步')}catch{setBackup(value);setStatus('自动复制不可用，请在下方备份文本中全选复制。')}}
 return <section aria-label="我的笔记" className="article-note">
  <div className="article-note-head"><div><strong>📝 我的笔记</strong><span>仅自己可见 · 保存成功后跨设备同步</span></div><small aria-live="polite">{status}</small></div>
  {recovery&&<div className="review-note-recovery" role="status"><p>发现本标签页的未同步草稿。{recovery.base!==state.current.saved?'服务器内容已变化；请先核对，恢复不会立即覆盖服务器。':'恢复后可继续编辑。'}</p><button type="button" onClick={restore}>恢复本地草稿</button><button type="button" onClick={discard}>放弃草稿，保留服务器内容</button></div>}
  {!storageOK&&<p role="status">浏览器未允许草稿暂存；离开前请保存或复制备份。</p>}
  <textarea ref={textarea} aria-label="我的笔记" disabled={!loaded||!!recovery} maxLength={MAX_NOTE} placeholder="记下你的判断、可复用思路、和其他文章/项目的关联……" rows={5} value={note} onBlur={()=>{if(!recovery){clearTimeout(state.current.timer);flush()}}} onChange={e=>change(e.target.value)} onKeyDown={e=>{if(!e.isComposing&&e.keyCode!==229&&(e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='s'){e.preventDefault();saveNow()}}}/>
  <div className="review-note-actions">{!loaded&&<button disabled={loading} onClick={load}>重新加载笔记</button>}{loaded&&<button disabled={!!recovery} onClick={saveNow}>{failed?'重试保存':'立即保存笔记'}</button>}<button disabled={!loaded&&!backup} onClick={copy}>复制笔记备份</button></div>
  {backup&&<textarea aria-label="笔记备份文本" readOnly rows={4} value={backup} onFocus={e=>e.target.select()}/>}
  <div className="article-note-foot"><span>{note.length.toLocaleString()} / {MAX_NOTE.toLocaleString()}</span><span>本地草稿仅留在此标签页 · Ctrl/⌘ + S 保存</span></div>
 </section>
}

export default function ArticleNote({entry}){
 const auth=useStore(authState),data=useStore(dataState);const owner=data.currentUser?.id??auth.username
 if(!owner)return <section className="article-note" aria-label="我的笔记"><p>正在确认笔记账号…</p></section>
 const key=draftKey(auth.server,owner,entry.id),session=getDataSessionRevision()
 return <NoteEditor key={key+':'+session} entryId={entry.id} storageKey={key} session={session}/>
}
