"""Versioned synthetic transport, not a claim of identical production wire bytes.

RFC 9110 12.5.3: explicit coding overrides wildcard; identity is acceptable
unless excluded. An implicit identity preference is the fallback when no
supported coding was requested. Explicit identity weights compete with gzip.
Absent header chooses identity (server policy); empty header forbids coding.
Conflicting duplicates and malformed bounded input fail closed with HTTP 400.
"""
import gzip
import hashlib
import json
import re
import sys
import zlib

IDENTITY='identity-v1'
GZIP='gzip6-static-text-v1'
PROFILES=(IDENTITY,GZIP)
TEXT_MIMES=('text/html','text/css','text/javascript','application/javascript')
TOKEN=re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
QUALITY=re.compile(r'(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)')


def profile_identity(name):
    if name not in PROFILES:raise ValueError('unknown transport profile')
    return {'name':name,'text_mimes':list(TEXT_MIMES),'minimum_bytes':0,
            'gzip_level':6 if name==GZIP else None,'gzip_mtime':0 if name==GZIP else None,
            'python':'.'.join(map(str,sys.version_info[:3])),
            'zlib_build':zlib.ZLIB_VERSION,'zlib_runtime':zlib.ZLIB_RUNTIME_VERSION,
            'implicit_identity':'fallback; explicit weights compete; absent chooses identity',
            'production_wire_equivalence':False}


def validate_profile(value):
    if type(value) is not dict:raise ValueError('missing transport profile')
    expected=profile_identity(value.get('name'))
    if json.dumps(value,sort_keys=True,allow_nan=False)!=json.dumps(expected,sort_keys=True,allow_nan=False):
        raise ValueError('transport profile implementation mismatch')
    return value


def select_encoding(header):
    """Return identity/gzip/None (406); raise ValueError for malformed syntax."""
    if header is None:return 'identity'
    if not isinstance(header,str) or len(header)>2048 or any(ord(c)<32 and c!='\t' or ord(c)>126 for c in header):
        raise ValueError('invalid Accept-Encoding')
    if header.strip(' \t')=='':return 'identity'
    preferences={};members=header.split(',')
    if len(members)>32:raise ValueError('too many Accept-Encoding elements')
    for member in members:
        member=member.strip(' \t')
        if not member:continue  # RFC list syntax allows reasonable empty members.
        parts=[p.strip(' \t') for p in member.split(';')]
        coding=parts[0].lower()
        if not TOKEN.fullmatch(coding) or len(parts)>2:raise ValueError('invalid Accept-Encoding member')
        q=1000
        if len(parts)==2:
            if not parts[1].lower().startswith('q=') or not QUALITY.fullmatch(parts[1][2:]):
                raise ValueError('invalid Accept-Encoding quality')
            q=int(float(parts[1][2:])*1000+0.5)
        if coding=='x-gzip':coding='gzip'
        if coding in preferences and preferences[coding]!=q:raise ValueError('conflicting Accept-Encoding duplicate')
        preferences[coding]=q
    if not preferences:return 'identity'
    gzip_q=preferences.get('gzip',preferences.get('*',0))
    identity_q=preferences.get('identity',0 if preferences.get('*')==0 else None)
    if gzip_q>0 and (identity_q is None or gzip_q>=identity_q):return 'gzip'
    if identity_q is None or identity_q>0:return 'identity'
    return None


def static_representation(body,media,header,profile):
    """Only successful admitted static text passes here; APIs/images are outside."""
    profile_identity(profile)
    if profile==IDENTITY or media.split(';',1)[0].lower() not in TEXT_MIMES:
        return {'status':200,'body':body,'encoding':None,'vary':False,
                'raw_bytes':len(body),'raw_sha256':hashlib.sha256(body).hexdigest(),
                'representation_sha256':hashlib.sha256(body).hexdigest()}
    try:encoding=select_encoding(header)
    except ValueError:
        return {'status':400,'body':b'invalid Accept-Encoding','encoding':None,'vary':True,
                'raw_bytes':None,'raw_sha256':None,'representation_sha256':None}
    if encoding is None:
        return {'status':406,'body':b'no acceptable representation','encoding':None,'vary':True,
                'raw_bytes':None,'raw_sha256':None,'representation_sha256':None}
    encoded=gzip.compress(body,compresslevel=6,mtime=0) if encoding=='gzip' else body
    if encoding=='gzip' and gzip.decompress(encoded)!=body:raise ValueError('gzip roundtrip mismatch')
    return {'status':200,'body':encoded,'encoding':'gzip' if encoding=='gzip' else None,'vary':True,
            'raw_bytes':len(body),'raw_sha256':hashlib.sha256(body).hexdigest(),
            'representation_sha256':hashlib.sha256(encoded).hexdigest()}
