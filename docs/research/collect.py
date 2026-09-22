import json, urllib.request, pathlib, concurrent.futures, datetime
ROOT=pathlib.Path(__file__).parent
REPOS=['miniflux/v2','electh/ReactFlux','Qetesh/miniflux-ai','WCY-dt/MrRSS','Tiendil/feeds.fun','karakeep-app/karakeep','brandonhon/ember','umputun/newscope','DIYgod/RSSHub']
def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':'PersonalAIReader-Research/1.0'})
    with urllib.request.urlopen(req,timeout=25) as r: return r.read()
def collect(repo):
    try:
        base='https://api.github.com/repos/'+repo
        d=json.loads(get(base)); branch=d['default_branch']
        releases=json.loads(get(base+'/releases?per_page=3'))
        p=ROOT/repo.replace('/','__'); p.mkdir(exist_ok=True)
        out={k:d.get(k) for k in ['full_name','created_at','pushed_at','default_branch','archived','stargazers_count','license']}
        out['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        out['releases']=[{k:r.get(k) for k in ['tag_name','published_at','prerelease','html_url','assets']} for r in releases]
        (p/'metadata.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
        tree=json.loads(get(base+'/git/trees/'+branch+'?recursive=1')); (p/'tree.json').write_text(json.dumps(tree))
        for name in ['README.md','LICENSE','package.json']:
            try: (p/name).write_bytes(get('https://raw.githubusercontent.com/'+repo+'/'+branch+'/'+name))
            except Exception: pass
        print(repo, 'commit='+str(d['pushed_at']), 'releases='+','.join(r['tag_name'] for r in releases),'sha='+tree.get('sha',''),flush=True)
    except Exception as e: print(repo, 'ERROR',str(e),flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex: list(ex.map(collect,REPOS))
