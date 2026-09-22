"""Install non-privileged units. Does not read, generate or migrate credentials."""
from pathlib import Path
import os, subprocess, shutil
ROOT=Path('/home/ubuntu/ai-news')
units=Path.home()/'.config/systemd/user'; units.mkdir(parents=True,exist_ok=True)
node=shutil.which('node')
if not node: raise RuntimeError('Node.js missing')
def unit(description,workdir,command,extra=''):
 return f'''[Unit]
Description={description}
[Service]
Type=simple
WorkingDirectory={workdir}
ExecStart={command}
Environment=PYTHONUNBUFFERED=1
{extra}
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
UMask=0077
[Install]
WantedBy=default.target
'''
(units/'ai-news-rsshub.service').write_text(unit('AI News RSSHub private adapters',ROOT/'upstream/rsshub',f'{node} dist/index.mjs','Environment=NODE_ENV=production\nEnvironment=NODE_OPTIONS=--max-old-space-size=512\nEnvironment=PORT=1200\nEnvironment=LISTEN_INADDR_ANY=0\nEnvironment=ENABLE_CLUSTER=0\nEnvironment=CACHE_TYPE=memory\nEnvironmentFile=-/home/ubuntu/ai-news/.private/rsshub.env'))
(units/'ai-news-web.service').write_text(unit('AI News private web gateway',ROOT/'src',f'{ROOT}/runtime/venv/bin/python -m uvicorn api:app --host 127.0.0.1 --port 8092 --no-access-log','EnvironmentFile=-/home/ubuntu/ai-news/.private/ai.env'))
mf=unit('AI News Miniflux reader',ROOT,f'{ROOT}/runtime/miniflux','EnvironmentFile=/home/ubuntu/ai-news/.private/miniflux.env')
mf=mf.replace('[Service]','After=ai-news-postgres.service\nConditionPathExists=/home/ubuntu/ai-news/.private/miniflux.env\n[Service]')
(units/'ai-news-miniflux.service').write_text(mf)
# Keep human-reviewable copies in the project; no runtime secrets are included.
for name in ['ai-news-postgres','ai-news-miniflux','ai-news-rsshub','ai-news-web']:
 source=units/(name+'.service')
 if source.exists(): (ROOT/'docs/ops'/(name+'.service')).write_text(source.read_text())
env=dict(os.environ,XDG_RUNTIME_DIR='/run/user/'+str(os.getuid()),DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/'+str(os.getuid())+'/bus')
subprocess.run(['systemctl','--user','daemon-reload'],env=env,check=True)
subprocess.run(['systemctl','--user','enable','--now','ai-news-rsshub.service','ai-news-web.service'],env=env,check=True)
print('RSSHub and gateway installed on loopback only. Miniflux unit awaits user configuration.')
