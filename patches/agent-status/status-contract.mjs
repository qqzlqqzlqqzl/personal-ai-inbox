export class InvalidStatus extends Error {}
const fields=(value,names)=>{
  if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).length!==names.length||names.some(key=>!Object.hasOwn(value,key)))throw new InvalidStatus('fields');
};
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const label=value=>typeof value==='string'&&Array.from(value).length>0&&Array.from(value).length<=80&&!/[\\/:<>\x00-\x1f]/u.test(value);
function timestamp(value){
  if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$/.test(value))throw new InvalidStatus('time');
  const time=new Date(value);if(!Number.isFinite(+time)||time.toISOString().slice(0,19)!==value.slice(0,19))throw new InvalidStatus('time');return +time;
}
export function validateStatus(value,now=Date.now()){
  const envelope=['sample','last_successful_pull_at','last_attempt_at','pull_status','freshness','stale_after_seconds','target_interval_seconds'];
  fields(value,Object.hasOwn(value??{},'error_code')?[...envelope,'error_code']:envelope);
  if(!['ok','failed','unknown'].includes(value.pull_status)||!['fresh','stale','unknown'].includes(value.freshness)||!integer(value.stale_after_seconds)||value.stale_after_seconds<1||!integer(value.target_interval_seconds)||value.target_interval_seconds<1)throw new InvalidStatus('envelope');
  for(const key of ['last_successful_pull_at','last_attempt_at'])if(value[key]!==null&&timestamp(value[key])>now+30000)throw new InvalidStatus('future_pull');
  let sample=null;
  if(value.sample!==null){
    const input=value.sample;fields(input,['schema_version','sequence','observed_at','tasks','statistics']);
    if(input.schema_version!==1||!integer(input.sequence)||timestamp(input.observed_at)>now+30000||!Array.isArray(input.tasks)||input.tasks.length>128||value.last_successful_pull_at===null)throw new InvalidStatus('sample');
    const statistics={total:input.tasks.length,capacity:input.statistics?.capacity,active:0,waiting:0,completed:0,failed:0,unknown:0};
    fields(input.statistics,Object.keys(statistics));if(!integer(statistics.capacity)||statistics.capacity<input.tasks.length||statistics.capacity>128)throw new InvalidStatus('capacity');
    const seen=new Set(),states={running:'active',waiting:'waiting',done:'completed',failed:'failed',unknown:'unknown'};
    const tasks=input.tasks.map(task=>{
      fields(task,['name','state','model']);if(!label(task.name)||!label(task.model)||seen.has(task.name)||typeof task.state!=='string'||!Object.hasOwn(states,task.state))throw new InvalidStatus('task');
      seen.add(task.name);statistics[states[task.state]]++;if(task.state==='waiting')statistics.active++;
      return Object.freeze({name:task.name,state:task.state,model:task.model});
    });
    for(const key of Object.keys(statistics))if(!integer(input.statistics[key])||input.statistics[key]!==statistics[key])throw new InvalidStatus('statistics');
    sample=Object.freeze({schema_version:1,sequence:input.sequence,observed_at:input.observed_at,tasks:Object.freeze(tasks),statistics:Object.freeze(statistics)});
  }else if(value.freshness!=='unknown'||value.pull_status==='ok')throw new InvalidStatus('missing_sample');
  return Object.freeze({sample,last_successful_pull_at:value.last_successful_pull_at,last_attempt_at:value.last_attempt_at,pull_status:value.pull_status,freshness:value.freshness,stale_after_seconds:value.stale_after_seconds,target_interval_seconds:value.target_interval_seconds});
}