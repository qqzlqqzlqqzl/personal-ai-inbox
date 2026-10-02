"""Durable, finite Kaggle batch lifecycle. Credentials stay in the Kaggle client.

One immutable notebook per batch. A failed/uncertain submit is reconciled by
status, never blindly resubmitted. A retry gets a new batch containing missing
items only. Local output download can be repeated without starting a GPU.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
try:
    from .dispatch_policy import DispatchStopped
except ImportError:
    from dispatch_policy import DispatchStopped
try:
    from .quota_guard import query_client
    from .recovery_policy import ProviderError, classify_failure, classify, exception_code
    from .absence_proof import prove_absent
    from .queue_dispatch import claimed_entries, DispatchBlocked, month_roots
except ImportError:
    from quota_guard import query_client
    from recovery_policy import ProviderError, classify_failure, classify, exception_code
    from absence_proof import prove_absent
    from queue_dispatch import claimed_entries, DispatchBlocked, month_roots

TERMINAL = {'COMPLETE', 'ERROR', 'CANCELLED', 'CANCELED'}
UNCERTAIN_NOT_FOUND_GRACE = {
    'quota': 660, 'capacity': 660, 'auth': 660, 'rate_limit': 660,
    'network': 1800, 'unknown': 1800, 'not_found': 660,
}


class RetiredManifest(ValueError):
    def __init__(self,batch_id):
        self.batch_id=batch_id
        super().__init__('retired_manifest_requires_new_attempt')

def digest(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()

def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.pending')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)

class Controller:
    def __init__(self, root, owner, client=None, kaggle_python=None, *, initialize=False, required_roots=None, admission=None):
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', owner):
            raise ValueError('Invalid Kaggle owner')
        self.admission = admission
        self._admit()
        self.root = Path(root).resolve()
        database = self.root / 'batches.sqlite3'
        if initialize and not database.exists():
            # First installation only. Sidecars, manifests and even old lock
            # files are evidence of prior use, never a license to recreate DB.
            try:
                if self.root.exists() and any(self.root.iterdir()):
                    raise DispatchBlocked('initialization_evidence')
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                descriptor = os.open(database, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
            except DispatchBlocked:
                raise
            except OSError:
                raise DispatchBlocked('unreadable_ledger') from None
        else:
            claimed_entries([self.root])
        self.required_roots = list(dict.fromkeys([self.root, *month_roots(self.root), *(required_roots or [])]))
        self.owner = owner
        self.kaggle_python = str(kaggle_python or sys.executable)
        original_client = client or self._cli
        if admission is None:
            self.client = original_client
        else:
            def guarded_client(args, timeout):
                self._admit()
                return original_client(args, timeout)
            self.client = guarded_client
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL, state TEXT NOT NULL,
                remote_status TEXT, error TEXT, updated REAL NOT NULL)''')
            db.execute('CREATE TABLE IF NOT EXISTS batch_progress (batch_id TEXT PRIMARY KEY, next_try REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, error TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS batch_claims (batch_id TEXT NOT NULL, entry_id INTEGER NOT NULL, PRIMARY KEY(batch_id,entry_id))')
        with self.db() as db:
            pending=[row['id'] for row in db.execute("SELECT id FROM batches WHERE state NOT IN ('imported','retired','resolved')")]
        for batch in pending:
            with self.db() as db:
                if db.execute('SELECT 1 FROM batch_claims WHERE batch_id=? LIMIT 1',(batch,)).fetchone():continue
            manifest=self.manifest(batch)
            refs={(batch,int(ref['entry_id'])) for item in manifest['items'] for ref in item.get('source_refs',[])}
            with self.db() as db:db.executemany('INSERT OR IGNORE INTO batch_claims VALUES (?,?)',refs)


    def _admit(self):
        if self.admission is not None:self.admission()

    @contextmanager
    def db(self):
        self._admit()
        try:
            db = sqlite3.connect((self.root/'batches.sqlite3').as_uri()+'?mode=rw', uri=True, timeout=15)
        except sqlite3.Error:
            raise DispatchBlocked('unreadable_ledger') from None
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
                self._admit()
        except sqlite3.OperationalError:
            raise DispatchBlocked('unreadable_ledger') from None
        except sqlite3.Error:
            raise DispatchBlocked('invalid_ledger') from None
        finally:
            db.close()

    def next_pending(self,now=None):
        now=time.time() if now is None else now
        with self.db() as db:
            row=db.execute("""SELECT b.id FROM batches b LEFT JOIN batch_progress p ON p.batch_id=b.id
                WHERE b.state NOT IN ('imported','retired','resolved') AND COALESCE(p.next_try,0)<=?
                ORDER BY CASE WHEN b.state IN ('submitting','submitted','running','submit_unknown') THEN 0
                              WHEN b.state IN ('terminal','downloaded') THEN 1 ELSE 2 END,b.updated LIMIT 1""",(now,)).fetchone()
        return row['id'] if row else None

    def next_retry(self):
        with self.db() as db:
            row=db.execute("""SELECT MIN(p.next_try) FROM batch_progress p JOIN batches b ON b.id=p.batch_id
                WHERE b.state NOT IN ('imported','retired','resolved') AND p.next_try>?""",(time.time(),)).fetchone()
        return row[0]

    def defer_local(self,batch_id,code):
        # Confirmed terminal output recovery never starts another GPU.
        row=self.row(batch_id)
        if row['remote_status'] not in TERMINAL:raise ValueError('Only terminal batches may defer local recovery')
        with self.db() as db:
            old=db.execute('SELECT failures FROM batch_progress WHERE batch_id=?',(batch_id,)).fetchone()
            failures=(old[0] if old else 0)+1
            retry_at=time.time()+min(3600,660*2**min(failures-1,3))
            db.execute('INSERT OR REPLACE INTO batch_progress VALUES (?,?,?,?)',(batch_id,retry_at,failures,code))
        return {'batch_id':batch_id,'state':'local_retry_scheduled','retry_at':retry_at,'code':code,'failures':failures,'gpu_resubmitted':False}

    def _cli(self, args, timeout):
        try:
            result = subprocess.run([self.kaggle_python, '-m', 'kaggle', *args], timeout=timeout,
                                    capture_output=True, text=True, encoding='utf-8')
        except (subprocess.TimeoutExpired, OSError):
            raise ProviderError('network') from None
        # Never propagate raw client errors: they can include signed URLs.
        if result.returncode:
            raise classify_failure(str(result.stderr or '') + '\n' + str(result.stdout or '')) from None
        return result.stdout

    def row(self, batch_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM batches WHERE id=?',(batch_id,)).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return dict(row)

    def outstanding(self):
        with self.db() as db:
            row=db.execute("SELECT * FROM batches WHERE state NOT IN ('imported','retired','resolved') "
                           "ORDER BY updated LIMIT 1").fetchone()
        return dict(row) if row else None

    def manifest(self,batch_id):
        value=json.loads((self.root/batch_id/'manifest.json').read_text(encoding='utf-8'))
        expected=self.row(batch_id)['manifest_hash']
        canonical={k:v for k,v in value.items() if k not in {'batch_id','manifest_hash'}}
        if value.get('batch_id')!=batch_id or value.get('manifest_hash')!=expected or digest(canonical)!=expected:
            raise ValueError('Prepared manifest was changed; do not submit or import')
        return value

    def prepare(self, manifest, template):
        self._admit()
        claimed_entries(self.required_roots)
        if not manifest.get('items'):
            return None  # Empty queue must not allocate a GPU.
        ids = [item['id'] for item in manifest['items']]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate item id')
        for item in manifest['items']:
            if not item.get('messages') or not item.get('input_hash'):
                raise ValueError('Missing messages or input version')
        if not 60 <= int(manifest.get('session_timeout',0)) <= 14400:
            raise ValueError('Batch needs a finite session timeout of 60..14400 seconds')
        if manifest.get('require_model_cache') and not manifest.get('dataset_sources'):
            raise ValueError('Cached-model batch must attach its model Dataset')
        manifest = {**manifest,'runner_sha256':hashlib.sha256(template.encode()).hexdigest()}
        canonical = digest(manifest)
        batch_id = 'qwen-inbox-' + canonical[:24]
        folder = self.root/batch_id
        folder.mkdir(exist_ok=True)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM batches WHERE id=?',(batch_id,)).fetchone()
            if previous:
                if previous['manifest_hash'] != canonical:
                    raise ValueError('Batch hash collision')
                if previous['state']=='retired':
                    raise RetiredManifest(batch_id)
                return batch_id
            claimed_entries(self.required_roots)
            self._admit()
            runtime_manifest = {**manifest, 'batch_id':batch_id, 'manifest_hash':canonical}
            atomic_json(folder/'manifest.json', runtime_manifest)
            if template.count('MANIFEST = None') != 1:
                raise ValueError('Runner template must have exactly one manifest slot')
            code = template.replace('MANIFEST = None', 'MANIFEST = '+repr(runtime_manifest))
            (folder/'runner.py').write_text(code,encoding='utf-8',newline='\n')
            atomic_json(folder/'prepared-code.json',{'sha256':hashlib.sha256(code.encode()).hexdigest()})
            atomic_json(folder/'kernel-metadata.json', {
                'id':self.owner+'/'+batch_id,'title':batch_id,'code_file':'runner.py',
                'language':'python','kernel_type':'script','is_private':True,
                'enable_gpu':True,'enable_internet':not manifest.get('require_model_cache',False),
                'kernel_sources':[] if manifest.get('runtime_dataset_source') else [manifest['runtime_source']],
                'dataset_sources':manifest.get('dataset_sources',[]),'competition_sources':[]})
            db.execute('INSERT INTO batches VALUES (?,?,?,NULL,NULL,?)',
                       (batch_id,canonical,'prepared',time.time()))
            refs={(batch_id,int(ref['entry_id'])) for item in manifest['items'] for ref in item.get('source_refs',[])}
            db.executemany('INSERT OR IGNORE INTO batch_claims VALUES (?,?)',refs)
        return batch_id

    def submit(self, batch_id):
        manifest=self.manifest(batch_id)
        folder=self.root/batch_id
        expected=json.loads((folder/'prepared-code.json').read_text())['sha256']
        if hashlib.sha256((folder/'runner.py').read_bytes()).hexdigest()!=expected:
            raise ValueError('Prepared runner was changed')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT state FROM batches WHERE id=?',(batch_id,)).fetchone()
            if not row:
                raise KeyError(batch_id)
            if row['state'] != 'prepared':
                return self.row(batch_id)
            active = db.execute("SELECT id FROM batches WHERE state IN ('submitting','submitted','running','submit_unknown') AND id<>?",(batch_id,)).fetchone()
            if active:
                raise RuntimeError('Another batch is active: '+active['id'])
            claimed_entries(self.required_roots)
            quota_gate = query_client(self.client)
            if not quota_gate['allowed']:
                return {**self.row(batch_id), 'submission_blocked':True, 'quota_gate':quota_gate}
            self._admit()
            db.execute("UPDATE batches SET state='submitting',updated=? WHERE id=?",(time.time(),batch_id))
        try:
            output = self.client(['kernels','push','-p',str(self.root/batch_id),
                '--accelerator','NvidiaTeslaT4','--timeout',str(manifest['session_timeout'])],90)
            if not re.search(r'Kernel version \d+ successfully pushed', output):
                raise ProviderError(classify(output))
        except DispatchStopped:
            raise
        except Exception as exc:
            code=exception_code(exc)
            # A typed quota/auth rejection still requires remote reconciliation.
            self._set(batch_id,'submit_unknown',error=code)
            atomic_json(folder/'submit-diagnostic.json',{'at':time.time(),'code':code})
            raise ProviderError(code) from None
        self._set(batch_id,'submitted')
        return self.row(batch_id)

    def _set(self, batch_id, state, remote=None, error=None):
        with self.db() as db:
            db.execute('UPDATE batches SET state=?,remote_status=COALESCE(?,remote_status),error=?,updated=? WHERE id=?',
                       (state,remote,error,time.time(),batch_id))

    def status(self, batch_id, *, allow_retirement=True):
        before = self.row(batch_id)
        if before['state']=='retired' or (before['state'] in {'terminal','downloaded','imported','resolved'} and before['remote_status'] in TERMINAL):
            return before
        try:
            output = self.client(['kernels','status',self.owner+'/'+batch_id],45)
        except ProviderError as exc:
            if not allow_retirement:raise
            return self._reconcile_absence(batch_id,before,exc)
        match = re.search(r'KernelWorkerStatus\.([A-Z]+)',output)
        if not match:
            raise ProviderError('protocol')
        remote = match[1]
        if remote not in TERMINAL | {'QUEUED','RUNNING'}:
            raise RuntimeError('Unknown remote status: '+remote)
        if before['state'] in {'downloaded','imported'}:
            if remote not in TERMINAL:
                raise RuntimeError('Immutable notebook unexpectedly started another run')
            return before
        state = 'terminal' if remote in TERMINAL else ('running' if remote=='RUNNING' else 'submitted')
        self._set(batch_id,state,remote)
        return self.row(batch_id)

    def _reconcile_absence(self,batch_id,before,error):
        """Retire only uncertain, never-observed submissions after repeated proof.

        This performs read-only observations and a local CAS. It never retries,
        resubmits, cancels remote work, or treats quota depletion as absence.
        """
        import math
        import fcntl
        now=time.time()
        eligible=(error.code in {'not_found','inaccessible'}
                  and before['state'] in {'submitting','submit_unknown'}
                  and before['remote_status'] is None and now-before['updated']>=1800)
        if not eligible:
            raise error
        folder=self.root/batch_id
        with (folder/'absence.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            path=folder/'absence-observations.json'
            try:
                proof=prove_absent(self.client,self.owner,batch_id)
            except (ProviderError,ValueError,OSError,subprocess.SubprocessError):
                proof=None
            if not proof:
                # Failed/ambiguous evidence breaks the sequence; retain the job.
                if path.exists():
                    atomic_json(path,{'version':1,'owner':self.owner,'batch_id':batch_id,
                                      'count':0,'last_at':now,'reason':'absence_not_proven'})
                raise error
            fields=('state','remote_status','updated','error','manifest_hash')
            def unchanged(row):
                return all(row[key]==before[key] for key in fields)
            current=self.row(batch_id)
            if not unchanged(current):
                return current
            now=time.time()
            try:
                previous=json.loads(path.read_text())
                valid=(previous.get('version')==1 and previous.get('owner')==self.owner
                       and previous.get('batch_id')==batch_id and previous.get('state')==before['state']
                       and previous.get('ledger_updated')==before['updated'] and type(previous.get('count')) is int
                       and previous['count']==1 and type(previous.get('listed_count')) is int and previous['listed_count']>0
                       and type(previous.get('pages')) is int and 2<=previous['pages']<=10
                       and re.fullmatch(r'[0-9a-f]{64}',previous.get('listing_sha256',''))
                       and math.isfinite(previous['first_at']) and math.isfinite(previous['last_at'])
                       and 0<=previous['first_at']<=previous['last_at']<=now and now-previous['last_at']<=3600)
            except (OSError,ValueError,TypeError,KeyError,AttributeError):
                valid=False
            if not valid:
                atomic_json(path,{'version':1,**proof,'state':before['state'],
                                  'ledger_updated':before['updated'],'count':1,'first_at':now,'last_at':now})
                raise ProviderError('not_found')
            if now-previous['last_at']<660:
                raise ProviderError('not_found')
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE')
                current=db.execute('SELECT * FROM batches WHERE id=?',(batch_id,)).fetchone()
                if not unchanged(current):
                    return dict(current)
                reason='network' if before['error']=='network' else 'unknown'
                db.execute("UPDATE batches SET state='retired',error=?,updated=? WHERE id=?",
                           ('confirmed_not_found_after_'+reason,now,batch_id))
            atomic_json(path,{'version':1,**proof,'state':before['state'],
                              'ledger_updated':before['updated'],'count':2,
                              'first_at':previous['first_at'],'last_at':now,'retired':True})
            return self.row(batch_id)

    def download(self, batch_id, salvage=False):
        self._admit()
        row = self.status(batch_id)
        if row['remote_status'] not in TERMINAL:
            raise RuntimeError('Remote job is still active')
        if row['state'] in {'downloaded','imported'}:
            return self.verify_output(batch_id,salvage=salvage)
        folder = self.root/batch_id/'output'
        folder.mkdir(exist_ok=True)
        self.client(['kernels','output',self.owner+'/'+batch_id,'-p',str(folder)],180)
        self._admit()
        evidence = self.verify_output(batch_id,salvage=salvage)
        self._set(batch_id,'downloaded',row['remote_status'])
        return evidence

    def verify_output(self, batch_id, salvage=False):
        if salvage:return self.salvage_output(batch_id)
        folder = self.root/batch_id
        manifest = self.manifest(batch_id)
        expected = {item['id']:item['input_hash'] for item in manifest['items']}
        results = []
        result_file=folder/'output/results.jsonl'
        interrupted=self.row(batch_id)['remote_status'] in TERMINAL-{'COMPLETE'}
        if not result_file.exists() and not interrupted:
            raise ValueError('Completed job has no results file')
        lines=result_file.read_bytes().splitlines(keepends=True) if result_file.exists() else []
        truncated_tail=False
        for index,line in enumerate(lines):
            try:
                value = json.loads(line)
            except (ValueError,UnicodeError):
                if interrupted and index==len(lines)-1 and not line.endswith(b'\n'):
                    truncated_tail=True
                    break
                raise ValueError('Corrupted result record') from None
            if value.get('batch_id') != batch_id or value.get('manifest_hash') != manifest['manifest_hash']:
                raise ValueError('Result belongs to another batch')
            key = value.get('id')
            if key not in expected or value.get('input_hash') != expected[key]:
                raise ValueError('Unexpected input version')
            if key in {row['id'] for row in results}:
                raise ValueError('Duplicate output item')
            if value.get('status') not in {'ok','error'}:
                raise ValueError('Invalid item status')
            results.append(value)
        evidence = {'batch_id':batch_id,'results':results,
                    'missing_ids':sorted(set(expected)-{row['id'] for row in results}),
                    'interrupted_partial_record':truncated_tail}
        atomic_json(folder/'verified-results.json',evidence)
        atomic_json(folder/'download-verified.json', {
            'batch_id':batch_id,'result_sha256':hashlib.sha256(result_file.read_bytes()).hexdigest() if result_file.exists() else None,
            'downloaded_items':len(results),'missing_ids':evidence['missing_ids'],
            'interrupted_partial_record':truncated_tail,
            'remote_status':self.row(batch_id)['remote_status'],'verified_at':time.time()})
        return evidence

    def salvage_output(self,batch_id):
        """Recover independently valid records without ever editing raw output.

        A poisoned/duplicate/mismatched item is excluded, not silently repaired.
        Strict legacy verify_output remains available for diagnostics/tests.
        """
        folder=self.root/batch_id;manifest=self.manifest(batch_id)
        if self.row(batch_id)['remote_status'] not in TERMINAL:raise ValueError('Output is not terminal')
        expected={item['id']:item['input_hash'] for item in manifest['items']}
        path=folder/'output/results.jsonl';valid={};poisoned=set();rejected=[];tail=False
        if path.exists():
            if path.stat().st_size>64*1024**2:raise ValueError('Output exceeds bounded parser limit')
            with path.open('rb') as stream:
                for index,line in enumerate(stream,1):
                    key=None;reason=None
                    try:
                        if len(line)>2*1024**2:raise ValueError('oversized_record')
                        value=json.loads(line)
                        if not isinstance(value,dict):raise ValueError('invalid_record_type')
                        key=value.get('id')
                        if not isinstance(key,str) or key not in expected:raise ValueError('unknown_item')
                        if value.get('batch_id')!=batch_id or value.get('manifest_hash')!=manifest['manifest_hash']:raise ValueError('wrong_batch')
                        if value.get('input_hash')!=expected[key]:raise ValueError('wrong_input_version')
                        if value.get('status') not in ('ok','error'):raise ValueError('invalid_item_status')
                        if value['status']=='ok' and not isinstance(value.get('content'),str):raise ValueError('invalid_content')
                        if key in valid:
                            if valid[key]!=value:raise ValueError('conflicting_duplicate')
                            continue
                        if key not in poisoned:valid[key]=value
                    except (ValueError,UnicodeError,TypeError,RecursionError) as exc:
                        reason=str(exc) if str(exc) in {'oversized_record','invalid_record_type','unknown_item','wrong_batch','wrong_input_version','invalid_item_status','invalid_content','conflicting_duplicate'} else 'malformed_record'
                        if isinstance(key,str) and key in expected:poisoned.add(key);valid.pop(key,None)
                        tail=tail or not line.endswith(b'\n')
                        rejected.append({'line':index,'id':key if isinstance(key,str) and key in expected else None,'code':reason})
        results=[value for key,value in valid.items() if key not in poisoned]
        evidence={'batch_id':batch_id,'results':results,'missing_ids':sorted(set(expected)-set(valid)),
                  'interrupted_partial_record':tail,'rejected_records':rejected,'missing_results_file':not path.exists()}
        atomic_json(folder/'verified-results.json',evidence)
        atomic_json(folder/'download-verified.json',{'batch_id':batch_id,'result_sha256':hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None,
                    'downloaded_items':len(results),'missing_ids':evidence['missing_ids'],'rejected_records':rejected,
                    'remote_status':self.row(batch_id)['remote_status'],'verified_at':time.time(),'salvage':True})
        return evidence

    def retry(self,batch_id,template,invalid_ids=()):
        if self.row(batch_id)['state'] not in {'downloaded','imported'}:
            raise ValueError('Download and verify terminal output before preparing a retry')
        evidence=self.verify_output(batch_id)
        original=self.manifest(batch_id)
        pending=set(evidence['missing_ids']) | {item['id'] for item in evidence['results'] if item['status']=='error'} | set(invalid_ids)
        if not pending <= {item['id'] for item in original['items']}:
            raise ValueError('Unknown failed item')
        manifest={k:v for k,v in original.items() if k not in {'batch_id','manifest_hash','runner_sha256'}}
        manifest.update(items=[item for item in original['items'] if item['id'] in pending],
                        resume_of=batch_id,attempt=int(original.get('attempt',1))+1)
        return self.prepare(manifest,template)

    def retire(self,batch_id,reason):
        if not reason or not reason.strip():
            raise ValueError('A retirement reason is required')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM batches WHERE id=?',(batch_id,)).fetchone()
            if not row:
                raise KeyError(batch_id)
            if row['state']=='retired':
                return dict(row)
            if row['state']!='prepared' or row['remote_status'] is not None:
                raise ValueError('Only never-submitted prepared batches may be retired')
            db.execute("UPDATE batches SET state='retired',error=?,updated=? WHERE id=?",
                       (reason.strip(),time.time(),batch_id))
        return self.row(batch_id)

    def wait(self, batch_id, timeout=7200):
        deadline = time.monotonic()+timeout
        while True:
            state = self.status(batch_id)
            if state['remote_status'] in TERMINAL:
                return state
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                return state  # Observation timeout never means remote failure.
            time.sleep(min(660,remaining))
            if time.monotonic()>=deadline:
                return state

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root',required=True)
    parser.add_argument('--owner',required=True)
    parser.add_argument('--kaggle-python',help='Isolated Kaggle CLI Python, separate from Inbox dependencies')
    parser.add_argument('action',choices=['init','prepare','submit','status','download','wait','retry','retire'])
    parser.add_argument('value',nargs='?')
    parser.add_argument('--template',default=str(Path(__file__).with_name('batch_runner.py')))
    parser.add_argument('--reason',help='Required when retiring a never-submitted batch')
    args = parser.parse_args()
    global CONTROL_CONTEXT
    CONTROL_CONTEXT={'root':args.root}
    control = Controller(args.root,args.owner,kaggle_python=args.kaggle_python,
                         initialize=args.action=='init')
    if args.action=='init':
        print(json.dumps({'state':'initialized','gpu_started':False}))
        return
    if args.value is None:
        parser.error('value is required for this action')
    if args.action=='prepare':
        result = control.prepare(json.loads(Path(args.value).read_text(encoding='utf-8')),
                                 Path(args.template).read_text(encoding='utf-8'))
    elif args.action=='retry':
        result=control.retry(args.value,Path(args.template).read_text(encoding='utf-8'))
    elif args.action=='retire':
        result=control.retire(args.value,args.reason)
    else:
        result = getattr(control,args.action)(args.value)
    # Article results stay in private files; report status/counters only.
    if isinstance(result,dict) and 'results' in result:
        result = {k:v for k,v in result.items() if k!='results'} | {'count':len(result['results'])}
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:
        main()
    except DispatchBlocked as exc:
        try:from .queue_dispatch import block_with_backoff
        except ImportError:from queue_dispatch import block_with_backoff
        print(json.dumps(block_with_backoff(globals().get('CONTROL_CONTEXT',{}).get('root'),exc)))
