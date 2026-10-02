import {displayState} from './status-view.mjs';
import {createStatusCache} from './status-cache.mjs';
export function mountStatusPage(root,readStatus){
const el = id => root.querySelector('#'+id);
const labels = {total:'任务总数',active:'调度运行',waiting:'其中等待',completed:'已完成',failed:'失败',unknown:'未知'};
const states = {running:'运行中',waiting:'等待中',done:'已完成',failed:'失败',unknown:'未知'};
const cache=createStatusCache();let busy = false,disposed=false;
function time(id, value) {el(id).textContent = value ? new Date(value).toLocaleString('zh-CN',{hour12:false,timeZoneName:'short'}) : '未知';}
function render() {
  if(disposed)return;
  const {last,requestFailed,restricted}=cache.get();
  const d = displayState(last,new Date(),requestFailed);
  el('notice').textContent = d.label; el('notice').className = 'notice '+d.state;
  if(restricted)el('notice').textContent=restricted===403?'无权访问 · 私有状态已清除':'登录已失效 · 私有状态已清除，请重新登录';
  time('sample-time',last?.sample?.observed_at); time('pull-time',last?.last_successful_pull_at);
  el('known').textContent = d.counts ? '最后已知统计；调度运行含等待，实际推理活动未知。过期或未知不代表空闲。' : '统计未知';
  el('counts').replaceChildren(...Object.entries(labels).map(([key,label])=>{const item=document.createElement('div');item.className='card';item.append(document.createTextNode(label));const strong=document.createElement('strong');strong.textContent=d.counts?.[key] ?? '—';item.append(strong);return item;}));
  const items=(last?.sample?.tasks??[]).map(task=>{const item=document.createElement('div');item.className='task';const text=document.createElement('div');const name=document.createElement('strong');name.textContent=task.name;const model=document.createElement('div');model.className='model';model.textContent=task.model;text.append(name,model);const badge=document.createElement('span');badge.className='badge';badge.textContent=states[task.state]??'未知';item.append(text,badge);return item;});
  el('tasks').replaceChildren(...items);if(!items.length)el('tasks').textContent=d.counts ? '该有效采样未列出任务' : '暂无有效数据';
}
async function refresh() {
  if(busy||disposed)return;busy=true;el('refresh').disabled=true;time('page-time',new Date().toISOString());
  try {const value=await readStatus();if(!disposed)cache.accept(value);}
  catch(error) {if(!disposed)cache.fail(error);}finally{busy=false;if(!disposed){el('refresh').disabled=false;render();}}
}
const visibility=()=>{if(!document.hidden)refresh();};el('refresh').addEventListener('click',refresh);const poll=setInterval(refresh,60000),age=setInterval(render,1000);document.addEventListener('visibilitychange',visibility);refresh();
return ()=>{cache.clearPrivate();render();disposed=true;clearInterval(poll);clearInterval(age);el('refresh').removeEventListener('click',refresh);document.removeEventListener('visibilitychange',visibility);};
}