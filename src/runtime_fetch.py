from pathlib import Path
import urllib.request, json, hashlib, shutil, os
ROOT=Path('/home/ubuntu/ai-news')
meta=json.loads((ROOT/'docs/research/miniflux__v2/metadata.json').read_text())
rel=next(r for r in meta['releases'] if r['tag_name']=='2.3.3')
a=next(a for a in rel['assets'] if a['name']=='miniflux-linux-amd64')
out=ROOT/'runtime/miniflux'; tmp=out.with_suffix('.download')
req=urllib.request.Request(a['browser_download_url'],headers={'User-Agent':'AIReader/1.0'})
with urllib.request.urlopen(req,timeout=120) as r,tmp.open('wb') as f: shutil.copyfileobj(r,f)
actual=hashlib.sha256(tmp.read_bytes()).hexdigest()
expected=(a.get('digest') or '').removeprefix('sha256:')
if not expected:
 check=next(x for x in rel['assets'] if x['name']=='miniflux-linux-amd64.sha256')
 with urllib.request.urlopen(check['browser_download_url'],timeout=30) as r: expected=r.read().decode().split()[0]
if actual!=expected: raise RuntimeError('Miniflux checksum mismatch')
os.replace(tmp,out); out.chmod(0o755)
(ROOT/'docs/research/miniflux-binary.json').write_text(json.dumps({'version':'2.3.3','source':a['browser_download_url'],'sha256':actual},indent=2))
print('Miniflux binary verified',actual)
