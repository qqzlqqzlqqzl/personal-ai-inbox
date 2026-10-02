import {validateStatus,InvalidStatus,statusInstant} from './status-contract.mjs';
export function createStatusCache(){
  let last=null,failed=false,restricted=null;
  return {
    accept(value,now){
      const next=validateStatus(value,now);
      if(last?.sample&&next.sample){
        if(next.sample.sequence<last.sample.sequence||statusInstant(next.sample.observed_at)<statusInstant(last.sample.observed_at))throw new InvalidStatus('regression');
        if(next.sample.sequence===last.sample.sequence&&JSON.stringify(next.sample)!==JSON.stringify(last.sample))throw new InvalidStatus('sequence_conflict');
      }
      last=next;failed=false;restricted=null;
    },
    fail(error){
      const status=Number(error?.status??error?.response?.status??error?.statusCode);
      if(status===401||status===403||['Invalid auth','Stale auth request','Stale auth response'].includes(error?.message)){last=null;restricted=status===403?403:401;failed=false;}
      else failed=true;
    },
    clearPrivate(){last=null;failed=false;restricted=401;},
    get(){return {last,requestFailed:failed,restricted};}
  };
}