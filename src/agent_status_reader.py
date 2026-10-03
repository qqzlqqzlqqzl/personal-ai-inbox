"""Isolated private-data-branch reader; reuses config in place, never copies secrets."""
import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from agent_status_publisher import normalize
from agent_status_store import MAX_BYTES, InvalidSample, record_pull, validate

class ReadFailure(Exception): pass
APPROVED_REF='refs/heads/ops/dot-agent-status'
def git(args, timeout=35):
    env={**os.environ,'GIT_TERMINAL_PROMPT':'0','SSH_ASKPASS':'/bin/false','SSH_ASKPASS_REQUIRE':'force'}
    try:
        result=subprocess.run(['git',*args],env=env,capture_output=True,timeout=timeout)
    except (OSError, subprocess.TimeoutExpired): raise ReadFailure('git_unavailable_or_timeout') from None
    if result.returncode: raise ReadFailure('git_read_failed')
    return result.stdout

def read_file(source_checkout, cache_dir, ref):
    source=Path(source_checkout).resolve();cache=Path(cache_dir).resolve()
    if cache.is_relative_to(source) or source.is_relative_to(cache): raise ReadFailure('cache_not_isolated')
    if ref != APPROVED_REF:
        raise ReadFailure('invalid_ref')
    config=Path(git(['-C',str(source),'rev-parse','--absolute-git-dir']).decode().strip())/'config'
    remote=git(['-C',str(source),'config','--get','remote.origin.url']).decode().strip()
    parsed=urlsplit(remote)
    if parsed.scheme!='ssh' or parsed.hostname not in ['github.com','ssh.github.com'] or parsed.password is not None or parsed.path.rstrip('/')!='/qqzlqqzlqqzl/personal-ai-inbox.git':
        raise ReadFailure('unexpected_origin')
    # ls-remote does not change source HEAD, index, worktree, refs or FETCH_HEAD.
    output=git(['-C',str(source),'ls-remote','--exit-code','origin',ref]).decode().splitlines()
    candidates=[line.split('\t') for line in output if '\t' in line]
    matches=[sha for sha,name in candidates if name==ref and re.fullmatch(r'[0-9a-f]{40}',sha)]
    if len(matches)!=1: raise ReadFailure('ref_not_resolved')
    sha=matches[0]
    if not cache.exists():
        cache.mkdir(parents=True,mode=0o700)
        git(['init','--bare',str(cache)])
    elif git(['--git-dir='+str(cache),'rev-parse','--is-bare-repository']).strip()!=b'true':
        raise ReadFailure('cache_not_bare')
    # Read the existing authorized config through include.path; do not persist it.
    context=['--git-dir='+str(cache),'-c','include.path='+str(config),'-c','core.bare=true','-c','core.hooksPath=/dev/null','-c','gc.auto=0','-c','maintenance.auto=0','-c','fetch.writeCommitGraph=false']
    git([*context,'fetch','--depth=1','--no-tags','--no-write-fetch-head','--no-recurse-submodules','origin',sha])
    files=set(git([*context,'ls-tree','--name-only',sha]).decode().splitlines())
    if not {'agent-status.json'}<=files or not files<={'agent-status.json','README','README.md'}:
        raise ReadFailure('not_data_only_branch')
    spec=sha+':agent-status.json'
    if git([*context,'cat-file','-t',spec]).strip()!=b'blob': raise ReadFailure('not_blob')
    try: size=int(git([*context,'cat-file','-s',spec]).strip())
    except ValueError: raise ReadFailure('size') from None
    if size>MAX_BYTES: raise ReadFailure('size')
    raw=git([*context,'show',spec])
    if len(raw)!=size: raise ReadFailure('size')
    return raw

def pull(source_checkout,cache_dir,ref,state_path,now):
    source=Path(source_checkout).resolve();state=Path(state_path).resolve()
    if state.is_relative_to(source) or state.is_relative_to(Path(cache_dir).resolve()): raise ReadFailure('state_not_isolated')
    try:
        raw=read_file(source_checkout,cache_dir,ref)
        try: sample=validate(raw,now) # Preferred canonical white-list schema.
        except InvalidSample: sample=normalize(raw,now) # Existing observed publisher format.
        payload=json.dumps(sample,ensure_ascii=False).encode('utf-8')
    except (ReadFailure,InvalidSample): payload=None
    return record_pull(state_path,payload,now)

def main():
    parser=argparse.ArgumentParser()
    for key in ['source-checkout','cache-dir','ref','state-path']:parser.add_argument('--'+key,required=True)
    args=parser.parse_args()
    try:
        result=pull(args.source_checkout,args.cache_dir,args.ref,args.state_path,datetime.now(timezone.utc))
        print(json.dumps({'pull_status':result['pull_status'],'freshness':result['freshness']}))
    except (ReadFailure,InvalidSample,ValueError,OSError):
        print(json.dumps({'pull_status':'failed','freshness':'unknown'}))
        return 2
    return 0
if __name__=='__main__':raise SystemExit(main())