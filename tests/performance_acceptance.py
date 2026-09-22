"""Measure live transfer sizes without persisting credentials or response bodies."""
import sys, json, time, gzip, statistics, re, httpx
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ops_common import client, local_admin
ROOT = Path(__file__).resolve().parents[1]
phase = sys.argv[1] if len(sys.argv) > 1 else "after"
report_path = ROOT / "artifacts/performance-acceptance.json"
report = json.loads(report_path.read_text()) if report_path.exists() else {}
result = {"at": time.time(), "vantage": "production server loopback and public HTTPS (not user's last mile)", "samples": {}}
with client(timeout=25) as c:
 for label, base in [("local", "http://127.0.0.1:8092"), ("public", "https://106.53.40.6")]:
  for name, path in [("recommended12", "/mf/v1/entries?ai_view=recommended&ai_min=6&limit=12"), ("recommended24", "/mf/v1/entries?ai_view=recommended&ai_min=6&limit=24"), ("unread20", "/mf/v1/entries?status=unread&limit=20")]:
   samples=[]
   for _ in range(3):
    start=time.perf_counter()
    with c.stream("GET",base+path,headers={"Accept-Encoding":"gzip"}) as r:
     raw=b"".join(r.iter_raw()); r.raise_for_status()
     encoding=r.headers.get("content-encoding", "identity")
     body=gzip.decompress(raw) if encoding=="gzip" else raw
     data=json.loads(body)
     samples.append({"duration_ms":round((time.perf_counter()-start)*1000,1),"response_bytes":len(body),"compressed_bytes":len(raw),"encoding":encoding,"server_timing":r.headers.get("server-timing"),"entries":len(data["entries"]),"body_deferred":all(e.get("content_deferred") and not e["content"] for e in data["entries"])})
   result["samples"][label+"_"+name]={"median_ms":statistics.median(s["duration_ms"] for s in samples),"runs":samples}
  html=c.get(base+"/inbox/").text
  refs=re.findall(r'(?:src|href)="([^" ]+\.(?:js|css))"',html)
  assets=[]
  for path in refs:
   with c.stream("GET",base+path,headers={"Accept-Encoding":"gzip"}) as r:
    raw=b"".join(r.iter_raw()); r.raise_for_status()
    encoding=r.headers.get("content-encoding","identity")
    body=gzip.decompress(raw) if encoding=="gzip" else raw
    assets.append({"path":path,"compressed_bytes":len(raw),"response_bytes":len(body),"encoding":encoding})
  result["samples"][label+"_assets"]=assets
# The browser logs in with Basic auth; maintenance-token timings alone miss
# repeated password verification in the gateway's upstream fan-out.
with httpx.Client(auth=local_admin(), timeout=25, trust_env=False) as c:
 for label, base in [("local", "http://127.0.0.1:8092"), ("public", "https://106.53.40.6")]:
  samples=[]
  for _ in range(3):
   start=time.perf_counter()
   with c.stream("GET",base+"/mf/v1/entries?ai_view=recommended&ai_min=6&limit=24",headers={"Accept-Encoding":"gzip"}) as r:
    raw=b"".join(r.iter_raw());r.raise_for_status()
    encoding=r.headers.get("content-encoding","identity")
    body=gzip.decompress(raw) if encoding=="gzip" else raw
    data=json.loads(body)
    samples.append({"duration_ms":round((time.perf_counter()-start)*1000,1),"response_bytes":len(body),"compressed_bytes":len(raw),"encoding":encoding,"server_timing":r.headers.get("server-timing"),"entries":len(data["entries"]),"body_deferred":all(e.get("content_deferred") and not e["content"] for e in data["entries"])})
  result["samples"][label+"_basic_recommended24"]={"median_ms":statistics.median(s["duration_ms"] for s in samples),"runs":samples}
report[phase]=result
report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False))
