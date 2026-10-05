"""Synthetic negative controls; no browser launch or claim of real capture coverage."""
import ast
import base64
import copy
import hashlib
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

SOURCE=Path(__file__).with_name('native_zoom_browser.py')
TREE=ast.parse(SOURCE.read_text())
NAMES={'surface_png','surface_state','surface_dimensions','capture_surface','marker_pixel_proof','prove_surface_corners'}
NS={name:globals()[name] for name in ('base64','hashlib','json','math','struct','zlib')}
exec(compile(ast.Module(body=[n for n in TREE.body if isinstance(n,ast.FunctionDef) and n.name in NAMES],type_ignores=[]),str(SOURCE),'exec'),NS)


def chunk(kind,data):
    return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)


def png_bytes(width,height,rows=None,color=2,depth=8,filter_mode=0):
    channels=3 if color==2 else 4
    rows=rows or [bytes([255])*(width*channels) for _ in range(height)]
    raw=bytearray();previous=bytes(width*channels)
    for row in rows:
        raw.append(filter_mode)
        for x,value in enumerate(row):
            a=row[x-channels] if x>=channels else 0;b=previous[x];c=previous[x-channels] if x>=channels else 0
            if filter_mode==1:predict=a
            elif filter_mode==2:predict=b
            elif filter_mode==3:predict=(a+b)//2
            elif filter_mode==4:
                p=a+b-c;dist=(abs(p-a),abs(p-b),abs(p-c));predict=(a,b,c)[dist.index(min(dist))]
            else:predict=0
            raw.append((value-predict)&255)
        previous=row
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,depth,color,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')


def state(width=100,height=80,dpr=2):
    return {'target_id':'fixture-page','dom':{'url':'http://127.0.0.1:31415/inbox/all/entry/101',
      'dpr':dpr,'inner':[width,height],'outer':[width*dpr,height*dpr+88],'client':[width,height],
      'scroll':[0,0],'focus':[1,3],'heading':{'x':12,'y':24},
      'visual':{'width':width,'height':height,'offsetLeft':0,'offsetTop':0,'pageLeft':0,'pageTop':0,'scale':1}},
      'layout':{'cssVisualViewport':{'clientWidth':width,'clientHeight':height},'cssLayoutViewport':{'clientWidth':width,'clientHeight':height}}}


def marker_fixture():
    s=state();rows=[bytearray([255]*600) for _ in range(160)];markers=[]
    for corner,color,left,top in [('tl',[239,17,173],4,4),('tr',[19,223,131],84,4),('bl',[37,83,241],4,64),('br',[251,163,29],84,64)]:
        markers.append({'corner':corner,'rgb':color,'rect':{'left':left,'top':top,'right':left+12,'bottom':top+12}})
        for y in range(top*2,(top+12)*2):
            for x in range(left*2,(left+12)*2):rows[y][x*3:x*3+3]=bytes(color)
    return s,markers,NS['surface_png'](png_bytes(200,160,rows))


class SurfaceGeometry(unittest.TestCase):
    def test_full_surface_and_explicit_rounding(self):
        s=state();self.assertEqual(NS['surface_dimensions']({'width':200,'height':160},s)['expected_pixels'],[200,160])
        NS['surface_dimensions']({'width':201,'height':159},s)

    def test_original_native_zoom_crop_dimensions_are_rejected(self):
        # The archived F PNG really is 720x436 while its observed CSS width is
        # 720 and DPR=2. Width alone disproves full-surface coverage.
        with self.assertRaisesRegex(AssertionError,'clipped/rescaled'):
            NS['surface_dimensions']({'width':720,'height':436},state(720,436,2))

    def test_extra_clip_rescale_wrong_dpr_or_height_rejected(self):
        for width,height in [(100,80),(200,80),(100,160),(204,160),(200,164)]:
            with self.subTest(width=width,height=height),self.assertRaises(AssertionError):
                NS['surface_dimensions']({'width':width,'height':height},state())

    def test_scrollbar_uses_independent_inner_size(self):
        s=state();s['dom']['client'][0]=85;s['dom']['visual']['width']=85
        for v in s['layout'].values():v['clientWidth']=85
        result=NS['surface_dimensions']({'width':200,'height':160},s)
        self.assertEqual(result['scrollbar_css'],[15,0])
        with self.assertRaises(AssertionError):NS['surface_dimensions']({'width':170,'height':160},s)

    def test_nonfinite_boolean_and_emulated_visual_zoom_fail(self):
        for value in [float('nan'),float('inf'),True,0,5]:
            s=state();s['dom']['dpr']=value
            with self.subTest(value=value),self.assertRaises(AssertionError):NS['surface_dimensions']({'width':200,'height':160},s)
        for field,value in [('scale',2),('offsetLeft',1),('offsetTop',1)]:
            s=state();s['dom']['visual'][field]=value
            with self.subTest(field=field),self.assertRaises(AssertionError):NS['surface_dimensions']({'width':200,'height':160},s)

    def test_missing_or_inconsistent_cdp_metrics_fail(self):
        for viewport in ['cssVisualViewport','cssLayoutViewport']:
            s=state();s['layout'][viewport]['clientWidth']=50
            with self.subTest(viewport=viewport),self.assertRaises(AssertionError):NS['surface_dimensions']({'width':200,'height':160},s)


class PngAndMarkers(unittest.TestCase):
    def test_rgb_rgba_all_filters_decode_real_pixels(self):
        for color,channels in [(2,3),(6,4)]:
            rows=[bytes((i*17+y*11)%256 for i in range(7*channels)) for y in range(6)]
            for mode in range(5):
                with self.subTest(color=color,mode=mode):
                    got=NS['surface_png'](png_bytes(7,6,rows,color=color,filter_mode=mode))
                    self.assertEqual(got['rows'],rows)

    def test_corrupt_truncated_trailing_and_budget_fail(self):
        good=png_bytes(2,2)
        cases=[b'not PNG',good[:-1],good+b'junk',good[:40]+bytes([good[40]^1])+good[41:]]
        cases.append(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',8192,8192,8,2,0,0,0))+chunk(b'IEND',b''))
        for data in cases:
            with self.subTest(size=len(data)),self.assertRaises(AssertionError):NS['surface_png'](data)

    def test_decompressed_excess_and_unsupported_encoding_fail(self):
        bomb=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00'*10000))+chunk(b'IEND',b'')
        with self.assertRaises(AssertionError):NS['surface_png'](bomb)
        with self.assertRaises(AssertionError):NS['surface_png'](png_bytes(2,2,depth=16))

    def test_all_four_rendered_rectangles_pass(self):
        s,markers,png=marker_fixture();proof=NS['marker_pixel_proof'](png,s,markers)
        self.assertEqual(len(proof),4);self.assertTrue(all(p['pixel_count']==576 for p in proof))

    def test_expected_coordinates_without_actual_pixels_fail(self):
        s,markers,_=marker_fixture()
        with self.assertRaisesRegex(AssertionError,'missing rendered corner'):
            NS['marker_pixel_proof'](NS['surface_png'](png_bytes(200,160)),s,markers)

    def test_partial_occluded_relocated_and_wrong_corner_fail(self):
        s,markers,png=marker_fixture()
        broken=copy.deepcopy(png);row=bytearray(broken['rows'][10]);row[30:33]=b'\xff'*3;broken['rows'][10]=bytes(row)
        with self.assertRaisesRegex(AssertionError,'occluded'):NS['marker_pixel_proof'](broken,s,markers)
        wrong=copy.deepcopy(markers);wrong[3]['rect']['left']-=2
        with self.assertRaisesRegex(AssertionError,'coordinates'):NS['marker_pixel_proof'](png,s,wrong)
        with self.assertRaisesRegex(AssertionError,'four distinct'):NS['marker_pixel_proof'](png,s,markers[:3])
        wrong=copy.deepcopy(markers);wrong[0]['corner']='tr';wrong[1]['corner']='tl'
        with self.assertRaisesRegex(AssertionError,'horizontal edge'):NS['marker_pixel_proof'](png,s,wrong)


class FakePage:
    def __init__(self,s):self.s=copy.deepcopy(s);self.url=s['dom']['url'];self.caret=False;self.probe=False;self.probe_failure=False
    def evaluate(self,code):
        if 'const v=visualViewport' in code:return copy.deepcopy(self.s['dom'])
        if 'const s=document.createElement' in code:self.caret=True;return
        if "native-capture-caret')?.remove" in code:self.caret=False;return
        if "document.querySelectorAll('#native-surface-probe').length"==code:return int(self.probe)
        if 'const root=document.createElement' in code:self.probe=True;return marker_fixture()[1]
        if 'requestAnimationFrame' in code:return
        if 'globalThis.__nativeSurfaceOwnedProbe?.remove()' in code:self.probe=False;return
        if 'document.querySelectorAll' in code:return int(self.probe)+int(self.caret)
        raise AssertionError('unexpected fake page operation: '+code)


class FakeSession:
    def __init__(self,s,page):self.s=copy.deepcopy(s);self.page=page;self.calls=[];self.target={'targetId':s['target_id'],'type':'page','url':page.url};self.fail=False;self.on_capture=None
    def send(self,method,args=None):
        self.calls.append((method,args))
        if method=='Target.getTargetInfo':return {'targetInfo':copy.deepcopy(self.target)}
        if method=='Page.getLayoutMetrics':return copy.deepcopy(self.s['layout'])
        if method=='Page.captureScreenshot':
            if self.fail:raise RuntimeError('synthetic capture failure')
            if self.on_capture:self.on_capture()
            return {'data':base64.b64encode(png_bytes(200,160)).decode()}
        raise AssertionError('unexpected CDP call')


class CaptureIdentity(unittest.TestCase):
    def setUp(self):
        self.s=state();self.p=FakePage(self.s);self.session=FakeSession(self.s,self.p)
        # Retained, private synthetic outputs; deliberately no TemporaryDirectory cleanup.
        self.out=Path(tempfile.mkdtemp(prefix='native-surface-contract-'))

    def capture(self):return NS['capture_surface'](self.p,self.session,self.p.url,self.s['target_id'],self.out/'body.png')

    def test_exact_target_unclipped_capture_and_raw_bytes(self):
        data,_,_=self.capture();self.assertEqual((self.out/'body.png').read_bytes(),data)
        self.assertEqual([args for method,args in self.session.calls if method=='Page.captureScreenshot'],[{'format':'png','fromSurface':True,'captureBeyondViewport':False}])
        self.assertFalse(self.p.caret)

    def test_other_tab_type_identity_or_url_refused_before_capture(self):
        for field,value in [('targetId','other-tab'),('type','browser'),('url','https://example.test/unrelated')]:
            self.session.target[field]=value
            with self.subTest(field=field),self.assertRaises(AssertionError):self.capture()
            self.assertFalse(any(m=='Page.captureScreenshot' for m,_ in self.session.calls))
            self.session.target={'targetId':self.s['target_id'],'type':'page','url':self.p.url}

    def test_navigation_during_capture_rejected_and_raw_failure_retained(self):
        self.session.on_capture=lambda:self.session.target.update(targetId='another-tab')
        with self.assertRaisesRegex(AssertionError,'target changed'):self.capture()
        self.assertTrue((self.out/'body.png').is_file());self.assertFalse(self.p.caret)
        self.assertFalse(json.loads((self.out/'body.surface.json').read_text())['passed'])

    def test_focus_scroll_or_heading_change_rejected(self):
        for field,value in [('focus',[0]),('scroll',[0,10]),('heading',{'x':10,'y':20})]:
            p=FakePage(self.s);session=FakeSession(self.s,p);session.on_capture=lambda f=field,v=value:p.s['dom'].__setitem__(f,v)
            with self.subTest(field=field),self.assertRaisesRegex(AssertionError,'capture changed'):
                NS['capture_surface'](p,session,p.url,self.s['target_id'],self.out/(field+'.png'))

    def test_capture_failure_restores_caret_and_keeps_failure_record(self):
        self.session.fail=True
        with self.assertRaisesRegex(RuntimeError,'synthetic capture failure'):self.capture()
        self.assertFalse(self.p.caret);self.assertTrue((self.out/'body.surface.json').is_file())

    def test_probe_left_in_body_is_rejected_before_capture(self):
        self.p.probe=True
        with self.assertRaisesRegex(AssertionError,'unexpected probe'):self.capture()
        self.assertFalse(any(m=='Page.captureScreenshot' for m,_ in self.session.calls))

    def test_probe_removed_during_its_capture_is_rejected(self):
        self.p.probe=True;self.session.on_capture=lambda:setattr(self.p,'probe',False)
        with self.assertRaisesRegex(AssertionError,'probe changed'):
            NS['capture_surface'](self.p,self.session,self.p.url,self.s['target_id'],self.out/'probe.png',probe=True)

    def test_probe_failure_restores_marker_focus_and_viewport(self):
        self.session.fail=True
        with self.assertRaisesRegex(RuntimeError,'synthetic capture failure'):
            NS['prove_surface_corners'](self.p,self.session,self.p.url,self.s['target_id'],self.out/'probe.png')
        self.assertFalse(self.p.probe);self.assertFalse(self.p.caret)
        record=json.loads((self.out/'probe.proof.json').read_text());self.assertTrue(record['restored']);self.assertFalse(record['passed'])


if __name__=='__main__':unittest.main()
