"""Restart only this project's four services; verify persisted state and restore test flags."""
import sys, time, json, subprocess
from pathlib import Path
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ops_common import client, GATEWAY
from initialize_secrets import ENV, ROOT
NAMES=['ai-news-postgres','ai-news-miniflux','ai-news-rsshub','ai-news-web']
report={'at':time.time(),'whole_server_rebooted':False}
def systemctl(*args):
    r=subprocess.run(['systemctl','--user',*args],env=ENV,capture_output=True,text=True,timeout=90)
    if r.returncode:raise RuntimeError('Project service operation failed')
    return r.stdout.strip()
with client() as c:
    baseline=c.get('/v1/entries?limit=1').json();entry=baseline['entries'][0];eid=entry['id']
    before=len(c.get('/v1/feeds').json())
    c.put('/v1/entries',json={'entry_ids':[eid],'status':'read','starred':True}).raise_for_status()
try:
    systemctl('stop','ai-news-web','ai-news-miniflux','ai-news-rsshub')
    systemctl('restart','ai-news-postgres')
    systemctl('start','ai-news-miniflux','ai-news-rsshub','ai-news-web')
    for _ in range(45):
        try:
            if httpx.get(GATEWAY+'/readyz',timeout=3,trust_env=False).status_code==200:break
        except httpx.HTTPError:pass
        time.sleep(1)
    else:raise RuntimeError('Readiness not recovered')
    with client() as c:
        actual=c.get(f'/v1/entries/{eid}').json()
        report['read_and_star_survived']=actual['status']=='read' and actual['starred'] is True
        report['feeds_preserved']=len(c.get('/v1/feeds').json())==before
        report['entries_preserved']=c.get('/v1/entries?limit=1').json()['total']>=baseline['total']
        report['ai_status_available']=c.get('/v1/ai/status').status_code==200
    report['services']={name:systemctl('is-active',name) for name in NAMES}
    report['passed']=all(report[k] for k in ['read_and_star_survived','feeds_preserved','entries_preserved','ai_status_available']) and all(v=='active' for v in report['services'].values())
except Exception as exc:
    report.update(passed=False,error=type(exc).__name__+': '+str(exc))
finally:
    try:
        systemctl('start',*NAMES)
        with client() as c:c.put('/v1/entries',json={'entry_ids':[eid],'status':entry['status'],'starred':entry['starred']}).raise_for_status()
        report['test_flags_restored']=True
    except Exception:
        report['test_flags_restored']=False;report['passed']=False
report['seconds']=round(time.time()-report['at'],2)
(ROOT/'artifacts/restart-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(report,ensure_ascii=False))
sys.exit(0 if report['passed'] else 1)
