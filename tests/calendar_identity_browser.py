"""Invalid/failed/late account timezone recovery through real reader controls; mock APIs only."""
from datetime import datetime
from urllib.parse import parse_qs,urlsplit
from playwright.sync_api import expect
from review_reader_harness import Harness

for width in (1440,390):
    h=Harness('calendar-identity-'+str(width),timezone_id='America/Los_Angeles')
    h.settings['minimum_score']=8
    page=h.page;page.set_viewport_size({'width':width,'height':900})
    page.clock.set_fixed_time(datetime.fromisoformat('2026-10-02T01:37:00+00:00'))
    page.add_init_script("localStorage.setItem('settings',JSON.stringify({showStatus:'unread',markReadOnScroll:false,articleListLayout:'list'}));localStorage.setItem('ai-view-state',JSON.stringify({mode:'recommended',minimum:8,auxiliary:'none',sort:'score',direction:'desc'}))")
    held,queries=[],[]
    h.feeds[0].update(icon={'feed_id':7,'icon_id':0},checked_at='2026-10-01T08:00:00Z',parsing_error_count=0,parsing_error_message='',disabled=False,hide_globally=False)
    records=[(1,'2026-10-01T17:00:00Z'),(2,'2026-10-02T01:00:00Z')]
    def transport(route,path,method):
        if path.endswith('/me'):
            held.append(route);return True
        if path.endswith('/entries') and method=='GET':
            q=parse_qs(urlsplit(route.request.url).query);queries.append(q)
            after=int(q.get('published_after',['0'])[0]);ai='ai_view' in q
            selected=[(eid,pub) for eid,pub in records if datetime.fromisoformat(pub.replace('Z','+00:00')).timestamp()>after or ai and datetime.fromisoformat(pub.replace('Z','+00:00')).timestamp()==after]
            entries=[{'id':eid,'user_id':1,'feed_id':7,'feed':h.feeds[0],'title':f'Identity fixture {eid}','url':f'https://example.test/{eid}','hash':str(eid),'published_at':pub,'changed_at':pub,'created_at':pub,'status':'unread','starred':False,'content':'<p>synthetic</p>','enclosures':[],'reading_time':1,'ai':{'status':'done','score':9,'summary_zh':'fixture'}} for eid,pub in selected]
            route.fulfill(json={'total':len(entries),'entries':entries[:int(q.get('limit',['24'])[0])]});return True
        return False
    h.custom=transport
    try:
        h.goto();page.wait_for_timeout(50)
        held.pop().fulfill(json={'id':1,'username':'fixture','timezone':'Invalid/Zone','is_admin':True})
        notice=page.locator('.home-identity-notice')
        expect(notice).to_be_visible()
        h.check('invalid timezone offers retry and sends no date request',not any('published_after' in q for q in queries))
        retry=notice.get_by_role('button',name='重试',exact=True)
        retry.click();page.wait_for_timeout(50)
        held.pop().fulfill(status=503,json={'error_message':'synthetic identity failure'})
        expect(notice).to_be_visible();expect(retry).to_be_enabled()
        h.check('failed identity stays gated',not any('published_after' in q for q in queries))
        retry.click();page.wait_for_timeout(50)
        expect(page.locator('.entry-list [data-entry-id]')).to_have_count(0)
        h.check('pending retry stays gated',not any('published_after' in q for q in queries) and len(held)==1)
        held.pop().fulfill(json={'id':1,'username':'fixture','timezone':'UTC','is_admin':True})
        expect(page.locator('.entry-list [data-entry-id]')).to_have_count(1)
        expect(page.locator('.entry-list [data-entry-id]').first).to_have_attribute('data-entry-id','2')
        expect(notice).to_have_count(0)
        h.check('valid UTC recovers IDs and cutoff',any(q.get('published_after')==['1790899200'] and q.get('ai_min')==['8'] and q.get('status')==['unread'] for q in queries))
        h.check('valid UTC explained', 'UTC' in page.locator('.reading-timezone:visible').inner_text())
        h.check('identity recovery no writes',not h.writes)
        page.screenshot(path=str(h.out/'recovered.png'),full_page=True)
    finally:
        h.close()
