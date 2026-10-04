import {validateStatus,InvalidStatus,statusInstant} from './status-contract.mjs';
import {sampleFingerprint} from './status-fingerprint.mjs';
export function createStatusCache(){
  let last=null,lastIdentity=null,failed=false,restricted=null;
  return {
    accept(value,now){
      const next=validateStatus(value,now);
      // No observation is not a new zero-task observation. Retain last-good and
      // its high-water identity; only explicit auth/session retirement clears it.
      if(last?.sample&&next.sample===null){failed=true;return;}
      const identity=next.sample?sampleFingerprint(value.sample):null;
      if(last?.sample&&next.sample){
        if(next.sample.sequence<last.sample.sequence||statusInstant(next.sample.observed_at)<statusInstant(last.sample.observed_at))throw new InvalidStatus('regression');
        if(next.sample.sequence===last.sample.sequence&&identity!==lastIdentity)throw new InvalidStatus('sequence_conflict');
      }
      last=next;lastIdentity=identity;failed=false;restricted=null;
    },
    fail(error){
      const status=Number(error?.status??error?.response?.status??error?.statusCode);
      if(status===401||status===403||['Invalid auth','Stale auth request','Stale auth response'].includes(error?.message)){last=null;lastIdentity=null;restricted=status===403?403:401;failed=false;}
      else failed=true;
    },
    clearPrivate(){last=null;lastIdentity=null;failed=false;restricted=401;},
    get(){return {last,requestFailed:failed,restricted};}
  };
}
