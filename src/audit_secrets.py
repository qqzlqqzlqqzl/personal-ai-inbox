"""Check all reachable Git blobs and nonignored files without printing secrets."""
import json, re, subprocess, sys
from pathlib import Path
from urllib.parse import quote, urlsplit, unquote
ROOT=Path('/home/ubuntu/ai-news')
def git(*args):
    return subprocess.run(['git','-C',str(ROOT),*args],capture_output=True,check=True,timeout=40).stdout
known=set()
def add(value):
    if isinstance(value,str) and len(value)>=12:
        known.add(value.encode()); known.add(quote(value,safe='').encode())
for name in ['arkKey.txt','postgres.password','database.password']:
    add((ROOT/'.private'/name).read_text(encoding='utf-8-sig').strip())
for name in ['miniflux.env','ai.env']:
    for line in (ROOT/'.private'/name).read_text().splitlines():
        if '=' in line:
            key,value=line.split('=',1)
            if key.endswith(('PASSWORD','KEY')): add(value)
            if key=='DATABASE_URL': add(unquote(urlsplit(value).password or ''))
for name in ['access.json','provider.json']:
    p=ROOT/'.private'/name
    if p.exists():
        for key,value in json.loads(p.read_text()).items():
            if key in ['password','api_key','token']: add(value)
rules=[re.compile(rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----'),
       re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),
       re.compile(rb'\bsk-[A-Za-z0-9_-]{30,}\b'),
       re.compile(rb'\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\b')]
fail=[]
def scan(data,label):
    if any(secret in data for secret in known): fail.append((label,'known secret'))
    if any(rule.search(data) for rule in rules): fail.append((label,'credential pattern'))
for line in git('rev-list','--objects','--all').splitlines():
    oid=line.split(b' ',1)[0].decode()
    if git('cat-file','-t',oid).strip()==b'blob': scan(git('cat-file','blob',oid),oid)
blocked={'.private','state','runtime','logs','backups'}
for rev in git('rev-list','--all').decode().splitlines():
    for raw in git('ls-tree','-r','--name-only','-z',rev).split(b'\0'):
        if raw and raw.decode().split('/')[0] in blocked: fail.append((rev,'forbidden historical path'))
files=set(git('ls-files','--cached','--others','--exclude-standard','-z').split(b'\0'))-{b''}
for raw in files:
    name=raw.decode(); path=ROOT/name
    if name.split('/')[0] in blocked: fail.append((name,'forbidden path')); continue
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT): fail.append((name,'unsafe path')); continue
    scan(path.read_bytes(),name)
print(json.dumps({'files_scanned':len(files),'findings':fail},ensure_ascii=False))
sys.exit(bool(fail))
