"""Create a new isolated instance; never read or migrate existing credentials."""
from pathlib import Path
import json, secrets, subprocess, os, time
ROOT=Path('/home/ubuntu/ai-news'); PRIVATE=ROOT/'.private'
PRIVATE.mkdir(exist_ok=True); PRIVATE.chmod(0o700)
def create_private(name,content):
 p=PRIVATE/name
 if not p.exists():
  p.write_text(content if isinstance(content,str) else json.dumps(content,indent=2))
 p.chmod(0o600)
 return p
create_private('postgres.password',secrets.token_urlsafe(32))
create_private('database.password',secrets.token_urlsafe(32))
create_private('access.json',{'username':'reader','password':secrets.token_urlsafe(24)})
create_private('provider.json',{'base_url':'https://ark.cn-beijing.volces.com/api/v3','model':'deepseek-v4-flash-ga-260731','api_key':''})
create_private('media.key',secrets.token_hex(32))
access=json.loads((PRIVATE/'access.json').read_text())
pgpass=(PRIVATE/'postgres.password').read_text().strip()
dbpass=(PRIVATE/'database.password').read_text().strip()
pgbin=ROOT/'runtime/pg/usr/lib/postgresql/16/bin'
env=dict(os.environ,XDG_RUNTIME_DIR='/run/user/'+str(os.getuid()),DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/'+str(os.getuid())+'/bus',LD_LIBRARY_PATH=str(ROOT/'runtime/pg/usr/lib/x86_64-linux-gnu'))
def run(args,**kw): return subprocess.run([str(x) for x in args],env=env,check=True,**kw)
pgdata=ROOT/'state/postgres'
if not (pgdata/'PG_VERSION').exists():
 run([pgbin/'initdb','-D',pgdata,'-U','newsowner','--auth=scram-sha-256','--pwfile='+str(PRIVATE/'postgres.password'),'-L',ROOT/'runtime/pg/usr/share/postgresql/16','--encoding=UTF8','--locale=C.UTF-8'])
(PRIVATE/'pgsocket').mkdir(exist_ok=True,mode=0o700)
units=Path.home()/'.config/systemd/user'; units.mkdir(parents=True,exist_ok=True)
pgunit=f'''[Unit]
Description=AI News isolated PostgreSQL
[Service]
Type=simple
WorkingDirectory={ROOT}
Environment=LD_LIBRARY_PATH={ROOT}/runtime/pg/usr/lib/x86_64-linux-gnu
ExecStart={pgbin}/postgres -D {pgdata} -p 55432 -c listen_addresses=127.0.0.1 -c unix_socket_directories={PRIVATE}/pgsocket -c shared_buffers=64MB -c max_connections=20 -c jit=off
Restart=on-failure
RestartSec=5
TimeoutStopSec=60
UMask=0077
[Install]
WantedBy=default.target
'''
(units/'ai-news-postgres.service').write_text(pgunit)
run(['systemctl','--user','daemon-reload'])
run(['systemctl','--user','enable','--now','ai-news-postgres.service'])
for _ in range(30):
 check=subprocess.run([str(pgbin/'pg_isready'),'-h','127.0.0.1','-p','55432'],env=env,capture_output=True)
 if check.returncode==0: break
 time.sleep(1)
else: raise RuntimeError('PostgreSQL did not become ready')
pg_env=dict(env,PGPASSWORD=pgpass)
def sql(text,db='postgres'):
 r=subprocess.run([str(pgbin/'psql'),'-h','127.0.0.1','-p','55432','-U','newsowner','-d',db,'-At','-v','ON_ERROR_STOP=1'],input=text,text=True,env=pg_env,capture_output=True)
 if r.returncode: raise RuntimeError('Database bootstrap failed; inspect local service logs')
 return r.stdout.strip()
if sql("SELECT 1 FROM pg_roles WHERE rolname='news_app'")!='1': sql("CREATE ROLE news_app LOGIN PASSWORD '"+dbpass+"'")
if sql("SELECT 1 FROM pg_database WHERE datname='news'")!='1': sql('CREATE DATABASE news OWNER news_app')
