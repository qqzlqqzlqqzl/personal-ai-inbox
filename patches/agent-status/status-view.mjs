export function displayState(value, pageNow = new Date(), requestFailed = false) {
  const sample = value?.sample;
  if (!sample) return {state: 'unknown', label: '未知 · 尚无有效采样', counts: null};
  const age = (pageNow - new Date(sample.observed_at)) / 1000;
  const pullAge = (pageNow - new Date(value.last_successful_pull_at)) / 1000;
  if (!Number.isFinite(age) || !Number.isFinite(pullAge) || age < -30 || !Number.isInteger(value.stale_after_seconds) || value.stale_after_seconds < 1 || !sample.statistics || !Array.isArray(sample.tasks) || !Object.values(sample.statistics).every(x=>Number.isInteger(x)&&x>=0)) return {state:'unknown',label:'未知 · 数据校验失败',counts:null};
  const stale = value.freshness === 'stale' || age > value.stale_after_seconds || pullAge > value.stale_after_seconds;
  const failed = requestFailed || value.freshness === 'unknown' || value.pull_status !== 'ok';
  const state = stale ? 'stale' : failed ? 'unknown' : 'fresh';
  return {state, label: stale ? '过期 · 仅显示最后已知状态' : failed ? '未知 · 读取失败，保留最后已知状态' : '采样有效', counts: sample.statistics};
}