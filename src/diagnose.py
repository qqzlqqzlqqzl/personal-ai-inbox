"""Read-only, allowlisted operational summary. Never dump environment or journal bodies."""
import json, os, re, subprocess, time, sqlite3, datetime
from pathlib import Path
from ops_common import client
ROOT = Path(__file__).resolve().parents[1]

def diagnose():
    env = dict(os.environ, XDG_RUNTIME_DIR=f"/run/user/{os.getuid()}",
               DBUS_SESSION_BUS_ADDRESS=f"unix:path=/run/user/{os.getuid()}/bus")
    services = {}
    for name in ("web", "miniflux", "rsshub", "postgres"):
        r = subprocess.run(["systemctl", "--user", "is-active", "ai-news-"+name],
                           env=env, timeout=10, capture_output=True, text=True)
        services[name] = r.stdout.strip() if r.stdout.strip() in ("active", "inactive", "failed", "activating") else "unknown"
    out = {"at": time.time(), "services": services}
    with client(timeout=10) as c:
        health = c.get("http://127.0.0.1:8092/healthz"); health.raise_for_status()
        out["healthz"] = health.json()
        r = c.get("/v1/feeds"); r.raise_for_status()
        out["feeds"] = {"total": len(r.json()), "error_count": sum(bool(f.get("parsing_error_count")) for f in r.json())}
    with sqlite3.connect(f"file:{ROOT}/state/analysis.sqlite3?mode=ro", uri=True) as c:
        c.row_factory=sqlite3.Row
        out["events"]=[dict(r) for r in c.execute("SELECT at,kind,entry_id FROM events ORDER BY id DESC LIMIT 20")]
        row=c.execute("SELECT value FROM settings WHERE name='meta:worker_heartbeat'").fetchone()
        heartbeat=json.loads(row[0]) if row else None
        out["worker_heartbeat"]={"at":heartbeat,"age_seconds":round(time.time()-heartbeat,1) if heartbeat else None}
        row=c.execute("SELECT value FROM settings WHERE name='preferences'").fetchone()
        cfg=json.loads(row[0]) if row else {}
        day=datetime.datetime.now(datetime.timezone.utc).date().isoformat()
        usage=c.execute("SELECT COUNT(*) calls,COALESCE(SUM(COALESCE(actual,reserved)),0) tokens FROM usage WHERE day=?",(day,)).fetchone()
        out["budget"]={"day":day,"daily_articles":cfg.get("daily_articles",80),"daily_tokens":cfg.get("daily_tokens",500000),**dict(usage)}
    journal=subprocess.run(["journalctl","--user","-u","ai-news-web","--since","30 min ago","-n","5000","-o","cat","--no-pager"],env=env,timeout=15,capture_output=True,text=True)
    requests=[]
    pattern=r"ai-news request id=([a-f0-9]+) method=([A-Z]+) path=(/mf/v1/[a-zA-Z0-9/_-]+) status=(\d+) duration_ms=([\d.]+) response_bytes=([\d-]+)"
    for m in re.finditer(pattern,journal.stdout):
        requests.append(dict(zip(("request_id","method","path","status","duration_ms","response_bytes"),m.groups())))
    slow=[r for r in requests if float(r["duration_ms"])>=500]
    out["requests_30m"]={"sampled":len(requests),"slow_count":len(slow),"recent_slow":slow[-10:],"journal_available":journal.returncode==0,"bounded_to_last_lines":5000}
    return out

if __name__ == "__main__":
    print(json.dumps(diagnose(),ensure_ascii=False,indent=2))
