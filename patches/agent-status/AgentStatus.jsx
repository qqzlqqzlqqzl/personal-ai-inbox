import {useLayoutEffect,useRef} from 'react';
import {useStore} from '@nanostores/react';
import apiClient from '@/apis/ofetch';
import {authState} from '@/store/authState';
import {dataState} from '@/store/dataState';
import {mountSessionStatusPage} from '@/components/AgentStatus/status-controller.mjs';
import {noteSession} from '@/utils/note-session';
import {createReaderStatusClient} from '@/components/AgentStatus/reader-status-client.mjs';
import '@/components/AgentStatus/status.css';

export default function AgentStatus(){
  const root=useRef(null),{server}=useStore(authState,{keys:['server']});
  const {verifiedAuthSessionKey}=useStore(dataState,{keys:['verifiedAuthSessionKey']});
  useLayoutEffect(()=>{
    try{return mountSessionStatusPage(root.current,()=>createReaderStatusClient(apiClient,authState.get().server,window.location.href)(),noteSession);}
    catch{root.current.querySelector('#notice').textContent='当前登录服务器不匹配，无法读取状态。';}
  },[server,verifiedAuthSessionKey]);
  return <main ref={root} className="agent-status">
    <header><p className="eyebrow">AGENT STATUS</p><h1>任务状态</h1><p>页面每 60 秒刷新 · 发布目标 10 分钟 · 非硬实时</p></header>
    <section id="notice" className="notice" role="status" aria-live="polite">未知 · 尚无有效采样</section>
    <section className="times" aria-label="三种时间"><div>状态采样<time id="sample-time">未知</time></div><div>服务器成功拉取<time id="pull-time">未知</time></div><div>页面刷新尝试<time id="page-time">尚未刷新</time></div></section>
    <section id="counts" className="cards" aria-label="最后已知统计"/><p id="known" className="muted">统计未知</p>
    <section><h2>任务</h2><div id="tasks" className="tasks">暂无有效数据</div></section>
    <footer><button id="refresh" type="button">立即刷新</button><span>当前主任务树 · 排除 root · 管理员可见</span></footer>
  </main>;
}
