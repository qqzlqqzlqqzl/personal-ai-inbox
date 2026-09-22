"""Initialize only /home/ubuntu/ai-news; never display secret values."""
import os, secrets, subprocess, sys, time
from pathlib import Path
from urllib.parse import quote
import httpx

ROOT = Path('/home/ubuntu/ai-news')
PRIVATE = ROOT / '.private'
os.umask(0o077)
ENV = dict(os.environ, XDG_RUNTIME_DIR=f'/run/user/{os.getuid()}',
           DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{os.getuid()}/bus',
           LD_LIBRARY_PATH=str(ROOT / 'runtime/pg/usr/lib/x86_64-linux-gnu'))

def run(args, **kwargs):
    result = subprocess.run(args, env=kwargs.pop('env', ENV), timeout=kwargs.pop('timeout', 40),
                            capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError('Operation failed (output withheld): ' + Path(str(args[0])).name)
    return result.stdout.strip()

def read_secret(name):
    p = PRIVATE / name
    if p.is_symlink(): raise RuntimeError('Secret symlink refused')
    p.chmod(0o600)
    value = p.read_text(encoding='utf-8-sig').strip()
    if not value or '\n' in value or '\r' in value: raise RuntimeError('Invalid secret format: ' + name)
    return value

def read_env(name):
    p = PRIVATE / name
    return dict(line.split('=', 1) for line in p.read_text().splitlines() if line and not line.startswith('#')) if p.exists() else {}

def write_env(name, values):
    p = PRIVATE / name
    if p.is_symlink(): raise RuntimeError('Environment symlink refused')
    # Our generated values are URL-safe; Ark tokens must not contain environment metacharacters.
    lines = []
    for k, v in values.items():
        if any(c in v for c in '\n\r\0\"\'\\ \t'): raise RuntimeError('Unsupported environment value format')
        lines.append(k + '=' + v)
    with p.open('w', encoding='utf-8') as f: f.write('\n'.join(lines) + '\n')
    p.chmod(0o600)

def sql(query, database='postgres'):
    env = dict(ENV, PGPASSWORD=read_secret('postgres.password'))
    return run([str(ROOT/'runtime/pg/usr/lib/postgresql/16/bin/psql'), '-h','127.0.0.1','-p','55432',
                '-U','newsowner','-d',database,'-At','-v','ON_ERROR_STOP=1'], env=env, input=query)

def initialize():
    read_secret('arkKey.txt')
    dbpass = read_secret('database.password')
    if sql("SELECT 1 FROM pg_roles WHERE rolname='news_app'") != '1':
        sql("CREATE ROLE news_app LOGIN PASSWORD '" + dbpass.replace("'", "''") + "'")
    if sql("SELECT 1 FROM pg_database WHERE datname='news'") != '1':
        sql('CREATE DATABASE news OWNER news_app')
    if sql("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='news'") != 'news_app':
        raise RuntimeError('Existing database owner mismatch')
    old = read_env('miniflux.env')
    values = dict(DATABASE_URL='postgresql://news_app:'+quote(dbpass,safe='')+'@127.0.0.1:55432/news?sslmode=disable',
                  ADMIN_USERNAME='qqzl', ADMIN_PASSWORD=old.get('ADMIN_PASSWORD') or secrets.token_urlsafe(36),
                  CREATE_ADMIN='1',RUN_MIGRATIONS='1',BASE_URL='http://127.0.0.1:8092/mf',LISTEN_ADDR='127.0.0.1:8091',
                  WORKER_POOL_SIZE='2',DATABASE_MAX_CONNS='5',DATABASE_MIN_CONNS='1',POLLING_FREQUENCY='30',
                  FETCHER_ALLOW_PRIVATE_NETWORKS='1',MEDIA_PROXY_MODE='all',LOG_LEVEL='warning')
    write_env('miniflux.env', values)
    ai = read_env('ai.env'); ai['ARK_API_KEY'] = read_secret('arkKey.txt'); write_env('ai.env', ai)
    run(['systemctl','--user','daemon-reload'])
    run(['systemctl','--user','enable','--now','ai-news-miniflux.service'])
    run(['systemctl','--user','restart','ai-news-web.service','ai-news-rsshub.service'],timeout=70)
    with httpx.Client(timeout=10, trust_env=False) as client:
        for _ in range(40):
            try:
                if client.get('http://127.0.0.1:8091/mf/healthcheck').status_code == 200: break
            except httpx.HTTPError: pass
            time.sleep(1)
        else: raise RuntimeError('Miniflux did not become healthy')
        auth = (values['ADMIN_USERNAME'], values['ADMIN_PASSWORD'])
        base = 'http://127.0.0.1:8091/mf/v1'
        response = client.get(base+'/me', auth=auth)
        if response.status_code != 200: raise RuntimeError('Miniflux login failed')
        response = client.get(base+'/api-keys',auth=auth)
        if response.status_code != 200: raise RuntimeError('API key enumeration failed')
        token = next((x['token'] for x in response.json() if x['description']=='ai-news-worker'), None)
        if not token:
            response = client.post(base+'/api-keys',auth=auth,json={'description':'ai-news-worker'})
            if response.status_code not in (200,201): raise RuntimeError('API key creation failed')
            token = response.json()['token']
        ai['MINIFLUX_API_KEY'] = token; write_env('ai.env', ai)
        response = client.get(base+'/me',headers={'X-Auth-Token':token})
        print('Miniflux /v1/me HTTP',response.status_code)
        if response.status_code != 200: raise RuntimeError('API token verification failed')
    run(['systemctl','--user','restart','ai-news-web.service'])
    print('Secret initialization complete; values withheld')

if __name__ == '__main__':
    try: initialize()
    except Exception as exc:
        print('Initialization failed:',type(exc).__name__,file=sys.stderr)
        sys.exit(1)
