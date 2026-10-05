"""Small synthetic HTTP and RFC negotiation controls; never browser performance."""
import ast
import copy
import gzip
import hashlib
import http.client
from pathlib import Path
import unittest

import reader_loading_fixture as fixture
from reader_loading_transport import GZIP, IDENTITY, TEXT_MIMES, profile_identity, select_encoding, static_representation, validate_profile


class NegotiationTests(unittest.TestCase):
    def test_explicit_wildcard_identity_and_default_preferences(self):
        cases=[(None,'identity'),('','identity'),(' \t','identity'),('gzip','gzip'),('GZiP;Q=1.000','gzip'),
            ('gzip;q=0','identity'),('gzip;q=0, identity','identity'),('gzip;q=0, *;q=1','identity'),
            ('*','gzip'),('*;q=0',None),('*;q=0,identity;q=.5','INVALID'),
            ('*;q=0,identity;q=0.5','identity'),('*;q=0,gzip;q=1','gzip'),
            ('identity;q=0,gzip;q=0',None),('identity;q=0',None),('identity;q=0,*;q=0.5','gzip'),
            ('br,deflate','identity'),('br,identity;q=0',None),('gzip;q=0.5','gzip'),
            ('gzip;q=0.5,identity;q=0.9','identity'),('gzip;q=0.9,identity;q=0.1','gzip'),
            ('gzip;q=0.5,identity;q=0.5','gzip'),('x-gzip','gzip'),('gzip;q=0.001','gzip'),
            ('gzip;q=0.000','identity'),('gzip;q=1.,identity;q=0.','gzip'),(' ,gzip,,','gzip'),
            ('gzip;q=0.5,GZIP;q=0.5','gzip'),('identity, *;q=0.8','identity')]
        for header,expected in cases:
            with self.subTest(header=header):
                if expected=='INVALID':
                    with self.assertRaises(ValueError):select_encoding(header)
                else:self.assertEqual(select_encoding(header),expected)

    def test_malformed_and_ambiguous_input_cannot_select_gzip(self):
        cases=['gzip;q=-1','gzip;q=1.001','gzip;q=2','gzip;q=NaN','gzip;q=0.0001','gzip;q="1"',
            'gzip;q=1;q=0','gzip;level=6','gzip;q = 1','gzip gzip','gzip\r\nX: y',
            'gzip;q=1,GZIP;q=0','gzip;q=1,x-gzip;q=0','*;q=1,*;q=0','é','x'*2049,','.join(['br']*33),False]
        for header in cases:
            with self.subTest(header=header),self.assertRaises(ValueError):select_encoding(header)

    def test_profile_types_versions_and_fields_are_exact(self):
        for key,value in [('minimum_bytes',False),('gzip_mtime',False),('gzip_level',6.0),
                          ('zlib_runtime','unknown'),('extra','unknown')]:
            value_copy=profile_identity(GZIP);value_copy[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_profile(value_copy)
        self.assertEqual(validate_profile(profile_identity(GZIP)),profile_identity(GZIP))

    def test_deterministic_hash_verified_lossless_text_only(self):
        body=('合成 static content '+'.'*100).encode()*8
        a=static_representation(body,'text/css','gzip',GZIP);b=static_representation(body,'text/css','gzip',GZIP)
        self.assertEqual(a,b);self.assertEqual(gzip.decompress(a['body']),body)
        self.assertEqual(a['raw_sha256'],hashlib.sha256(body).hexdigest())
        self.assertEqual(a['representation_sha256'],hashlib.sha256(a['body']).hexdigest())
        self.assertNotEqual(a['raw_sha256'],a['representation_sha256'])
        self.assertEqual(a['body'][4:8],bytes(4));self.assertTrue(a['vary'])
        for media in ['application/json','image/png','font/woff2','application/octet-stream']:
            out=static_representation(body,media,'gzip',GZIP)
            self.assertEqual(out['body'],body);self.assertIsNone(out['encoding']);self.assertFalse(out['vary'])
        self.assertEqual(static_representation(body,'text/css','gzip',IDENTITY)['body'],body)


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files={'index.html':b'<html>synthetic</html>'*40,'assets/a.css':b'.synthetic{color:red}'*200,
                   'assets/a.js':b'const synthetic=1;'*100,'styles/fonts.css':b'.font{font:serif}'*30,'assets/a.png':b'synthetic-png'}
        cls.f=fixture.Fixture(cls.files,transport_profile=GZIP)
    @classmethod
    def tearDownClass(cls):cls.f.close()
    def request(self,path='/inbox/assets/a.css',encoding='gzip',method='GET',extra=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.f.server.server_port,timeout=3)
        conn.putrequest(method,path,skip_accept_encoding=True)
        if encoding is not None:
            for value in encoding if isinstance(encoding,list) else [encoding]:conn.putheader('Accept-Encoding',value)
        for key,value in (extra or {}).items():conn.putheader(key,value)
        conn.endheaders();response=conn.getresponse();body=response.read();out=(response.status,dict(response.getheaders()),body);conn.close();return out

    def test_gzip_and_identity_vary_length_cache_csp(self):
        for encoding in ['gzip','gzip;q=0, identity','',None]:
            status,headers,body=self.request(encoding=encoding)
            self.assertEqual(status,200);self.assertEqual(headers['Vary'],'Accept-Encoding')
            self.assertEqual(headers['Cache-Control'],'public, max-age=31536000, immutable')
            self.assertEqual(headers['Content-Security-Policy'],fixture.CSP)
            self.assertEqual(int(headers['Content-Length']),len(body))
            decoded=gzip.decompress(body) if encoding=='gzip' else body
            self.assertEqual(decoded,self.files['assets/a.css'])
            self.assertEqual(headers.get('Content-Encoding'),'gzip' if encoding=='gzip' else None)
            self.assertNotIn('ETag',headers)  # No new static validator semantics.
            record=self.f.records[-1];self.assertEqual(record['transport']['accept_encoding'],encoding)
            self.assertEqual(record['transport']['representation_sha256'],hashlib.sha256(body).hexdigest())

    def test_document_and_nonimmutable_text_keep_original_no_store(self):
        for path,key in [('/inbox/all','index.html'),('/inbox/styles/fonts.css','styles/fonts.css')]:
            status,headers,body=self.request(path);self.assertEqual(status,200)
            self.assertEqual(headers['Cache-Control'],'no-store');self.assertEqual(gzip.decompress(body),self.files[key])

    def test_head_uses_same_representation_headers_with_no_body(self):
        for encoding in ('gzip','gzip;q=0,identity'):
            get=self.request(encoding=encoding);head=self.request(encoding=encoding,method='HEAD')
            self.assertEqual(head[0],200);self.assertEqual(head[2],b'')
            for key in ('Content-Encoding','Content-Length','Vary','Content-Type','Cache-Control','Content-Security-Policy'):
                self.assertEqual(get[1].get(key),head[1].get(key))

    def test_multiple_header_lines_and_no_acceptable_representation(self):
        status,headers,body=self.request(encoding=['gzip;q=0.5','identity;q=0.9'])
        self.assertEqual(status,200);self.assertNotIn('Content-Encoding',headers)
        self.assertEqual(body,self.files['assets/a.css'])
        for encoding,status in [('gzip;q=0,identity;q=0',406),('gzip;q=NaN',400),(['gzip;q=1','gzip;q=0'],400)]:
            with self.subTest(encoding=encoding):
                response=self.request(encoding=encoding);self.assertEqual(response[0],status)
                self.assertEqual(response[1]['Cache-Control'],'no-store');self.assertEqual(response[1]['Vary'],'Accept-Encoding')
                self.assertNotIn('Content-Encoding',response[1]);self.assertEqual(len(response[2]),int(response[1]['Content-Length']))
                head=self.request(encoding=encoding,method='HEAD');self.assertEqual(head[0],status);self.assertEqual(head[2],b'')

    def test_api_png_revalidation_and_unknown_guards_stay_unchanged(self):
        status,headers,body=self.request('/mf/v1/version',extra={'X-Auth-Token':fixture.TOKEN})
        self.assertEqual(status,200);self.assertEqual(headers['Cache-Control'],'no-store');self.assertNotIn('Content-Encoding',headers)
        original=self.request('/fixture-images/1.png');self.assertEqual(original[0],200)
        self.assertNotIn('Content-Encoding',original[1]);self.assertNotIn('Vary',original[1])
        cached=self.request('/fixture-images/1.png',extra={'If-None-Match':original[1]['ETag']})
        self.assertEqual(cached[0],304);self.assertEqual(cached[2],b'');self.assertEqual(cached[1]['ETag'],original[1]['ETag'])
        self.assertEqual(self.request('/mf/v1/unknown',extra={'X-Auth-Token':fixture.TOKEN})[0],501)
        self.assertEqual(self.request(method='POST')[0],405)
        self.assertEqual(self.request('/inbox/assets/unknown.js')[0],404)
        self.assertEqual(self.request('/mf/v1/version',extra={'X-Auth-Token':'wrong'})[0],401)


class ComparisonTests(unittest.TestCase):
    def test_missing_mixed_tampered_or_pair_mismatched_profiles_refused(self):
        from compare_reader_loading import compare
        from test_reader_loading_performance import SamplingContract
        factory=SamplingContract();left=factory.row('baseline');right=factory.row('candidate')
        self.assertEqual(compare(left,right)['transport_profile'],profile_identity(IDENTITY))
        changes=[lambda d:d.pop('transport_profile'),lambda d:d.update(transport_profile=profile_identity(GZIP)),
                 lambda d:d['transport_profile'].update(gzip_level=9),lambda d:d['pairs'][0].pop('transport_profile')]
        for change in changes:
            candidate=copy.deepcopy(right);change(candidate)
            with self.subTest(change=change),self.assertRaises(ValueError):compare(left,candidate)

    def test_cdp_encoding_headers_and_fixed_ci_profile_are_recorded(self):
        source=Path(__file__).with_name('reader_loading_performance.py').read_text()
        self.assertIn("'content-encoding','vary'",source)
        ci=Path(__file__).with_name('reader_loading_ci.py').read_text();self.assertIn("'--transport-profile', GZIP",ci)
        tree=ast.parse(source)
        warm=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='warm_image_proof')
        self.assertNotIn('gzip',ast.unparse(warm));self.assertNotIn('transport',ast.unparse(warm))


if __name__=='__main__':unittest.main()
