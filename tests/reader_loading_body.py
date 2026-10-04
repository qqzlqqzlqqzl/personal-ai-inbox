"""Synthetic original-prose DOM admission and bounded passive action observations.

These are DOM observations, never a claim that a frame has reached the display.
"""
import json
import math
from reader_loading_fixture import require

PROSE_TEXT = '合成性能正文，只用于测量阅读时序与滚动。' * 12
BODY_CONTRACT = 'exact-fixture-prose-visible-dom-v1'
SCROLL_ROOT = '.article-content .scroll-container[data-native-scroll="true"], .article-content .scroll-container .simplebar-content-wrapper'

SNAPSHOT_FUNCTION = r'''() => {
  const expected=__PROSE__, roots=[...document.querySelectorAll(__ROOT__)];
  const body=document.querySelector('.article-body'), article=document.querySelector('.article-content');
  const box=e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}};
  const viewport={left:0,top:0,right:innerWidth,bottom:innerHeight};
  const root=roots.length===1?roots[0]:null, rootBox=root?box(root):null;
  const computed=e=>{let opacity=1,visible=true;for(let n=e;n;n=n.parentElement){const s=getComputedStyle(n);
    opacity*=Number(s.opacity);if(s.display==='none'||s.visibility==='hidden'||s.visibility==='collapse')visible=false;}
    return {visible:visible&&opacity>0,opacity};};
  const paragraphs=body?[...body.querySelectorAll('p')].filter(p=>p.textContent===expected&&
    !p.closest('.article-note,.article-source-footer,.ai-verdict-detail,.ai-summary')):[];
  const prose=paragraphs.map(p=>{const rect=box(p),style=computed(p);let clip={...viewport};const clips=[];
    for(let n=p.parentElement;n;n=n.parentElement){const s=getComputedStyle(n),r=box(n);
      const x=/(auto|scroll|hidden|clip)/.test(s.overflowX),y=/(auto|scroll|hidden|clip)/.test(s.overflowY);
      if(x){clip.left=Math.max(clip.left,r.left);clip.right=Math.min(clip.right,r.right)}
      if(y){clip.top=Math.max(clip.top,r.top);clip.bottom=Math.min(clip.bottom,r.bottom)}
      if(x||y)clips.push({className:n.className,rect:r,x,y});}
    if(rootBox){clip.left=Math.max(clip.left,rootBox.left);clip.right=Math.min(clip.right,rootBox.right);
      clip.top=Math.max(clip.top,rootBox.top);clip.bottom=Math.min(clip.bottom,rootBox.bottom);}
    const intersection={width:Math.max(0,Math.min(rect.right,clip.right)-Math.max(rect.left,clip.left)),
      height:Math.max(0,Math.min(rect.bottom,clip.bottom)-Math.max(rect.top,clip.top))};
    return {exactText:true,rect,computed:style,clip,clips,intersection,
      visible:Boolean(root&&root.contains(p)&&style.visible&&intersection.width>0&&intersection.height>0)};});
  const heading=article?.querySelector('.article-title')?.textContent?.trim()||null;
  return {contract:'exact-fixture-prose-visible-dom-v1',at:performance.timeOrigin+performance.now(),
    url:location.href,pathname:location.pathname,heading,bodyPresent:Boolean(body),
    ariaBusy:body?.getAttribute('aria-busy')??null,containerTextLength:body?.innerText.length??0,
    rootCount:roots.length,scrollRoot:root?{className:root.className,rect:rootBox,scrollTop:root.scrollTop,
      scrollHeight:root.scrollHeight,clientHeight:root.clientHeight}:null,viewport,
    matchingParagraphs:paragraphs.length,visibleParagraphs:prose.filter(p=>p.visible).length,prose,
    imageNodes:body?[...body.querySelectorAll('img[src^="/fixture-images/"]')].map(i=>({src:i.getAttribute('src'),
      complete:i.complete,naturalWidth:i.naturalWidth,naturalHeight:i.naturalHeight})):[],
    ready:Boolean(body&&body.getAttribute('aria-busy')!=='true'&&heading==='性能样本 001'&&prose.some(p=>p.visible)),
    painted:null};
}'''.replace('__PROSE__', json.dumps(PROSE_TEXT, ensure_ascii=False)).replace('__ROOT__', json.dumps(SCROLL_ROOT))

BODY_OBSERVER = r'''(() => {
  const snapshot=__SNAPSHOT__;
  const record={contract:'exact-fixture-prose-visible-dom-v1',samples:[],truncated:false,paint_observed:false};
  window.__readerBodyObservation=record;
  window.__readerBodySnapshot=snapshot;
  window.__readerBodyMark=(kind,detail=null)=>{const value={kind,detail,...snapshot()};
    if(record.samples.length>=512){record.truncated=true;return value;}
    record.samples.push(value);return value;};
  const mark=window.__readerBodyMark;
  const observer=new MutationObserver(()=>mark('mutation'));
  observer.observe(document,{subtree:true,childList:true,characterData:true,
    attributes:true,attributeFilter:['class','style','aria-busy','hidden']});
  window.__readerBodyStop=()=>observer.disconnect();
  addEventListener('pagehide',window.__readerBodyStop,{once:true});
  for(const type of ['pointerdown','click','keydown'])addEventListener(type,event=>{
    const target=event.target instanceof Element?event.target.closest('[data-entry-id],button'):null;
    if(!target)return;if(type==='keydown'&&!['Enter','Escape',' '].includes(event.key))return;
    mark('user-'+type,{entryId:target.getAttribute('data-entry-id'),button:target.getAttribute('aria-label')||target.textContent?.trim().slice(0,80),
      key:type==='keydown'?event.key:null,isTrusted:event.isTrusted});
  },true);
  mark('installed');
})()'''.replace('__SNAPSHOT__', SNAPSHOT_FUNCTION)


def mark(page, kind):
    return page.evaluate('(kind)=>window.__readerBodyMark(kind)', kind)


def collect(page):
    return page.evaluate('window.__readerBodyObservation')


def validate_ready(snapshot):
    require(snapshot.get('contract') == BODY_CONTRACT and snapshot.get('ready') is True and
            snapshot.get('painted') is None, 'actual prose DOM readiness was not established')
    require(snapshot.get('heading') == '性能样本 001' and snapshot.get('bodyPresent') is True and
            snapshot.get('ariaBusy') in (None, 'false') and type(snapshot.get('rootCount')) is int and snapshot['rootCount'] == 1,
            'wrong article identity, busy body or ambiguous scroll root')
    require(type(snapshot.get('visibleParagraphs')) is int and snapshot['visibleParagraphs'] > 0 and
            type(snapshot.get('matchingParagraphs')) is int and
            snapshot['matchingParagraphs'] >= snapshot['visibleParagraphs'], 'no exact visible original prose')
    visible=[]
    for row in snapshot['prose']:
        if row.get('visible') is not True:
            continue
        require(row.get('exactText') is True and row.get('computed',{}).get('visible') is True,
                'visible prose lacks exact text or computed visibility')
        opacity=row['computed'].get('opacity')
        require(type(opacity) in (int,float) and math.isfinite(opacity) and opacity>0,'invalid visible prose opacity')
        for record,keys in ((row['rect'],('left','right','top','bottom','width','height')),
                            (row['clip'],('left','right','top','bottom')),
                            (row['intersection'],('width','height'))):
            require(all(type(record.get(key)) in (int,float) and math.isfinite(record[key]) for key in keys),
                    'invalid prose geometry')
        width=max(0,min(row['rect']['right'],row['clip']['right'])-max(row['rect']['left'],row['clip']['left']))
        height=max(0,min(row['rect']['bottom'],row['clip']['bottom'])-max(row['rect']['top'],row['clip']['top']))
        require(row['rect']['width']==row['rect']['right']-row['rect']['left']>0 and
                row['rect']['height']==row['rect']['bottom']-row['rect']['top']>0,'inconsistent prose rectangle')
        require(width==row['intersection']['width']>0 and height==row['intersection']['height']>0,
                'prose has no positive clipped visibility')
        visible.append(row)
    require(len(visible)==snapshot['visibleParagraphs']>0,'visible prose set differs')
