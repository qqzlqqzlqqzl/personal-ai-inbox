"""Release gate: wait for readiness, reject stale artifacts, keep failed-run evidence."""
import json, os, subprocess, time, shutil
import xml.etree.ElementTree as ET
from pathlib import Path
import httpx
ROOT=Path('/home/ubuntu/ai-news');OUT=ROOT/'artifacts/ui-review'
PY=str(ROOT/'runtime/venv/bin/python');NODE=str(ROOT/'runtime/node/bin/node')
env={**os.environ,'PYTHONPATH':str(ROOT/'src')+':'+str(ROOT/'tests'),'AI_NEWS_WEB_BASE':'https://106.53.40.6'}
report={'at':time.time(),'stages':[],'readiness':False}
with httpx.Client(timeout=4,trust_env=False) as client:
 deadline=time.monotonic()+45
 while time.monotonic()<deadline:
  try:
   r=client.get('http://127.0.0.1:8092/healthz')
   public=client.get('https://106.53.40.6/inbox/')
   if r.status_code==200 and r.json().get('ready') and public.status_code==200:
    report['readiness']=True;break
  except (httpx.HTTPError,ValueError):pass
  time.sleep(.4)
if not report['readiness']:
 report['passed']=False
 (OUT/'suite.json').write_text(json.dumps(report,indent=2))
 raise SystemExit('Readiness gate failed; no acceptance claim')
stages=[
 ('unit',[PY,'-m','pytest','-q','tests','--junitxml=artifacts/ui-review/unit-tests.xml'], 'artifacts/ui-review/unit-tests.xml',150),
 ('pagination-js',[NODE,'tests/test_ai_pagination.mjs'],None,30),
 ('fetch-js',[NODE,'tests/test_fetch_content.mjs'],None,30),
 ('prefetch-js',[NODE,'tests/test_reading_session.mjs'],None,30),
 ('ui-browser',[PY,'tests/ui_review_browser.py'],'artifacts/ui-review/browser.json',240),
 ('reader-detail',[PY,'tests/reader_detail_quality_browser.py'],'artifacts/reader-detail-quality.json',120),
 ('native-tail',[PY,'tests/native_tail_acceptance.py'],'artifacts/ui-review/native-tail.json',210),
 ('comprehensive',[PY,'tests/browser_acceptance.py'],'artifacts/browser-acceptance-public.json',160),
 ('scroll',[PY,'tests/scroll_session_acceptance.py'],'artifacts/scroll-session/browser.json',260),
 ('live-api',[PY,'tests/live_acceptance.py'],'artifacts/live-acceptance.json',75),
]
for name,cmd,artifact,timeout in stages:
 started=time.time();item={'name':name,'started':started};log=ROOT/'logs'/('ui-review-gate-'+name+'.log')
 try:
  with log.open('w') as f:
   child=subprocess.run(cmd,cwd=ROOT,env=env,stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
  item['exit_code']=child.returncode;item['passed']=child.returncode==0
  if artifact:
   path=ROOT/artifact;item['fresh']=path.exists() and path.stat().st_mtime>=started
   item['passed']=item['passed'] and item['fresh']
   if item['fresh'] and path.suffix=='.json':
    data=json.loads(path.read_text());item['checks']=len(data.get('checks',[]))
    item['passed']=item['passed'] and data.get('passed') is True and data.get('at',0)>=started
    if not item['passed']:item['error']=(data.get('error') or '')[:300]
    dest=OUT/(name+'.json')
    if dest!=path:shutil.copy2(path,dest)
   elif item['fresh']:
    suites=list(ET.parse(path).getroot().iter('testsuite'))
    item['checks']=sum(int(s.get('tests',0)) for s in suites)
    item['passed']=item['passed'] and all(int(s.get('failures',0))+int(s.get('errors',0))==0 for s in suites)
 except Exception as exc:
  item.update(passed=False,error=type(exc).__name__)
 item['seconds']=round(time.time()-started,2);report['stages'].append(item)
 (OUT/'suite.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
 print(json.dumps(item,ensure_ascii=False),flush=True)
report['passed']=report['readiness'] and all(s['passed'] for s in report['stages'])
(OUT/'suite.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
raise SystemExit(0 if report['passed'] else 1)
