"""Background original-content extraction and structured AI evaluation."""
import asyncio, os, json, time, re, logging
import httpx
from bs4 import BeautifulSoup
from core import connect, discover, update, settings, event, hash_text, reserve_budget, close_budget
MF = 'http://127.0.0.1:8091/mf'
log = logging.getLogger('ai-news.worker')
def worker_headers(): return {'X-Auth-Token':os.environ.get('MINIFLUX_API_KEY','')}

def validate_result(data):
 if not isinstance(data, dict): raise ValueError('Model response must be an object')
 result = {}
 for key in ['score','technical_score','business_score']:
  n = data.get(key)
  if isinstance(n,bool) or not isinstance(n,(int,float)) or not 0 <= n <= 10: raise ValueError('Invalid score')
  result[key] = round(n,1)
 for key,limit in [('summary',450),('reason',400),('evidence',160),('content_type',30)]:
  if not isinstance(data.get(key),str) or not data[key].strip(): raise ValueError('Missing field '+key)
  result[key] = data[key].strip()[:limit]
 if not isinstance(data.get('worth_reading'),bool): raise ValueError('Missing boolean worth_reading')
 result['worth_reading'] = data['worth_reading']
 if not isinstance(data.get('tags'),list): raise ValueError('Missing tags')
 result['tags'] = [str(t)[:40] for t in data['tags'][:6] if isinstance(t,str)]
 return result

async def mf_get(client,path,**params):
 r = await client.get(MF+path,headers=worker_headers(),params=params,timeout=70)
 r.raise_for_status(); return r.json()
async def process_one(client, row, cfg):
 entry_id = row['entry_id']; phase = 'fetch_error'
 try:
  entry = await mf_get(client,f'/v1/entries/{entry_id}')
  source = 'original_url'
  current = entry.get('content','')
  if not row['extracted_at'] or hash_text(current) != row['content_hash']:
   update(entry_id,state='fetching')
   fetched = await mf_get(client,f'/v1/entries/{entry_id}/fetch-content',update_content='true')
   current = fetched.get('content','')
   if not current: raise ValueError('Original extraction returned no content')
  soup = BeautifulSoup(current,'html.parser')
  for el in soup(['script','style','noscript']): el.decompose()
  text = soup.get_text(' ',strip=True)
  if len(text) < 120: raise ValueError('Original text too short for a reliable evaluation')
  update(entry_id,content_hash=hash_text(current),input_chars=len(text),image_count=len(soup.find_all('img')),extracted_at=time.time(),error=None)
  if not os.environ.get('ARK_API_KEY'):
   update(entry_id,state='waiting_model'); return
  phase = 'ai_error'
  payload_text = text[:cfg['max_chars']]
  usage_id = reserve_budget(entry_id,payload_text,cfg)
  if usage_id is None:
   update(entry_id,state='budget_paused',next_try=time.time()+3600); return
  update(entry_id,state='analyzing',model=cfg['model'],prompt_hash=hash_text(cfg['prompt']),truncated=int(len(text)>len(payload_text)))
  message = json.dumps({'title':entry['title'],'url':entry['url'],'content_source':source,'truncated':len(text)>len(payload_text),'content':payload_text},ensure_ascii=False)
  body = {'model':cfg['model'],'messages':[{'role':'system','content':cfg['prompt']},{'role':'user','content':message}],'max_tokens':cfg['max_output_tokens']}
  if cfg['json_mode']: body['response_format'] = {'type':'json_object'}
  response = await client.post(cfg['base_url'].rstrip('/')+'/chat/completions',json=body,headers={'Authorization':'Bearer '+os.environ['ARK_API_KEY']},timeout=120)
  response.raise_for_status(); raw = response.json()
  content = raw['choices'][0]['message']['content']
  content = re.sub(r'^```(?:json)?\s*|\s*```$','',content.strip())
  result = validate_result(json.loads(content))
  if result['evidence'] not in text: raise ValueError('Evidence quotation is not present in the original text')
  tokens = int(raw.get('usage',{}).get('total_tokens') or 0)
  if tokens: close_budget(usage_id,tokens)
  update(entry_id,state='done',result=json.dumps(result,ensure_ascii=False),score=result['score'],technical_score=result['technical_score'],business_score=result['business_score'],tokens=tokens,analyzed_at=time.time(),error=None,attempts=0)
  event('analysis_done',entry_id,'Original text evaluated; source pictures kept in Miniflux')
 except asyncio.CancelledError: raise
 except Exception as exc:
  attempts = row['attempts']+1
  detail = type(exc).__name__
  if isinstance(exc,httpx.HTTPStatusError): detail += ' HTTP '+str(exc.response.status_code)
  elif isinstance(exc,ValueError): detail += ': '+str(exc)[:150]
  update(entry_id,state=phase,attempts=attempts,next_try=time.time()+min(86400,300*2**min(attempts,8)),error=detail)
  event(phase,entry_id,detail)

async def run_worker():
 async with httpx.AsyncClient(follow_redirects=False) as client:
  while True:
   cfg = settings()
   if not os.environ.get('MINIFLUX_API_KEY') or not cfg['enabled']:
    await asyncio.sleep(30); continue
   try:
    data = await mf_get(client,'/v1/entries',limit=200,order='published_at',direction='desc')
    discover(data.get('entries',[]))
    states = ['pending','fetch_error','ai_error','budget_paused']
    if os.environ.get('ARK_API_KEY'): states.append('waiting_model')
    with connect() as c:
     rows = c.execute('SELECT * FROM analyses WHERE state IN ('+','.join('?' for _ in states)+') AND next_try<=? AND attempts<5 ORDER BY published_at DESC LIMIT 6',(*states,time.time())).fetchall()
    for row in rows:
     if not settings()['enabled']: break
     await process_one(client,row,settings())
   except asyncio.CancelledError: raise
   except Exception as exc:
    event('worker_error',detail=type(exc).__name__)
   await asyncio.sleep(cfg['interval_seconds'])
