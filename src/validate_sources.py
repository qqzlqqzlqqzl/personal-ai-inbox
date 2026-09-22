"""Validate public feed endpoints; never treats RSS parsing as full-text proof."""
from pathlib import Path
import json, time, datetime, concurrent.futures, xml.etree.ElementTree as ET
import httpx
ROOT=Path('/home/ubuntu/ai-news')
sources={}
previous=ROOT/'sources.catalog.json'
if previous.exists():
 for s in json.loads(previous.read_text()):
  sources[s['url']]={k:s[k] for k in ['category','name','url']}
for line in (ROOT/'docs/research/source-candidates.txt').read_text().splitlines():
 category,name,url=line.split('|',2)
 sources[url]={'category':category,'name':name,'url':url}
original=Path('/home/ubuntu/personal-news/sources.opml')
if original.exists():
 for e in ET.parse(original).iter('outline'):
  if e.get('xmlUrl'): sources.setdefault(e.get('xmlUrl'),{'category':'原有订阅','name':e.get('text') or e.get('title'),'url':e.get('xmlUrl')})
def check(source):
 start=time.time(); out={**source,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'failed','original_text_verified':False}
 try:
  with httpx.Client(follow_redirects=True,timeout=12,max_redirects=5,headers={'User-Agent':'PersonalReaderFeedCheck/1.0'}) as client:
   with client.stream('GET',source['url']) as r:
    out['http_status']=r.status_code; r.raise_for_status(); chunks=[]; size=0
    for part in r.iter_bytes(65536):
     size+=len(part)
     if size>2*1024*1024: raise ValueError('Feed larger than validation limit')
     chunks.append(part)
    data=b''.join(chunks); root=ET.fromstring(data)
    if root.tag.split('}')[-1] not in ['rss','feed','RDF']: raise ValueError('Not an RSS or Atom document')
    out.update(status='ok',entries=sum(1 for e in root.iter() if e.tag.split('}')[-1] in ['item','entry']),bytes=len(data),final_url=str(r.url))
 except httpx.HTTPStatusError as e: out['error']='HTTP '+str(e.response.status_code)
 except httpx.TimeoutException: out['error']='本次连接超时'
 except Exception as e: out['error']=type(e).__name__+': '+str(e)[:100]
 out['elapsed_seconds']=round(time.time()-start,2)
 print(out['name'],out['status'],out.get('error',''),flush=True)
 return out
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
 results=list(pool.map(check,sources.values()))
(ROOT/'sources.catalog.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
(ROOT/'docs/research/source-validation.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
opml=ET.Element('opml',version='2.0'); head=ET.SubElement(opml,'head'); ET.SubElement(head,'title').text='个人 AI 资讯 · 本次可解析来源'
body=ET.SubElement(opml,'body'); groups={}
for s in results:
 if s['status']!='ok': continue
 group=groups.setdefault(s['category'],ET.SubElement(body,'outline',text=s['category'],title=s['category'])) if s['category'] not in groups else groups[s['category']]
 ET.SubElement(group,'outline',type='rss',text=s['name'],title=s['name'],xmlUrl=s['url'])
ET.indent(opml); ET.ElementTree(opml).write(ROOT/'artifacts/validated-sources.opml',encoding='utf-8',xml_declaration=True)
print('RESULT',sum(s['status']=='ok' for s in results),'/',len(results),'RSS endpoints parsed. Full article extraction is not implied.',flush=True)
