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

TERMINAL = {'COMPLETE', 'ERROR', 'CANCELLED', 'CANCELED'}

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
    def __init__(self, root, owner, client=None, kaggle_python=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not re.fullmatch(r'[a-zA-Z0-9_-]+', owner):
            raise ValueError('Invalid Kaggle owner')
        self.owner = owner
        self.kaggle_python = str(kaggle_python or sys.executable)
        self.client = client or self._cli
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS batches (
                id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL, state TEXT NOT NULL,
                remote_status TEXT, error TEXT, updated REAL NOT NULL)''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root/'batches.sqlite3', timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _cli(self, args, timeout):
        result = subprocess.run([self.kaggle_python, '-m', 'kaggle', *args], timeout=timeout,
                                capture_output=True, text=True, encoding='utf-8')
        # Never propagate raw client errors: they can include signed URLs.
        if result.returncode:
            raise RuntimeError('Kaggle command failed: ' + args[1])
        return result.stdout

    def row(self, batch_id):
        with self.db() as db:
            row = db.execute('SELECT * FROM batches WHERE id=?',(batch_id,)).fetchone()
        if row is None:
            raise KeyError(batch_id)
        return dict(row)

    def manifest(self,batch_id):
        value=json.loads((self.root/batch_id/'manifest.json').read_text(encoding='utf-8'))
        expected=self.row(batch_id)['manifest_hash']
        canonical={k:v for k,v in value.items() if k not in {'batch_id','manifest_hash'}}
        if value.get('batch_id')!=batch_id or value.get('manifest_hash')!=expected or digest(canonical)!=expected:
            raise ValueError('Prepared manifest was changed; do not submit or import')
        return value

    def prepare(self, manifest, template):
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
                return batch_id
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
            db.execute("UPDATE batches SET state='submitting',updated=? WHERE id=?",(time.time(),batch_id))
        try:
            output = self.client(['kernels','push','-p',str(self.root/batch_id),
                '--accelerator','NvidiaTeslaT4','--timeout',str(manifest['session_timeout'])],90)
            if not re.search(r'Kernel version \d+ successfully pushed', output):
                raise RuntimeError('Submission acknowledgement missing')
        except Exception as exc:
            self._set(batch_id,'submit_unknown',error=type(exc).__name__)
            raise RuntimeError('Submission uncertain; reconcile status before any retry') from None
        self._set(batch_id,'submitted')
        return self.row(batch_id)

    def _set(self, batch_id, state, remote=None, error=None):
        with self.db() as db:
            db.execute('UPDATE batches SET state=?,remote_status=COALESCE(?,remote_status),error=?,updated=? WHERE id=?',
                       (state,remote,error,time.time(),batch_id))

    def status(self, batch_id):
        before = self.row(batch_id)
        output = self.client(['kernels','status',self.owner+'/'+batch_id],45)
        match = re.search(r'KernelWorkerStatus\.([A-Z]+)',output)
        if not match:
            raise RuntimeError('Unrecognized status; retain previous state')
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

    def download(self, batch_id):
        row = self.status(batch_id)
        if row['remote_status'] not in TERMINAL:
            raise RuntimeError('Remote job is still active')
        if row['state'] in {'downloaded','imported'}:
            return self.verify_output(batch_id)
        folder = self.root/batch_id/'output'
        folder.mkdir(exist_ok=True)
        self.client(['kernels','output',self.owner+'/'+batch_id,'-p',str(folder)],180)
        evidence = self.verify_output(batch_id)
        self._set(batch_id,'downloaded',row['remote_status'])
        return evidence

    def verify_output(self, batch_id):
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
    parser.add_argument('action',choices=['prepare','submit','status','download','wait','retry','retire'])
    parser.add_argument('value')
    parser.add_argument('--template',default=str(Path(__file__).with_name('batch_runner.py')))
    parser.add_argument('--reason',help='Required when retiring a never-submitted batch')
    args = parser.parse_args()
    control = Controller(args.root,args.owner,kaggle_python=args.kaggle_python)
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
    main()
