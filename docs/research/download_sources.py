import pathlib,urllib.request,json,tarfile,concurrent.futures,shutil
ROOT=pathlib.Path('/home/ubuntu/ai-news')
def snapshot(pair):
 repo,name=pair; d=ROOT/'docs/research'/repo.replace('/','__'); meta=json.loads((d/'metadata.json').read_text()); tree=json.loads((d/'tree.json').read_text()); sha=tree['sha']
 target=ROOT/'upstream'/name; target.mkdir(exist_ok=True); archive=ROOT/'runtime'/(name+'.tar.gz')
 url='https://codeload.github.com/'+meta['full_name']+'/tar.gz/'+sha
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'AIReader/1.0'})
  with urllib.request.urlopen(req,timeout=90) as r,archive.open('wb') as f: shutil.copyfileobj(r,f)
  with tarfile.open(archive) as t:
   for m in t.getmembers():
    parts=pathlib.PurePosixPath(m.name).parts[1:]
    if not parts: continue
    m.name=str(pathlib.PurePosixPath(*parts)); t.extract(m,target,filter='data')
  (target/'UPSTREAM_REVISION').write_text(meta['full_name']+'\n'+sha+'\n')
  print(name,'READY',sha,flush=True)
 except Exception as e: print(name,'FAILED',str(e),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
 list(ex.map(snapshot,[('electh/ReactFlux','reactflux'),('WCY-dt/MrRSS','mrrss'),('Qetesh/miniflux-ai','miniflux-ai'),('DIYgod/RSSHub','rsshub')]))
