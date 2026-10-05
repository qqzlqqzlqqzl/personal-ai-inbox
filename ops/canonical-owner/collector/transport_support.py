"""Pinned reviewed support, extracted from sampler v3; no execution entry point."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit
PINS_SHA256 = '35a6e17708ee082b39f14dbb37d120a11d29d39d68ff281e5264ddddb619c714'

class Refused(Exception):
    pass

def require(condition, code):
    if not condition:
        raise Refused(code)

def unique(pairs):
    result={}
    for key,value in pairs:
        require(key not in result, 'duplicate_json_key')
        result[key]=value
    return result

def parse_json(raw):
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(Refused('nonfinite_json')))

def bounded_read(path, cap, private=False):
    """Read exact regular file through no-follow parent descriptors."""
    path=Path(os.path.abspath(path)); directory=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    leaf=None
    try:
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child
        leaf=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        before=os.fstat(leaf)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= cap,'input_file_boundary')
        if private:
            require(before.st_uid==os.getuid() and before.st_mode & 0o077==0 and before.st_nlink==1,
                    'private_credentials_boundary')
        parts=[];remaining=cap+1
        while remaining:
            chunk=os.read(leaf,min(65536,remaining))
            if not chunk:break
            parts.append(chunk);remaining-=len(chunk)
        data=b''.join(parts);after=os.fstat(leaf)
        identity=lambda info:(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        require(len(data)<=cap and identity(before)==identity(after),'input_changed')
        return data
    finally:
        if leaf is not None:os.close(leaf)
        os.close(directory)

def verify_install():
    raw=bounded_read(Path(__file__).with_name('SOURCE_PINS.json'),100000)
    require(hashlib.sha256(raw).hexdigest()==PINS_SHA256,'source_pin_manifest_changed')
    pins=parse_json(raw);distributions={}
    for name,version in pins['versions'].items():
        distributions[name]=importlib.metadata.distribution(name)
        require(distributions[name].version==version,'package_version_changed')
    for row in pins['files']:
        data=bounded_read(distributions[row['package']].locate_file(row['path']),2_000_000)
        require(len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256'],
                'installed_source_changed')

def existing_proxy():
    """Consume the already-approved route without editing config or environment."""
    value=os.environ.get('AI_NEWS_OUTBOUND_PROXY')
    require(isinstance(value,str) and 1<=len(value)<=4096
            and all(33<=ord(c)<127 for c in value),'explicit_outbound_proxy_required')
    parsed=urlsplit(value)
    require(parsed.scheme in ('http','https') and parsed.hostname
            and parsed.path in ('','/') and not parsed.query and not parsed.fragment,
            'unsupported_proxy_shape')
    require(parsed.port is None or 1<=parsed.port<=65535,'unsupported_proxy_port')
    return value

def no_proxy_conflict(mapping):
    # Conservative hostname-suffix refusal; no env writes, registry or DNS.
    values=[os.environ.get('no_proxy'),os.environ.get('NO_PROXY'),
            mapping.get('no'),mapping.get('no_proxy')]
    for value in values:
        if value is None or value=='':continue
        require(isinstance(value,str) and len(value)<=8192
                and not any(ord(c)<32 for c in value),'no_proxy_shape')
        for item in value.replace(' ','').split(','):
            item=item.casefold().lstrip('.')
            if not item:continue
            if item=='*':return True
            require('*' not in item,'unsupported_no_proxy_pattern')
            # Using the broader suffix check is deliberately fail-closed across
            # Requests versions; it may refuse a harmless suffix, never bypass.
            host=item[:-4] if item.endswith(':443') else item
            if 'api.kaggle.com'.endswith(host):return True
    return False

def bound_proxy_mapping(mapping, approved):
    require(isinstance(mapping,dict),'proxy_mapping_shape')
    require(not no_proxy_conflict(mapping),'no_proxy_route_conflict')
    keys=('https://api.kaggle.com','https','all://api.kaggle.com','all')
    for key in keys:
        if key in mapping:
            require(mapping[key]==approved,'effective_proxy_route_changed')
            break
    # Per-request routing uses exactly the existing AI_NEWS_OUTBOUND_PROXY.
    # No process environment or stored proxy configuration is changed.
    return {'https':approved,'http':approved}

class Quiet:
    def write(self, text):return len(text)
    def flush(self):pass
