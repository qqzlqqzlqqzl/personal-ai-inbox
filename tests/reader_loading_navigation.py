"""Passive navigation evidence. CDP event time and Python receipt time stay distinct.

Finished-response encoded bytes are attributed by completion time; they are not
bytes transferred during that phase. No command is sent between goto returning
and the unchanged first-24 assertion. CDP Page lifecycle events bind DCL to the
main document's exact frame and loader, not an unrelated document or iframe.
"""
import math
import time


def number(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0:
        raise ValueError('invalid navigation measurement number')
    return value


def summarize_navigation(record):
    if record['overflow']:raise ValueError('navigation observation budget exceeded')
    events=record['events'];marks=record['marks']
    docs=[e for e in events if e['kind']=='request' and e['type']=='Document' and e['url']==record['url']]
    if len(docs)!=1:raise ValueError('one exact main navigation request required')
    doc=docs[0];frame=doc['frame_id'];loader=doc['loader_id']
    if not frame or not loader:raise ValueError('missing navigation frame or loader')
    roots=[e for e in events if e['kind']=='frame' and e['frame_id']==frame and e['loader_id']==loader and e['url']==record['url'] and not e['parent_id']]
    if len(roots)!=1:raise ValueError('exact root document identity required')
    dcls=[e for e in events if e['kind']=='dcl' and e['frame_id']==frame and e['loader_id']==loader]
    if len(dcls)!=1:raise ValueError('one exact document DCL required')
    start=number(doc['timestamp']);dcl=number(dcls[0]['timestamp'])
    if dcl<start:raise ValueError('DCL precedes navigation')
    requests=[e for e in events if e['kind']=='request' and e['frame_id']==frame and e['loader_id']==loader]
    ids=[e['request_id'] for e in requests]
    if len(ids)!=len(set(ids)):raise ValueError('ambiguous navigation request identity')
    requests={e['request_id']:e for e in requests}
    terminals=[e for e in events if e['kind'] in ('finished','failed') and e['request_id'] in requests]
    terminal_ids=[e['request_id'] for e in terminals]
    if len(terminal_ids)!=len(set(terminal_ids)):raise ValueError('duplicate navigation terminal event')
    terminals={e['request_id']:e for e in terminals}
    for rid,q in requests.items():
        qt=number(q['timestamp']);number(q['received_at'])
        if qt<start:raise ValueError('request precedes navigation document')
        terminal=terminals.get(rid)
        if terminal:
            if number(terminal['timestamp'])<qt:raise ValueError('completion precedes request')
            number(terminal['received_at'])
            if terminal['kind']=='finished':number(terminal['encoded_bytes'])
    def completed_snapshot(clock,cutoff):
        # Python snapshots describe callbacks received by the local boundary.
        # CDP snapshots describe occurrence time within the bound target.
        key='timestamp' if clock=='cdp' else 'received_at'
        active={rid:q for rid,q in requests.items() if q[key]<=cutoff}
        done={rid:t for rid,t in terminals.items() if rid in active and t[key]<=cutoff}
        complete=[t for t in done.values() if t['kind']=='finished']
        return {'clock':clock,'cutoff':cutoff,'requested_ids':sorted(active),
                'finished_ids':sorted(t['request_id'] for t in complete),
                'finished_response_encoded_bytes':sum(t['encoded_bytes'] for t in complete),
                'failed_ids':sorted(rid for rid,t in done.items() if t['kind']=='failed'),
                'in_flight_ids':sorted(active.keys()-done.keys())}
    required=['goto_start','goto_end','assert_start','assert_end']
    if any(k not in marks for k in required):raise ValueError('incomplete original assertion boundaries')
    values=[number(marks[k]) for k in required]
    if values!=sorted(values):raise ValueError('navigation boundaries out of order')
    pre=completed_snapshot('cdp',dcl)
    post=[t for t in terminals.values() if t['kind']=='finished' and t['timestamp']>dcl]
    return {'main_frame_id':frame,'loader_id':loader,'document_request_id':doc['request_id'],
            'navigation_request_cdp_seconds':start,'domcontentloaded_cdp_seconds':dcl,
            'request_to_dcl_ms':(dcl-start)*1000,
            'goto_wait_python_ms':(marks['goto_end']-marks['goto_start'])*1000,
            'goto_return_to_assert_start_python_ms':(marks['assert_start']-marks['goto_end'])*1000,
            'first24_assertion_python_ms':(marks['assert_end']-marks['assert_start'])*1000,
            'navigation_through_assertion_python_ms':(marks['assert_end']-marks['goto_start'])*1000,
            'at_actual_dcl':pre,
            'responses_finishing_after_dcl_encoded_bytes':sum(t['encoded_bytes'] for t in post),
            'at_python_assert_start':completed_snapshot('python_callback_receipt',marks['assert_start']),
            'at_python_assert_end':completed_snapshot('python_callback_receipt',marks['assert_end']),
            'byte_semantics':'whole finished response encoded bytes grouped by finish event; not phase transfer bytes; in-flight bytes unknown; no server Content-Length substitution'}


class NavigationObservation:
    def __init__(self,session,url):
        self.active=True
        self.record={'url':url,'status':'INCOMPLETE','marks':{},'events':[],'overflow':False,
                     'clocks':'CDP monotonic occurrence vs Python monotonic callback receipt; no cross-clock subtraction'}
        def receive(kind,fields):
            if not self.active:return
            if len(self.record['events'])>=4096:
                self.record['overflow']=True;return
            self.record['events'].append({'kind':kind,'received_at':time.monotonic(),**fields})
        session.on('Network.requestWillBeSent',lambda e:receive('request',{
            'request_id':e['requestId'],'frame_id':e.get('frameId'),'loader_id':e.get('loaderId'),
            'timestamp':e['timestamp'],'type':e.get('type'),'url':e['request']['url']}))
        session.on('Network.loadingFinished',lambda e:receive('finished',{
            'request_id':e['requestId'],'timestamp':e['timestamp'],'encoded_bytes':e['encodedDataLength']}))
        session.on('Network.loadingFailed',lambda e:receive('failed',{
            'request_id':e['requestId'],'timestamp':e['timestamp']}))
        session.on('Page.frameNavigated',lambda e:receive('frame',{
            'frame_id':e['frame']['id'],'loader_id':e['frame'].get('loaderId'),
            'parent_id':e['frame'].get('parentId'),'url':e['frame'].get('url')}))
        session.on('Page.lifecycleEvent',lambda e:receive('dcl',{
            'frame_id':e['frameId'],'loader_id':e['loaderId'],'timestamp':e['timestamp']})
            if e.get('name')=='DOMContentLoaded' else None)
        session.send('Page.enable')
        session.send('Page.setLifecycleEventsEnabled',{'enabled':True})

    def mark(self,name):
        value=time.monotonic();self.record['marks'][name]=value;return value

    def finish(self):
        self.active=False
        try:
            self.record['summary']=summarize_navigation(self.record)
            self.record['status']='COMPLETE'
        except Exception as error:
            self.record['diagnostic_error']=type(error).__name__+': '+str(error)
        # A diagnostic failure never replaces the original goto/assertion error.
