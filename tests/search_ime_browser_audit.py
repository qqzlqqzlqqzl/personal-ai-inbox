"""Real Chromium regression candidate. Synthetic API only; no live data.

Run from the isolated repo with AI_NEWS_TEST_BUILD=runtime/browser-build.
Synthetic key events are not a claim of physical IME hardware coverage.
"""
import json
import re
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import expect
from review_reader_harness import Harness

failures=[]
for name,width,zoom in [('desktop',1440,1),('mobile',390,1),('desktop-css-zoom-200',1440,2)]:
 h=Harness('search-ime-'+name);p=h.page
 p.set_viewport_size({'width':width,'height':900 if width>390 else 844})
 requests=[];p.on('request',lambda req:requests.append(req.url))
 p.add_init_script("window.auditFocus=[];document.addEventListener('focusin',e=>window.auditFocus.push({tag:e.target.tagName,label:e.target.getAttribute('aria-label'),text:e.target.textContent?.slice(0,60),at:performance.now()}))")
 def intercept(route,path,method):
  if path.endswith('/entries'):
   q=parse_qs(urlsplit(route.request.url).query)
   route.fulfill(json={'total':3 if q.get('ai_view')==['recommended'] else 9,'entries':[]})
   return True
  return False
 h.custom=intercept
 def record(key,value):
  h.checks[key]=bool(value)
  print(('PASS ' if value else 'FAIL ')+name+': '+key,flush=True)
  if not value:failures.append(name+': '+key)
 try:
  h.goto();expect(p.locator('.page-info')).to_contain_text('(3)')
  if zoom==2:p.evaluate("document.documentElement.style.zoom='2'")
  record('Today AI route and total',p.url.endswith('/today') and 'AI精选' in p.locator('.page-info').inner_text())
  p.get_by_role('button',name='全部原始',exact=True).click();expect(p.locator('.page-info')).to_contain_text('(9)')
  p.get_by_role('button',name='AI 精选',exact=True).click();expect(p.locator('.page-info')).to_contain_text('(3)')
  record('lens switches preserve Today',p.url.endswith('/today'))
  # A native interaction control proves the fixture can submit normally.
  for mode,event in [
   ('composing',{'key':'Enter','code':'Enter','isComposing':True,'keyCode':13}),
   ('legacy229',{'key':'Enter','code':'Enter','isComposing':False,'keyCode':229}),
  ]:
   trigger=p.locator('.search-and-sort-bar .button-group button[aria-haspopup="dialog"]').first
   trigger.click();field=p.locator('.search-modal input');expect(field).to_be_visible();field.fill('输入法候选 '+mode)
   before=len(requests);field.dispatch_event('keydown',event);p.wait_for_timeout(400)
   still_open=field.is_visible()
   record(mode+' Enter keeps search open',still_open)
   record(mode+' Enter does not issue query',not any('search=' in url for url in requests[before:]))
   p.screenshot(path=str(h.out/(mode+'.png')),full_page=True)
   if still_open:
    field.press('Escape');expect(field).to_be_hidden();expect(trigger).to_be_focused()
    record(mode+' Cancel restores focus',True)
  trigger=p.locator('.search-and-sort-bar .button-group button[aria-haspopup="dialog"]').first
  trigger.click();field=p.locator('.search-modal input');field.fill('普通输入');before=len(requests);field.press('a');expect(field).to_have_value('普通输入a')
  record('ordinary letter keeps search open',field.is_visible())
  record('ordinary letter does not issue query',not any('search=' in url for url in requests[before:]))
  field.fill('已确认搜索')
  with p.expect_request(lambda request: parse_qs(urlsplit(request.url).query).get('search')==['已确认搜索']):
   field.press('Enter')
  expect(field).to_be_hidden()
  expect(trigger).to_have_attribute('aria-label',re.compile('已确认搜索'))
  record('ordinary Enter submits',True)
  trigger.click();field=p.locator('.search-modal input');field.fill('取消草稿');before=len(requests);p.locator('.search-modal').get_by_role('button',name='取消',exact=True).click();expect(field).to_be_hidden();expect(trigger).to_be_focused()
  record('Cancel leaves committed filter',not any('search=' in url for url in requests[before:]))
  record('no horizontal overflow',p.evaluate('document.documentElement.scrollWidth<=innerWidth'))
  record('zero API writes',len(h.writes)==0)
  (h.out/'requests.json').write_text(json.dumps(requests,ensure_ascii=False,indent=2))
 except Exception as exc:
  h.errors.append(str(exc));failures.append(name+': '+str(exc));p.screenshot(path=str(h.out/'failure.png'),full_page=True)
 finally:
  (h.out/'focus-diagnostics.json').write_text(json.dumps(p.evaluate('({active:document.activeElement?.outerHTML,events:window.auditFocus})'),ensure_ascii=False,indent=2))
  try:h.close()
  except AssertionError as exc:
   failures.append(name+': harness result '+str(exc))

if failures:raise AssertionError(failures)
