import { useEffect, useRef, useState } from "react"
import apiClient from "@/apis/ofetch"
import useAppData from "@/hooks/useAppData"
import SourceHistory from "./SourceHistory"
import { kaggleLaneStatus, kaggleQuotaText, kaggleStatus } from "./kaggle-status"
import {filterCatalog,normalizeDraft,settingsDelta,subscriptionQueue,uniqueSources,validateSettings} from "./review-utils"
import "./ReviewWorkflows.css"
import { aiState } from "@/store/aiState"
import { invalidateArticleList } from "@/store/contentState"

export default function AiPanel({ onClose, returnFocusRef }) {
  const dialog = useRef(null)
  const alive = useRef(true)
  const requests = useRef({})
  const baseline = useRef(null)
  const latestServer = useRef(null)
  const settingsSaving = useRef(false)
  const [savingSettings,setSavingSettings] = useState(false)
  const dirty = useRef(false)
  const busyRef = useRef(false)
  const stopBatch = useRef(false)
  const [loading,setLoading]=useState({})
  const [loadErrors,setLoadErrors]=useState({})
  const [sampled,setSampled]=useState({})
  const [fieldErrors,setFieldErrors]=useState({})
  const [closeRequested,setCloseRequested]=useState(false)
  const [sourceCategory,setSourceCategory]=useState("")
  const [sourceState,setSourceState]=useState("all")
  const [sourceLimit,setSourceLimit]=useState(24)
  const [pendingBatch,setPendingBatch]=useState(null)
  const [batchResults,setBatchResults]=useState([])
  const [batchProgress,setBatchProgress]=useState("")
  const [tab, setTab] = useState("settings")
  const [config, setConfig] = useState(null)
  const [status, setStatus] = useState(null)
  const [sources, setSources] = useState([])
  const [message, setMessage] = useState("")
  const [busy, setBusy] = useState(false)
  const [search, setSearch] = useState("")
  const [xHandle, setXHandle] = useState("@OpenAI")
  const [xProbe, setXProbe] = useState(null)
  const [xRoster, setXRoster] = useState(null)
  const { refreshFeedData } = useAppData()
  const load = async (names=["settings","status","catalog","roster"],{replace=false}={}) => {
    await Promise.allSettled(names.filter(name=>name!=='settings'||!settingsSaving.current).map(async name=>{
      // A synchronous request lock also covers two clicks before React renders.
      if(requests.current[name]&&!requests.current[name].signal.aborted&&!replace)return
      requests.current[name]?.abort()
      const controller=new AbortController();requests.current[name]=controller
      setLoading(prev=>({...prev,[name]:true}));setLoadErrors(prev=>({...prev,[name]:null}))
      try{
        const result=await apiClient.get(name==='roster'?"/v1/ai/x/roster":`/v1/ai/${name}`,{signal:controller.signal,retry:0,timeout:15000})
        if(!alive.current||requests.current[name]!==controller)return
        if(name==='settings'){latestServer.current=result;if(!dirty.current&&!settingsSaving.current){baseline.current=result;setConfig(result)}}
        if(name==='status')setStatus(result)
        if(name==='catalog')setSources(Array.isArray(result)?result:[])
        if(name==='roster')setXRoster(result)
        setSampled(prev=>({...prev,[name]:Date.now()}))
      }catch(e){if(alive.current&&requests.current[name]===controller&&!controller.signal.aborted)setLoadErrors(prev=>({...prev,[name]:([401,403].includes(e?.response?.status||e?.statusCode||e?.status)?'身份验证或访问权限未通过，当前服务器状态未确认。':'暂时无法读取，当前服务器状态未确认。')}))}
      finally{if(alive.current&&requests.current[name]===controller){delete requests.current[name];setLoading(prev=>({...prev,[name]:false}))}}
    }))
  }
  useEffect(()=>{
    alive.current=true;const opener=document.activeElement;dialog.current?.showModal();load()
    const leave=e=>{if(dirty.current){e.preventDefault();e.returnValue=''}}
    window.addEventListener('beforeunload',leave)
    return ()=>{
      alive.current=false;stopBatch.current=true;Object.values(requests.current).forEach(c=>c.abort());window.removeEventListener('beforeunload',leave)
      const visibleFocusable=node=>node?.isConnected&&node.getClientRects().length>0&&node.tabIndex>=0&&!node.matches(':disabled')&&!node.closest('[hidden],[inert]')&&getComputedStyle(node).visibility==='visible'
      const target=[opener,returnFocusRef?.current,document.querySelector(".ai-toolbar > .ai-settings-button")].find(visibleFocusable)
      target?.focus({preventScroll:true})
    }
  },[])
  useEffect(()=>setSourceLimit(24),[search,sourceCategory,sourceState])
  const savingCloseNotice=()=>{setCloseRequested(false);setMessage('正在保存，请等待结果；关闭不能撤销已发送的请求。')}
  const finishClose=()=>{if(settingsSaving.current){savingCloseNotice();return}stopBatch.current=true;dirty.current=false;onClose()}
  const requestClose=()=>{if(settingsSaving.current){savingCloseNotice();return}if(dirty.current||busyRef.current){setCloseRequested(true);return}finishClose()}
  const run = async action => {
    if(busyRef.current)return
    busyRef.current=true;setBusy(true);setMessage("")
    try{await action()}catch(e){if(alive.current)setMessage('操作未完成。草稿和已加载数据已保留，请重试；若持续失败请查看资源状态。')}
    finally{busyRef.current=false;if(alive.current)setBusy(false)}
  }
  const save = () => {
    if(busyRef.current)return
    const errors=validateSettings(config);setFieldErrors(errors)
    if(Object.keys(errors).length){setMessage('有未填写或超出范围的设置，请核对。');return}
    const normalized=normalizeDraft(config),changes=settingsDelta(baseline.current,normalized)
    if(!Object.keys(changes).length){dirty.current=false;baseline.current=latestServer.current||baseline.current;setConfig({...baseline.current});setMessage('没有需要保存的修改。');return}
    settingsSaving.current=true;setSavingSettings(true)
    requests.current.settings?.abort();delete requests.current.settings
    setLoading(previous=>({...previous,settings:false}))
    run(async()=>{try{
      const result=await apiClient.put('/v1/ai/settings',changes,{retry:0,timeout:15000});if(!alive.current)return
      const next={...(latestServer.current||baseline.current),...changes,...result};baseline.current=next;latestServer.current=next;dirty.current=false;setConfig(next);aiState.setKey('minimum',next.minimum_score);invalidateArticleList();setMessage('已保存到服务器')
    }finally{settingsSaving.current=false;if(alive.current)setSavingSettings(false)}})
  }
  const add = items => {
    if(busyRef.current)return
    const candidates=uniqueSources(items.filter(s=>!s.subscribed&&s.analysis_supported!==false))
    if(!candidates.length){setMessage('当前没有可添加的来源。');return}
    setPendingBatch(candidates);setBatchResults([]);setBatchProgress('尚未提交：请先确认来源清单。')
  }
  const startBatch = () => run(async()=>{
    if(!pendingBatch?.length)return
    const items=[...pendingBatch];stopBatch.current=false;setBatchProgress('正在添加；停止只影响尚未发送的项。')
    const results=await subscriptionQueue(items,{
      getCategories:()=>apiClient.get('/v1/categories',{retry:0,timeout:15000}),getFeeds:()=>apiClient.get('/v1/feeds',{retry:0,timeout:15000}),
      createCategory:title=>apiClient.post('/v1/categories',{title},{retry:0,timeout:15000}),
      subscribe:(source,category)=>apiClient.post('/v1/ai/subscribe',{url:source.url,category_id:category.id,crawler:false},{retry:0,timeout:15000}),
      stopped:()=>stopBatch.current||!alive.current,
      progress:rows=>{if(alive.current){setBatchResults(rows);setBatchProgress(`已处理 ${rows.length} / ${items.length}；已提交的操作不会撤回。`)}}
    })
    if(!alive.current)return
    setBatchResults(results);setPendingBatch(null);setBatchProgress(`本轮结束：成功 ${results.filter(r=>r.state==='success').length}，已存在 ${results.filter(r=>r.state==='existing').length}，失败 ${results.filter(r=>r.state==='failed').length}，未发送 ${items.length-results.length}。`)
    await refreshFeedData();invalidateArticleList();await load(['catalog'],{replace:true})
  })
  const change=(key,value)=>{if(settingsSaving.current)return;setConfig(previous=>{const next={...previous,[key]:value};dirty.current=Object.keys(validateSettings(next)).length>0||Object.keys(settingsDelta(baseline.current,normalizeDraft(next))).length>0;return next})}
  const filtered=filterCatalog(sources,search,sourceCategory,sourceState)
  const categories=[...new Set(sources.map(s=>s.category).filter(Boolean))].sort()
  const resetSourceFilters=()=>{setSearch('');setSourceCategory('');setSourceState('all')}
  const analysisCounts = status?.counts || {}
  const coverage = status?.coverage || {}
  const translationCounts = status?.translations?.counts || {}
  const articleTotal = coverage.reader_total || coverage.total_articles || Object.values(analysisCounts).reduce((a,b)=>a+b,0)
  const translated = (translationCounts.done || 0) + (translationCounts.native || 0)
  const translationTotal = Object.values(translationCounts).reduce((a,b)=>a+b,0)
  const queued = (analysisCounts.pending || 0) + (analysisCounts.waiting_model || 0) + (analysisCounts.budget_paused || 0)
  const attention = coverage.needs_attention || 0
  const laneEntries = Object.entries(status?.kaggle?.lanes || {})
  const kaggle = kaggleStatus(status?.kaggle)
  const laneNumbers = { primary: 1, secondary: 2, third: 3, fourth: 4, fifth: 5 }
  const hours = (value) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)}h` : "—"
  const refreshDate = (value) => value ? String(value).slice(5,10).replace("-","/") : "—"
  const resources = status?.resources || {}
  const percentage = (value,total) => total ? Math.round((value || 0) * 100 / total) : 0
  const formatBytes = (value) => {
    const n = Number(value || 0)
    if (!n) return "—"
    if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(1)} GB`
    if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)} MB`
    if (n >= 1024) return `${Math.round(n / 1024)} KB`
    return `${n} B`
  }
  const diskPct = percentage(resources.disk_used_bytes, resources.disk_total_bytes)
  const memoryPct = percentage(resources.memory_used_bytes, resources.memory_total_bytes)
  return <dialog className="ai-dialog" ref={dialog} aria-label="个人 AI 资讯控制台" onCancel={e=>{e.preventDefault();requestClose()}} onClose={onClose}>
    <header><h2>个人 AI 资讯控制台</h2><button aria-label="关闭" onClick={requestClose}>×</button></header>
    <nav>{[["settings","模型与偏好"],["status","资源看板"],["sources","来源目录"]].map(([id,label]) => <button key={id} aria-pressed={tab===id} onClick={() => setTab(id)}>{label}</button>)}</nav>
    {closeRequested && <section className="review-confirm" role="alert" aria-label="确认关闭控制台"><p>{dirty.current?'有尚未保存的设置。关闭将丢弃此处修改。 ':''}{busyRef.current?'有操作尚未结束。关闭不能撤销已发送的请求，尚未提交的批量订阅会停止。':''}</p><button onClick={()=>setCloseRequested(false)}>继续编辑</button><button onClick={finishClose}>放弃修改并关闭</button></section>}
    <section className="review-resource-freshness" aria-label="各资源读取状态">
      {[["settings","设置","设置"],["status","资源看板","看板"],["catalog","来源目录","目录"],["roster","X 名单","名单"]].map(([name,label,action])=><div key={name} data-resource={name} data-state={loading[name]?'loading':loadErrors[name]?'error':sampled[name]?'success':'empty'} data-sampled-at={sampled[name]||''}>
        <p role="status" className={loadErrors[name]?"review-error":"review-sampled"}><strong>{label}：</strong>{loading[name]?'正在读取；本次结果尚未确认。':loadErrors[name]||(!sampled[name]?'尚未取得成功数据。':'读取成功。')}{sampled[name]?<> 上次成功读取：<time dateTime={new Date(sampled[name]).toISOString()}>{new Date(sampled[name]).toLocaleString()}</time>{loading[name]||loadErrors[name]?' · 正在显示旧快照':''}</>:' 尚无可显示的成功快照。'}</p>
        <button disabled={loading[name]||(name==='settings'&&savingSettings)} onClick={()=>load([name])}>{loadErrors[name]?'重试':'重新读取'}{action}</button>
      </div>)}
    </section>
    {message && <p className="ai-message" role="status">{message}</p>}
    {tab==="settings"&&!config&&loading.settings&&<p role="status">正在读取服务器配置……</p>}
    {tab === "settings" && config && <section className="ai-form"><p role="status" className="review-draft-state">{dirty.current?"有未保存的修改":loadErrors.settings?"当前服务器设置未确认；保留上次成功读取的设置":loading.settings?"正在核对服务器设置":"当前没有未保存的修改"}</p>
      <p className="ai-notice">当前主链路为 Kaggle；下方 API 设置仅用于停用中的备用服务，不代表 Kaggle 的 Token 预算。</p>
      <p className="ai-notice">模型密钥{config.api_key_configured ? "已配置，调用结果以处理状态为准" : "尚未配置"}。密钥仅从服务器环境读取，网页不接收或回显密钥。修改接口会改变原文与模型认证的发送目标，只填写可信服务。</p>
      <label><input type="checkbox" disabled={savingSettings||status?.kaggle?.enabled} checked={config.enabled} onChange={e=>change("enabled",e.target.checked)} /> 开启后台分析</label>
      <label><input type="checkbox" disabled={savingSettings||status?.kaggle?.enabled} checked={config.translation_enabled !== false} onChange={e=>change("translation_enabled",e.target.checked)} /> 卡片标题与简介中文化（DeepSeek）</label>
      <p className="ai-notice">中文卡片与价值评分独立，结果缓存在服务器；翻译与评分共用下面的每日请求与 Token 总上限。正文保留原文。</p>
      <label>接口地址<input disabled={savingSettings} aria-label="接口地址" aria-invalid={!!fieldErrors.base_url} value={config.base_url} onChange={e=>change("base_url",e.target.value)} /></label>
      <label>模型 ID<input disabled={savingSettings} aria-label="模型 ID" aria-invalid={!!fieldErrors.model} value={config.model} onChange={e=>change("model",e.target.value)} /></label>
      <label>个人筛选提示词<textarea disabled={savingSettings} aria-label="个人筛选提示词" aria-invalid={!!fieldErrors.prompt} rows={9} value={config.prompt} onChange={e=>change("prompt",e.target.value)} /></label>
      <div className="ai-field-grid">{[["daily_articles","每日最多模型请求"],["daily_tokens","每日 Token 预算"],["max_chars","单篇输入字符上限"],["minimum_score","默认最低推荐分"]].map(([key,label])=><label key={key}>{label}<input disabled={savingSettings} aria-label={label} aria-invalid={!!fieldErrors[key]} type="number" value={config[key]} onChange={e=>change(key,e.target.value)}/></label>)}</div>
      <label><input type="checkbox" disabled={savingSettings} checked={config.json_mode} onChange={e=>change("json_mode",e.target.checked)} /> 使用 JSON 响应模式（需模型支持）</label>
      {Object.entries(fieldErrors).map(([key,error])=><p key={key} role="alert">{({base_url:'接口地址',model:'模型 ID',prompt:'提示词',daily_articles:'请求上限',daily_tokens:'Token预算',max_chars:'输入字符上限',minimum_score:'最低分'})[key]}：{error}</p>)}<div className="review-setting-actions"><button disabled={busy||!dirty.current} onClick={save}>保存到服务器</button><button disabled={busy||!dirty.current} onClick={()=>{baseline.current={...(latestServer.current||baseline.current)};setConfig({...baseline.current});dirty.current=false;setFieldErrors({});setMessage('已丢弃草稿，恢复为最近读取的服务器设置。')}}>还原服务器设置</button><button disabled={loading.settings||savingSettings} onClick={()=>load(["settings"])}>刷新设置</button></div>
    </section>}
    {tab==="status"&&!status&&loading.status&&<p role="status">正在读取资源看板…</p>}
    {tab === "status" && status && <section className="ai-dashboard">
      <p className="ai-dashboard-intro">先看阅读结果和覆盖率；底层队列、日志和用量放在“详细诊断”里，需要排障时再展开。</p>
      <div className="ai-dashboard-grid">
        <div><strong>{articleTotal}</strong><span>已收录文章</span><small>当前信息箱规模</small></div>
        <div><strong>{coverage.source_count || 0}</strong><span>订阅来源</span><small>当前启用目录</small></div>
        <div><strong>{coverage.ai_done || analysisCounts.done || 0}</strong><span>AI 已完成</span><small>{percentage(coverage.ai_done || analysisCounts.done, articleTotal)}% 覆盖</small></div>
        <div><strong>{coverage.substantial_source_text || 0}</strong><span>长正文已抓取</span><small>{percentage(coverage.substantial_source_text, articleTotal)}% 覆盖</small></div>
        <div><strong>{translated}</strong><span>中文卡片可用</span><small>{percentage(translated, translationTotal || articleTotal)}% 覆盖</small></div>
        <div><strong>{queued}</strong><span>队列中</span><small>等待抓取 / 模型</small></div>
        <div className={attention ? "needs-attention" : ""}><strong>{attention}</strong><span>需要处理</span><small>抓取或审核异常</small></div>
        <div><strong>{coverage.notes || 0}</strong><span>有笔记文章</span><small>个人沉淀</small></div>
        <div className={diskPct >= 85 ? "needs-attention" : ""}><strong>{diskPct}%</strong><span>磁盘已用</span><small>剩余 {formatBytes(resources.disk_free_bytes)}</small></div>
        <div className={memoryPct >= 85 ? "needs-attention" : ""}><strong>{memoryPct}%</strong><span>内存已用</span><small>可用 {formatBytes(resources.memory_available_bytes)}</small></div>
        <div><strong>{formatBytes(resources.analysis_db_bytes)}</strong><span>分析数据库</span><small>SQLite 文件大小</small></div>
      </div>
      <div className="ai-health-row">
        {[["网页网关",status.services?.gateway],["阅读器",status.services?.reader],["RSSHub",status.services?.rsshub]].map(([label,ok])=>
          <span className={ok ? "ok" : "bad"} key={label}>{label} · {ok ? "正常" : "异常"}</span>)}
        <span className={loadErrors.status ? "warn" : kaggle.tone}>{loadErrors.status ? "Kaggle 状态暂不可读 · 显示上次快照" : kaggle.text}</span>
      </div>
      <h3 className="ai-dashboard-section-title">Kaggle 计算资源</h3>
      <p className="ai-dashboard-caption">5 个独立 lane · GPU 周额度每 5 分钟缓存刷新{status.kaggle?.quota_checked_at ? ` · 最近查询 ${new Date(status.kaggle.quota_checked_at*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})}` : ""}</p>
      <div className="ai-kaggle-grid">
        {laneEntries.map(([key,lane]) => {
          const current=kaggleLaneStatus(lane, Date.now()/1000, status.kaggle?.scheduler?.lanes?.[key])
          const gpu=lane?.quota?.gpu
          const tpu=lane?.quota?.tpu
          const remaining=Number(gpu?.remaining_hours)
          const total=Number(gpu?.total_hours)
          const used=Number(gpu?.used_hours)
          const low=gpu?.remaining_hours != null && Number.isFinite(remaining) && remaining <= 1
          const quotaUnavailable=!gpu || gpu.remaining_hours == null || !Number.isFinite(remaining)
          return <div className={`ai-kaggle-card ${low ? "needs-attention" : ""}`} key={key} data-kaggle-state={current.kind}>
            <div className="ai-kaggle-head"><strong>Kaggle {laneNumbers[key] || key}</strong><span className={current.tone}>{current.text}</span></div>
            <small>{current.detail}</small>
            <div className="ai-kaggle-quota"><b>{quotaUnavailable ? "额度未知" : `${hours(remaining)} 剩余`}</b><span>{quotaUnavailable ? (lane?.quota?.state === "stale" ? "额度缓存过期" : "Kaggle quota 暂不可读") : `GPU / ${hours(total)}`}</span></div>
            <small data-quota-state={lane?.quota_gate?.state || "quota_unknown"}>{kaggleQuotaText(lane, status.kaggle?.enabled, current)}</small>
            {!quotaUnavailable && <progress max={total || 30} value={Number.isFinite(used) ? used : 0} />}
            {!quotaUnavailable && <small>已用 {hours(used)} · {refreshDate(gpu.refresh_at)} 刷新</small>}
            {tpu && <small>TPU 剩余 {hours(tpu.remaining_hours)}</small>}
            <small>本轮完成 {lane?.completed_batches || 0} 批{lane?.outstanding?.state ? ` · batch ${lane.outstanding.state}` : ""}</small>
          </div>
        })}
      </div>
      <div className="ai-dashboard-actions">
        <button disabled={loading.status} onClick={()=>load(["status"])}>{loading.status?"正在刷新…":"刷新看板"}</button>
        <button disabled={busy || !attention} onClick={()=>run(async()=>{const r=await apiClient.post("/v1/ai/retry",{});setMessage("已重排 " + r.queued + " 个失败任务");await load(["status"],{replace:true})})}>重试可重试任务</button>
      </div>
      <details className="ai-diagnostics">
        <summary>详细诊断</summary>
        <h3>分析状态</h3>
        <div className="ai-state-grid">{Object.entries(status.counts||{}).map(([key,value])=><div key={key}><strong>{value}</strong><span>{key}</span></div>)}</div>
        <h3>中文卡片状态</h3><p>{Object.entries(translationCounts).map(([k,v])=>k + ": " + v).join(" · ")}</p>
        {status.reading && <><h3>阅读行为</h3><p>打开 {status.reading.sessions} 次 · {status.reading.entries} 篇 · 有效前台阅读 {Math.round(status.reading.active_ms/1000)} 秒 · 深度阅读 {status.reading.deep_reads || 0} 次 · 平均滚动 {Math.round(status.reading.avg_scroll_pct || 0)}%</p>{status.reading.recent?.slice(0,6).map((r,i)=><p className="ai-event" key={[r.entry_id,r.opened_at,i].join("-")}>{new Date(r.opened_at*1000).toLocaleString()} · {Math.round((r.active_ms || 0)/1000)} 秒 · 滚动 {Math.round(r.max_scroll_pct || 0)}% · {r.starred ? "已收藏" : "未收藏"} · {r.title || ("#" + r.entry_id)}</p>)}</>}
        <h3>用量记录（UTC）</h3>{(status.usage||[]).map(u=><p key={u.day}>{u.day} · {u.calls} 次请求 · {u.tokens} Token</p>)}
        <h3>近期处理日志</h3>{(status.events||[]).map((e,i)=><p className="ai-event" key={i}>{new Date(e.at*1000).toLocaleString()} · {e.kind} · {e.detail}</p>)}
      </details>
    </section>}
    {tab === "sources" && <section>
      <p>先广泛收录，再按实际阅读价值裁剪。以下“可用”只表示本次成功解析订阅 XML，不代表每篇原文都能抓到。</p>
      <p className="ai-notice">初次订阅只导入 RSS/Atom 当时暴露的条目，此后轮询逐步累积，没有统一历史截止日。展开每个已订阅来源的“历史范围”可查询存储范围与当前 feed 窗口；日期统一以 UTC 显示，feed 快照最多缓存 5 分钟。Feed 范围不代表站点全部历史，未执行 archive/API/sitemap 回补。</p>
      {xRoster && <details><summary>X 核心名单：{xRoster.counts.total} 个 · timeline 有内容 {xRoster.counts.timeline_nonempty} · 空 {xRoster.counts.timeline_empty}（其中 {xRoster.counts.empty_with_fallback} 个已有稳定替代源）</summary><div className="ai-source-list">{xRoster.sources.map(s=><div key={s.handle}><div><strong>@{s.handle}</strong><small>{s.category} · {s.timeline_status === "nonempty" ? `X 已抓到 ${s.timeline_entries || 0} 条` : "X timeline 暂空"}{s.status === "fallback_active" ? " · 稳定替代源已启用" : ""}</small></div></div>)}</div></details>}
      <div className="review-source-filters"><input aria-label="搜索来源" placeholder="搜索名称或分类" value={search} onChange={e=>setSearch(e.target.value)} /><label>分类<select aria-label="目录分类" value={sourceCategory} onChange={e=>setSourceCategory(e.target.value)}><option value="">全部分类</option>{categories.map(c=><option key={c}>{c}</option>)}</select></label><label>状态<select aria-label="目录订阅状态" value={sourceState} onChange={e=>setSourceState(e.target.value)}><option value="all">全部</option><option value="subscribed">已订阅</option><option value="addable">尚未订阅且可添加</option><option value="attention">需要关注</option></select></label><button onClick={resetSourceFilters}>重置目录筛选</button></div>
      <p role="status">匹配 {filtered.length} / {sources.length} 个来源 · 本次显示 {Math.min(sourceLimit,filtered.length)} 个</p>
      <button disabled={busy||!filtered.some(s=>s.status==="ok"&&!s.subscribed&&s.analysis_supported!==false)} onClick={()=>add(filtered.filter(s=>s.status==="ok" && !s.subscribed && s.analysis_supported !== false))}>添加当前可用来源</button>
      {pendingBatch&&<section className="review-confirm" aria-label="订阅确认"><h3>将添加 {pendingBatch.length} 个来源</h3><p>确认前不会创建订阅。已订阅来源会跳过；停止不能撤回已提交的项目。</p><ul>{pendingBatch.slice(0,12).map(s=><li key={s.url}>{s.name||s.url}</li>)}</ul>{pendingBatch.length>12&&<p>另 {pendingBatch.length-12} 项</p>}<button disabled={busy} onClick={startBatch}>确认添加来源</button><button disabled={busy} onClick={()=>setPendingBatch(null)}>取消添加</button>{busy&&<button onClick={()=>{stopBatch.current=true;setBatchProgress('将停止后续项，等待已发出的请求结束。')}}>停止剩余添加</button>}</section>}
      {batchProgress&&<p role="status">{batchProgress}</p>}
      {batchResults.length>0&&<details open className="review-batch-results"><summary>本轮添加结果</summary>{batchResults.map((r,i)=><p key={i}>{r.source.name||r.source.url}：{({success:'添加成功',existing:'已存在，未重复添加',failed:'添加失败'})[r.state]} {r.error||''}</p>)}{batchResults.some(r=>r.state==='failed')&&<button disabled={busy} onClick={()=>add(batchResults.filter(r=>r.state==='failed').map(r=>r.source))}>仅重试失败来源</button>}</details>}
      <p className="ai-notice">X 已使用本机 x-cli guest Provider，无需登录、Cookie 或 API Key；上游限流时优先读取本地缓存。Instagram/Telegram 等其他社交源仍按各自适配器状态处理。</p>
      <form onSubmit={e=>{e.preventDefault();run(async()=>{const result=await apiClient.post("/v1/ai/x/probe",{handle:xHandle});setXProbe(result);setMessage(result.message)})}}>
        <label>X 用户名<input value={xHandle} onChange={e=>{setXHandle(e.target.value);setXProbe(null)}} placeholder="@OpenAI" required /></label>
        <button disabled={busy}>检查 X 来源</button>
        <button type="button" disabled={busy || !xProbe?.feed_ready || !xProbe?.profile_valid} onClick={()=>run(async()=>{
          const categories=await apiClient.get("/v1/categories")
          let category=categories.find(c=>c.title==="X 作者")
          if (!category) category=await apiClient.post("/v1/categories",{title:"X 作者"})
          await apiClient.post("/v1/ai/subscribe",{x_handle:xHandle,category_id:category.id})
          await refreshFeedData();invalidateArticleList();setMessage("已订阅 X guest 来源")
          await load(["catalog"],{replace:true})
        })}>订阅 X 来源</button>
      </form>
      {xProbe && <p className="ai-notice">适配器：{xProbe.adapter_configured ? "已配置" : "未配置"} · 直连 X：{xProbe.network_reachable ? "有 HTTP 响应" : "不可达"} · 本次帖子：{xProbe.post_count}。{xProbe.message}</p>}
      {status?.social_probe && <p className="ai-notice">最近 Telegram 公共路由实测：{status.social_probe.passed ? "成功" : "未通过，需检查服务器出站网络"}（HTTP {status.social_probe.http || "无响应"}）。这与 RSSHub 服务本身是否在线是两项不同检查。</p>}
      <form onSubmit={e=>{e.preventDefault();const url=new FormData(e.currentTarget).get("feed");add([{url,category:"手动来源"}])}}><label>自定义 RSS / RSSHub 地址<input required name="feed" type="url" placeholder="http://127.0.0.1:1200/telegram/channel/频道名" /></label><button disabled={busy}>添加订阅</button></form>
      <div className="ai-source-list">{filtered.slice(0,sourceLimit).map(s=><div key={s.url}><div><strong>{s.name}</strong><small>{s.category} · {s.subscribed ? "已订阅 · " : ""}{s.live_error ? "抓取异常 · " : ""}{s.status==="ok" ? "订阅可解析" : (s.subscribed ? "已接入阅读器" : (s.error || "待验证"))}</small><a href={s.url} target="_blank" rel="noreferrer">查看订阅地址 ↗</a>{s.subscribed&&Number(s.feed_id)>0&&<a href={`/inbox/feed/${Number(s.feed_id)}`}>打开此订阅 →</a>}{s.subscribed && s.feed_id && <SourceHistory key={s.feed_id} feedId={s.feed_id} />}</div><button disabled={busy || s.status!=="ok" || s.subscribed || s.analysis_supported === false} onClick={()=>add([s])}>{s.analysis_supported === false ? "需全文适配" : (s.subscribed ? "已添加" : "添加")}</button></div>)}</div>
      {!filtered.length&&!loading.catalog&&<p className="review-empty">没有匹配来源。<button onClick={resetSourceFilters}>清除目录条件</button></p>}{sourceLimit<filtered.length&&<button onClick={()=>setSourceLimit(n=>n+24)}>显示更多来源</button>}
    </section>}
    <footer>Miniflux + ReactFlux + RSSHub · AI 增强层独立保存分析，不替换原文章。<a href="/deployment" target="_blank" rel="noreferrer">部署状态</a></footer>
  </dialog>
}
