"""Finite private Notebook runner; MANIFEST is embedded by batch_control."""
import glob
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor,as_completed

MANIFEST = None

def server_layout(manifest):
    if manifest.get('split_mode','layer') not in ('layer','tensor'):
        raise ValueError('Unsupported GPU split mode')
    if manifest.get('ubatch_size',128) not in (128,512):
        raise ValueError('Unsupported microbatch size')
    parallel=manifest.get('parallel_requests',1)
    if parallel not in (1,2):
        raise ValueError('Only one or two simultaneous requests have been configured')
    return parallel,manifest['context_size']*parallel

def probe_messages(repetitions):
    filler='This neutral filler record contains no verification codes. '
    text=('Start code: MAPLE-731.\n'+filler*repetitions+
          '\nMiddle code: QUARTZ-482.\n'+filler*repetitions+'\nEnd code: HARBOR-956.')
    return [{'role':'system','content':'Return a JSON object with a codes array containing the three verification codes from the start, middle and end, in that order.'},
            {'role':'user','content':text}]

def calibrate_probe(post,target):
    def count(repetitions):
        prompt=post('/apply-template',{'messages':probe_messages(repetitions)},30)['prompt']
        return len(post('/tokenize',{'content':prompt,'add_special':True},30)['tokens'])
    lo,hi=0,target
    while lo<hi:
        mid=(lo+hi+1)//2
        if count(mid)<=target:
            lo=mid
        else:
            hi=mid-1
    return probe_messages(lo),count(lo)

def generation_parameters(manifest,item,input_tokens):
    settings=manifest.get('generation',{})
    available=manifest['context_size']-input_tokens-16
    if available<item['max_tokens']:
        raise ValueError('input_exceeds_context_with_output_reserve')
    budget=available if settings.get('use_remaining_context') else item['max_tokens']
    params={key:settings[key] for key in ('temperature','top_p','top_k','min_p',
            'presence_penalty','repeat_penalty','seed') if key in settings}
    params.setdefault('temperature',0)
    return {**params,'max_tokens':budget,'chat_template_kwargs':{'enable_thinking':settings.get('thinking',False)}}

def main():
    import requests  # Kaggle runtime only; pure control-side helpers need no HTTP dependency.
    assert isinstance(MANIFEST,dict), 'Prepare this template with batch_control.py'
    manifest = MANIFEST
    started = time.monotonic()
    deadline = started+manifest['session_timeout']-120
    out = Path('/kaggle/working')
    temporary = Path('/kaggle/tmp/qwen-inbox')
    temporary.mkdir(parents=True,exist_ok=True)
    result_file = out/'results.jsonl'
    result_file.touch()
    report = {'batch_id':manifest['batch_id'],'manifest_hash':manifest['manifest_hash'],
              'model':manifest['model'],'stage':'starting','temporary_results_only':True,
              'completed_items':0,'failed_items':0}
    server = None
    stop = threading.Event()

    def save():
        report['elapsed_seconds']=time.monotonic()-started
        with (out/'summary.json').open('w',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2)
            stream.flush()
            os.fsync(stream.fileno())

    def stage(name):
        report['stage']=name
        save()
        print('STAGE '+name,flush=True)

    def remaining(limit):
        value=min(limit,deadline-time.monotonic())
        if value<=0:
            raise TimeoutError('Batch deadline reached')
        return value

    def run(args,limit,log):
        with (out/log).open('a') as stream:
            subprocess.run(args,timeout=remaining(limit),check=True,stdout=stream,stderr=subprocess.STDOUT)

    def memory_monitor():
        with (out/'gpu-memory.jsonl').open('a') as stream:
            while not stop.is_set():
                try:
                    data=subprocess.run(['nvidia-smi','--query-gpu=index,memory.used,memory.total,utilization.gpu',
                        '--format=csv,noheader,nounits'],timeout=15,check=True,capture_output=True,text=True).stdout
                    memory={line.split(':',1)[0]:int(line.split()[1])
                            for line in Path('/proc/meminfo').read_text().splitlines()
                            if line.startswith(('MemTotal:','MemAvailable:'))}
                    server_memory={}
                    if server is not None and server.poll() is None:
                        server_memory={line.split(':',1)[0]:int(line.split()[1])
                            for line in Path(f'/proc/{server.pid}/status').read_text().splitlines()
                            if line.startswith(('VmRSS:','VmHWM:'))}
                    stream.write(json.dumps({'seconds':time.monotonic()-started,'gpu':data.strip(),
                        'system_memory_kib':memory,'server_memory_kib':server_memory})+'\n')
                    stream.flush()
                except Exception as exc:
                    stream.write(json.dumps({'error':type(exc).__name__})+'\n')
                stop.wait(5)

    def post(path,data,limit=600):
        response=requests.post('http://127.0.0.1:8080'+path,json=data,timeout=remaining(limit))
        response.raise_for_status()
        return response.json()

    def append(value):
        with result_file.open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(value,ensure_ascii=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())

    try:
        stage('runtime')
        threading.Thread(target=memory_monitor,daemon=True).start()
        # Kaggle Datasets unpack .tar.gz on upload and discard symlink metadata.
        # A .bin copy preserves the exact signed-off archive bytes for hash checking.
        runtime_files=list(Path('/kaggle/input').rglob('llama-runtime.tar.gz'))+list(Path('/kaggle/input').rglob('llama-runtime.bin'))
        if len(runtime_files)!=1:
            raise ValueError('Expected one attached runtime archive')
        with runtime_files[0].open('rb') as stream:
            runtime_hash=hashlib.file_digest(stream,'sha256').hexdigest()
        if runtime_hash!=manifest['runtime_sha256']:
            raise ValueError('Attached runtime revision/hash mismatch')
        with tarfile.open(runtime_files[0]) as archive:
            archive.extractall(temporary/'runtime',filter='data')
        binary=temporary/'runtime/bin/llama-server'
        runtime_env={**os.environ,'LD_LIBRARY_PATH':str(binary.parent)+':'+os.environ.get('LD_LIBRARY_PATH','')}
        check=subprocess.run([str(binary),'--version'],env=runtime_env,timeout=30,
                             capture_output=True,text=True,check=True)
        report['runtime_version']=(check.stdout+'\n'+check.stderr).strip()
        t=time.monotonic()
        stage('download')
        model_info=manifest['model']
        model=temporary/model_info['filename']
        # Optional attached model cache must contain exactly the selected GGUF.
        cached=list(Path('/kaggle/input').rglob(model_info['filename']))
        if len(cached)>1:
            raise ValueError('Ambiguous attached model cache')
        if cached:
            model=cached[0]
            report['model_source']='attached_dataset'
        else:
            if manifest.get('require_model_cache'):
                raise ValueError('Required model cache missing; cold download is disabled')
            downloader=temporary/'download.py'
            downloader.write_text('from huggingface_hub import hf_hub_download\n'+
                'hf_hub_download(repo_id='+repr(model_info['model_repo'])+', filename='+repr(model_info['filename'])+
                ', revision='+repr(model_info['model_revision'])+', local_dir='+repr(str(temporary))+')\n')
            run([sys.executable,str(downloader)],1800,'download.log')
            report['model_source']='huggingface_cold_download'
        if model.stat().st_size!=model_info['size']:
            raise ValueError('Model size mismatch')
        with model.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=model_info['sha256']:
                raise ValueError('Model SHA256 mismatch')
        report['download_hash_seconds']=time.monotonic()-t
        t=time.monotonic()
        stage('load')
        generation=manifest.get('generation',{})
        thinking=bool(generation.get('thinking',False))
        report['generation']=generation
        parallel,total_context=server_layout(manifest)
        report.update(parallel_requests=parallel,context_per_request=manifest['context_size'])
        args=[str(binary),'-m',str(model),'-ngl','99','--split-mode',manifest.get('split_mode','layer'),'--tensor-split','1,1',
            '--ctx-size',str(total_context),'--parallel',str(parallel),'--fit','off',
            '--batch-size','512','--ubatch-size',str(manifest.get('ubatch_size',128)),'--flash-attn','on',
            '--host','127.0.0.1','--port','8080',
            '--reasoning','on' if thinking else 'off',
            '--reasoning-budget',str(generation.get('reasoning_budget',-1) if thinking else 0),
            '--reasoning-format','deepseek' if thinking else 'auto',
            '--log-verbosity','4',
            '--chat-template-kwargs',json.dumps({'enable_thinking':thinking})]
        report['server_args']=args
        logfile=(out/'server.log').open('w')
        server=subprocess.Popen(args,stdout=logfile,stderr=subprocess.STDOUT,
                                start_new_session=True,env=runtime_env)
        load_deadline=min(deadline,time.monotonic()+300)
        while time.monotonic()<load_deadline:
            if server.poll() is not None:
                raise RuntimeError('Model process exited during load')
            try:
                response=requests.get('http://127.0.0.1:8080/health',timeout=5)
                if response.status_code==200:
                    break
            except requests.RequestException:
                pass
            time.sleep(2)
        else:
            raise TimeoutError('Model load timeout')
        report['load_seconds']=time.monotonic()-t
        response=requests.get('http://127.0.0.1:8080/props',timeout=10)
        response.raise_for_status()
        report['props']=response.json()
        if report['props']['total_slots']!=parallel or report['props']['default_generation_settings']['n_ctx']!=manifest['context_size']:
            raise ValueError('Actual per-request context or parallel slot count differs from manifest')
        stage('inference')
        def infer_item(item):
            begin=time.monotonic()
            value={'batch_id':manifest['batch_id'],'manifest_hash':manifest['manifest_hash'],
                   'id':item['id'],'input_hash':item['input_hash'],'status':'error'}
            try:
                if server.poll() is not None:
                    raise RuntimeError('Model process exited')
                prompt=post('/apply-template',{'messages':item['messages']},30)['prompt']
                tokens=post('/tokenize',{'content':prompt,'add_special':True},30)['tokens']
                value['input_tokens']=len(tokens)
                parameters=generation_parameters(manifest,item,len(tokens))
                value['generation_max_tokens']=parameters['max_tokens']
                response=post('/v1/chat/completions',{'model':model_info['filename'],
                    'messages':item['messages'],**parameters,
                    'response_format':{'type':'json_object'},
                    },900 if thinking else 180)
                choice=response['choices'][0]
                if choice.get('finish_reason')=='length':
                    raise ValueError('output_token_limit')
                content=choice['message'].get('content')
                if not content:
                    raise ValueError('empty_content')
                reasoning=choice['message'].get('reasoning_content') or ''
                value['reasoning_chars']=len(reasoning)
                value['reasoning_sha256']=hashlib.sha256(reasoning.encode()).hexdigest() if reasoning else None
                if reasoning:
                    value['reasoning_token_estimate']=len(post('/tokenize',{'content':reasoning,'add_special':False},30)['tokens'])
                # Business validators run on the control side against the same input.
                value.update(status='ok',content=content,usage=response.get('usage'),timings=response.get('timings'))
            except Exception as exc:
                value['error']=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
            value['seconds']=time.monotonic()-begin
            return value
        report['phase_wall_seconds']={}
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            for kind in dict.fromkeys(item['kind'] for item in manifest['items']):
                phase_started=time.monotonic()
                futures={pool.submit(infer_item,item):item for item in manifest['items'] if item['kind']==kind}
                for future in as_completed(futures):
                    item=futures[future]
                    value=future.result()
                    report['completed_items' if value['status']=='ok' else 'failed_items']+=1
                    append(value)
                    save()
                    print('ITEM '+item['id']+' '+value['status'],flush=True)
                report['phase_wall_seconds'][kind]=time.monotonic()-phase_started
                save()
        report['all_items_attempted']=True
        # Diagnostics stay outside business results and can never enter Inbox imports.
        if manifest.get('context_probes'):
            stage('context_probes')
            report['context_probes']=[]
            def run_probe(target):
                probe={'target_input_tokens':target,'synthetic':True,'cache_prompt':False}
                begin=time.monotonic()
                try:
                    messages,count=calibrate_probe(post,target)
                    probe['input_tokens']=count
                    probe['messages_sha256']=hashlib.sha256(json.dumps(messages,ensure_ascii=False).encode()).hexdigest()
                    params=generation_parameters(manifest,{'max_tokens':1024},count)
                    probe['generation_max_tokens']=params['max_tokens']
                    inference_started=time.monotonic()
                    probe['inference_started_monotonic']=inference_started
                    result=post('/v1/chat/completions',{'model':model_info['filename'],
                        'messages':messages,**params,'cache_prompt':False,
                        'response_format':{'type':'json_object'}},1800)
                    probe['inference_seconds']=time.monotonic()-inference_started
                    probe['inference_finished_monotonic']=time.monotonic()
                    choice=result['choices'][0]
                    content=choice['message'].get('content') or ''
                    thought=choice['message'].get('reasoning_content') or ''
                    probe.update(content=content,finish_reason=choice.get('finish_reason'),
                        usage=result.get('usage'),timings=result.get('timings'),reasoning_chars=len(thought))
                    if thought:
                        probe['reasoning_token_estimate']=len(post('/tokenize',{'content':thought,'add_special':False},30)['tokens'])
                    probe['passed']=(choice.get('finish_reason')!='length' and
                        json.loads(content).get('codes')==['MAPLE-731','QUARTZ-482','HARBOR-956'])
                except Exception as exc:
                    probe.update(passed=False,error=str(exc) if isinstance(exc,ValueError) else type(exc).__name__)
                probe['total_seconds']=time.monotonic()-begin
                return probe
            for target in manifest['context_probes']:
                probe=run_probe(target)
                report['context_probes'].append(probe)
                save()
                print('PROBE '+str(target)+' '+str(probe['passed']),flush=True)
            if parallel==2:
                # Two real long inputs must coexist, not merely fit sequentially.
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures=[pool.submit(run_probe,60000) for _ in range(2)]
                    report['parallel_context_probes']=[future.result() for future in futures]
                probes=report['parallel_context_probes']
                report['parallel_context_overlap_seconds']=max(0,
                    min(p.get('inference_finished_monotonic',0) for p in probes)-
                    max(p.get('inference_started_monotonic',0) for p in probes))
                save()
        stage('finished')
    except BaseException as exc:
        report['fatal_error']=type(exc).__name__
        raise
    finally:
        if server is not None:
            if server.poll() is None:
                os.killpg(server.pid,signal.SIGTERM)
                try:
                    server.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(server.pid,signal.SIGKILL)
                    server.wait(timeout=10)
            report['server_returncode']=server.returncode
        stop.set()
        report['process_cleanup_complete']=True
        # This remains temporary until the platform saves output after execution.
        save()

if __name__=='__main__':
    main()
