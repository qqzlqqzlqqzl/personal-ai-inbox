"""Deployment verification: emit status only, never credentials or model content."""
import json, os, subprocess, sys
import httpx
from initialize_secrets import ROOT, PRIVATE, ENV, read_env
from core import settings

def main():
    values = read_env('ai.env')
    services = {}
    for name in ('postgres','miniflux','rsshub','web'):
        result = subprocess.run(['systemctl','--user','is-active',f'ai-news-{name}.service'],
            env=ENV,capture_output=True,text=True,timeout=15)
        services[name] = result.stdout.strip()
    with httpx.Client(timeout=15, trust_env=False) as client:
        r = client.get('http://127.0.0.1:8092/healthz')
        health = r.json()
        me = client.get('http://127.0.0.1:8092/mf/v1/me',headers={'X-Auth-Token':values['MINIFLUX_API_KEY']})
    report = {'services':services,'healthz':health,'miniflux_me_http':me.status_code}
    if '--ark' in sys.argv:
        cfg = settings()
        if cfg['base_url'] != 'https://ark.cn-beijing.volces.com/api/v3': raise RuntimeError('Unexpected Ark endpoint')
        try:
            with httpx.Client(timeout=120,trust_env=False) as client:
                r = client.post(cfg['base_url']+'/chat/completions',
                    headers={'Authorization':'Bearer '+values['ARK_API_KEY']},
                    json={'model':cfg['model'],'messages':[{'role':'user','content':'Reply with OK.'}],'max_tokens':32})
            ok = r.status_code == 200 and bool(r.json().get('choices'))
            report['ark'] = {'success':ok,'model':cfg['model'],'http':r.status_code}
        except httpx.HTTPError:
            report['ark'] = {'success':False,'model':cfg['model'],'http':None}
    print(json.dumps(report,ensure_ascii=False))
    if not all(v=='active' for v in services.values()) or not health.get('ready') or not health.get('model_configured') or not health.get('reader_worker_configured') or me.status_code!=200: return 1
    return 0 if report.get('ark',{'success':True})['success'] else 1

if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        print('Verification failed: '+type(exc).__name__,file=sys.stderr)
        sys.exit(1)
