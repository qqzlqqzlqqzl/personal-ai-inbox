import { useEffect, useRef, useState } from "react"
import apiClient from "@/apis/ofetch"
import useAppData from "@/hooks/useAppData"
import { aiState } from "@/store/aiState"
import { invalidateArticleList } from "@/store/contentState"

export default function AiPanel({ onClose }) {
  const dialog = useRef(null)
  const alive = useRef(true)
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
  const load = async () => {
    try {
      const [c, s, f, xr] = await Promise.all([
        ...["settings", "status", "catalog"].map(p => apiClient.get(`/v1/ai/${p}`)),
        apiClient.get("/v1/ai/x/roster"),
      ])
      if (!alive.current) return
      setConfig(c); setStatus(s); setSources(f); setXRoster(xr)
    } catch (e) { if (alive.current) setMessage(e.message) }
  }
  useEffect(() => { alive.current = true; dialog.current?.showModal(); load(); return () => { alive.current = false } }, [])
  const run = async (action) => {
    setBusy(true); setMessage("")
    try { await action() } catch (e) { setMessage(e.message) } finally { if (alive.current) setBusy(false) }
  }
  const save = () => run(async () => {
    await apiClient.put("/v1/ai/settings", config)
    aiState.setKey("minimum", config.minimum_score); invalidateArticleList(); setMessage("已保存到服务器")
  })
  const add = (items) => run(async () => {
    const categories = await apiClient.get("/v1/categories")
    let added = 0, failed = 0
    for (const source of items) {
      if (!alive.current) break
      try {
        let category = categories.find(c => c.title === source.category)
        if (!category) { category = await apiClient.post("/v1/categories", { title: source.category }); categories.push(category) }
        await apiClient.post("/v1/ai/subscribe", { url: source.url, category_id: category.id, crawler: false })
        added++
      } catch { failed++ }
      setMessage(`已添加 ${added}，失败或已存在 ${failed} / ${items.length}`)
    }
    await refreshFeedData(); invalidateArticleList()
  })
  const change = (key, value) => setConfig({ ...config, [key]: value })
  const filtered = sources.filter(s => `${s.name} ${s.category}`.toLowerCase().includes(search.toLowerCase()))
  const analysisCounts = status?.counts || {}
  const coverage = status?.coverage || {}
  const translationCounts = status?.translations?.counts || {}
  const articleTotal = coverage.reader_total || coverage.total_articles || Object.values(analysisCounts).reduce((a,b)=>a+b,0)
  const translated = (translationCounts.done || 0) + (translationCounts.native || 0)
  const translationTotal = Object.values(translationCounts).reduce((a,b)=>a+b,0)
  const queued = (analysisCounts.pending || 0) + (analysisCounts.waiting_model || 0) + (analysisCounts.budget_paused || 0)
  const attention = coverage.needs_attention || 0
  const lanes = Object.values(status?.kaggle?.lanes || {})
  const activeLanes = lanes.filter(l => ["processing","submitted","running"].includes(l?.state)).length
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
  return <dialog className="ai-dialog" ref={dialog} onCancel={onClose} onClose={onClose}>
    <header><h2>个人 AI 资讯控制台</h2><button aria-label="关闭" onClick={onClose}>×</button></header>
    <nav>{[["settings","模型与偏好"],["status","资源看板"],["sources","来源目录"]].map(([id,label]) => <button key={id} aria-pressed={tab===id} onClick={() => setTab(id)}>{label}</button>)}</nav>
    {message && <p className="ai-message" role="status">{message}</p>}
    {!config && <p>正在读取服务器配置……</p>}
    {tab === "settings" && config && <section className="ai-form">
      <p className="ai-notice">当前主链路为 Kaggle；下方 API 设置仅用于停用中的备用服务，不代表 Kaggle 的 Token 预算。</p>
      <p className="ai-notice">模型密钥{config.api_key_configured ? "已配置，调用结果以处理状态为准" : "尚未配置"}。密钥仅从服务器环境读取，网页不接收或回显密钥。修改接口会改变原文与模型认证的发送目标，只填写可信服务。</p>
      <label><input type="checkbox" disabled={status?.kaggle?.enabled} checked={config.enabled} onChange={e=>change("enabled",e.target.checked)} /> 开启后台分析</label>
      <label><input type="checkbox" disabled={status?.kaggle?.enabled} checked={config.translation_enabled !== false} onChange={e=>change("translation_enabled",e.target.checked)} /> 卡片标题与简介中文化（DeepSeek）</label>
      <p className="ai-notice">中文卡片与价值评分独立，结果缓存在服务器；翻译与评分共用下面的每日请求与 Token 总上限。正文保留原文。</p>
      <label>接口地址<input value={config.base_url} onChange={e=>change("base_url",e.target.value)} /></label>
      <label>模型 ID<input value={config.model} onChange={e=>change("model",e.target.value)} /></label>
      <label>个人筛选提示词<textarea rows={9} value={config.prompt} onChange={e=>change("prompt",e.target.value)} /></label>
      <div className="ai-field-grid">{[["daily_articles","每日最多模型请求"],["daily_tokens","每日 Token 预算"],["max_chars","单篇输入字符上限"],["minimum_score","默认最低推荐分"]].map(([key,label])=><label key={key}>{label}<input type="number" value={config[key]} onChange={e=>change(key,Number(e.target.value))}/></label>)}</div>
      <label><input type="checkbox" checked={config.json_mode} onChange={e=>change("json_mode",e.target.checked)} /> 使用 JSON 响应模式（需模型支持）</label>
      <button disabled={busy} onClick={save}>保存到服务器</button>
    </section>}
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
        <span className={status.kaggle?.enabled ? "ok" : ""}>Kaggle · {status.kaggle?.enabled ? ("已启用 · " + activeLanes + " 条运行中") : "未启用"}</span>
      </div>
      <div className="ai-dashboard-actions">
        <button disabled={busy} onClick={load}>刷新看板</button>
        <button disabled={busy || !attention} onClick={()=>run(async()=>{const r=await apiClient.post("/v1/ai/retry",{});setMessage("已重排 " + r.queued + " 个失败任务");await load()})}>重试可重试任务</button>
      </div>
      <details className="ai-diagnostics">
        <summary>详细诊断</summary>
        <h3>分析状态</h3>
        <div className="ai-state-grid">{Object.entries(status.counts).map(([key,value])=><div key={key}><strong>{value}</strong><span>{key}</span></div>)}</div>
        <h3>中文卡片状态</h3><p>{Object.entries(translationCounts).map(([k,v])=>k + ": " + v).join(" · ")}</p>
        {status.reading && <><h3>阅读行为</h3><p>打开 {status.reading.sessions} 次 · {status.reading.entries} 篇 · 有效前台阅读 {Math.round(status.reading.active_ms/1000)} 秒 · 深度阅读 {status.reading.deep_reads || 0} 次 · 平均滚动 {Math.round(status.reading.avg_scroll_pct || 0)}%</p>{status.reading.recent?.slice(0,6).map((r,i)=><p className="ai-event" key={[r.entry_id,r.opened_at,i].join("-")}>{new Date(r.opened_at*1000).toLocaleString()} · {Math.round((r.active_ms || 0)/1000)} 秒 · 滚动 {Math.round(r.max_scroll_pct || 0)}% · {r.starred ? "已收藏" : "未收藏"} · {r.title || ("#" + r.entry_id)}</p>)}</>}
        <h3>用量记录（UTC）</h3>{status.usage.map(u=><p key={u.day}>{u.day} · {u.calls} 次请求 · {u.tokens} Token</p>)}
        <h3>近期处理日志</h3>{status.events.map((e,i)=><p className="ai-event" key={i}>{new Date(e.at*1000).toLocaleString()} · {e.kind} · {e.detail}</p>)}
      </details>
    </section>}
    {tab === "sources" && <section>
      <p>先广泛收录，再按实际阅读价值裁剪。以下“可用”只表示本次成功解析订阅 XML，不代表每篇原文都能抓到。</p>
      {xRoster && <details><summary>X 核心名单：{xRoster.counts.total} 个 · timeline 有内容 {xRoster.counts.timeline_nonempty} · 空 {xRoster.counts.timeline_empty}（其中 {xRoster.counts.empty_with_fallback} 个已有稳定替代源）</summary><div className="ai-source-list">{xRoster.sources.map(s=><div key={s.handle}><div><strong>@{s.handle}</strong><small>{s.category} · {s.timeline_status === "nonempty" ? `X 已抓到 ${s.timeline_entries || 0} 条` : "X timeline 暂空"}{s.status === "fallback_active" ? " · 稳定替代源已启用" : ""}</small></div></div>)}</div></details>}
      <input aria-label="搜索来源" placeholder="搜索名称或分类" value={search} onChange={e=>setSearch(e.target.value)} />
      <button disabled={busy} onClick={()=>add(filtered.filter(s=>s.status==="ok" && !s.subscribed && s.analysis_supported !== false))}>添加当前可用来源</button>
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
        })}>订阅 X 来源</button>
      </form>
      {xProbe && <p className="ai-notice">适配器：{xProbe.adapter_configured ? "已配置" : "未配置"} · 直连 X：{xProbe.network_reachable ? "有 HTTP 响应" : "不可达"} · 本次帖子：{xProbe.post_count}。{xProbe.message}</p>}
      {status?.social_probe && <p className="ai-notice">最近 Telegram 公共路由实测：{status.social_probe.passed ? "成功" : "未通过，需检查服务器出站网络"}（HTTP {status.social_probe.http || "无响应"}）。这与 RSSHub 服务本身是否在线是两项不同检查。</p>}
      <form onSubmit={e=>{e.preventDefault();const url=new FormData(e.currentTarget).get("feed");add([{url,category:"手动来源"}])}}><label>自定义 RSS / RSSHub 地址<input required name="feed" type="url" placeholder="http://127.0.0.1:1200/telegram/channel/频道名" /></label><button disabled={busy}>添加订阅</button></form>
      <div className="ai-source-list">{filtered.map(s=><div key={s.url}><div><strong>{s.name}</strong><small>{s.category} · {s.subscribed ? "已订阅 · " : ""}{s.live_error ? "抓取异常 · " : ""}{s.status==="ok" ? "订阅可解析" : (s.error || "待验证")}</small><a href={s.url} target="_blank" rel="noreferrer">查看订阅地址 ↗</a></div><button disabled={busy || s.status!=="ok" || s.subscribed || s.analysis_supported === false} onClick={()=>add([s])}>{s.analysis_supported === false ? "需全文适配" : (s.subscribed ? "已添加" : "添加")}</button></div>)}</div>
    </section>}
    <footer>Miniflux + ReactFlux + RSSHub · AI 增强层独立保存分析，不替换原文章。<a href="/deployment" target="_blank" rel="noreferrer">部署状态</a></footer>
  </dialog>
}
