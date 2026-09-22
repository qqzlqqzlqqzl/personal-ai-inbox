"""Live isolated cross-provider failover drill. Run with system Python (PyYAML)."""
from pathlib import Path
import json,yaml,time,subprocess,os,secrets,urllib.request,copy
ROOT=Path('/home/ubuntu/ai-news')
home=ROOT/'.private'/('proxy-drill-'+str(int(time.time())))
home.mkdir(mode=0o700)
chosen=[]
for provider,match,label in [('lingyun','台湾｜06','PRIMARY'),('yahaha','SG-A','BACKUP')]:
 data=yaml.safe_load((ROOT/'.private/mihomo/providers'/ (provider+'.yaml')).read_text())
 node=copy.deepcopy(next(n for n in data['proxies'] if match in n['name']))
 node['name']=label;chosen.append(node)
secret=secrets.token_urlsafe(24)
cfg={'mixed-port':17891,'bind-address':'127.0.0.1','allow-lan':False,'external-controller':'127.0.0.1:19091','secret':secret,'mode':'rule','log-level':'silent','proxies':chosen,'proxy-groups':[{'name':'FAILOVER','type':'fallback','proxies':['PRIMARY','BACKUP'],'url':'https://www.gstatic.com/generate_204','interval':5,'timeout':2000,'lazy':False,'expected-status':204,'max-failed-times':1}],'rules':['MATCH,FAILOVER']}
p=home/'config.yaml'
def write():
 p.write_text(yaml.safe_dump(cfg));p.chmod(0o600)
write()
proc=subprocess.Popen(['timeout','100',str(ROOT/'runtime/mihomo/mihomo'),'-d',str(home)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def api(path,method='GET',data=None):
 req=urllib.request.Request('http://127.0.0.1:19091'+path,data=json.dumps(data).encode() if data else None,headers={'Authorization':'Bearer '+secret,'Content-Type':'application/json'},method=method)
 with opener.open(req,timeout=5) as r:return json.loads(r.read() or b'{}')
def phase(name,expected):
 started=time.monotonic();selected=None
 for _ in range(15):
  time.sleep(1)
  try:selected=api('/proxies/FAILOVER').get('now')
  except Exception:continue
  if selected==expected:
   r=subprocess.run(['curl','--noproxy','','--proxy','http://127.0.0.1:17891','--max-time','8','-s','-o','/dev/null','-w','%{http_code}','https://www.gstatic.com/generate_204'],capture_output=True,text=True,timeout=10)
   if r.returncode==0 and r.stdout=='204':return {'phase':name,'selected':selected,'http_status':204,'seconds':round(time.monotonic()-started,2),'passed':True}
 return {'phase':name,'selected':selected,'passed':False}
try:
 results=[phase('healthy_primary','PRIMARY')]
 original=copy.deepcopy(cfg['proxies'][0]);cfg['proxies'][0]['server']='127.0.0.1';cfg['proxies'][0]['port']=9;write();api('/configs?force=true','PUT',{'path':str(p)})
 results.append(phase('primary_unreachable_cross_provider_fallback','BACKUP'))
 cfg['proxies'][0]=original;write();api('/configs?force=true','PUT',{'path':str(p)})
 results.append(phase('primary_recovers','PRIMARY'))
 report={'at':time.time(),'isolated_ports':[17891,19091],'production_proxy_untouched':True,'primary_provider':'lingyun','backup_provider':'yahaha','checks':results,'passed':all(x['passed'] for x in results)}
 (ROOT/'artifacts/proxy-failover-acceptance.json').write_text(json.dumps(report,indent=2));print(json.dumps(report));assert report['passed']
finally:
 proc.terminate();proc.wait(timeout=10)
