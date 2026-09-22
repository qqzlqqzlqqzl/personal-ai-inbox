"""Exercise a real duplicate-reuse job without spending model budget; restore its row."""
import sys,asyncio,json,logging,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import core,worker,httpx
from ops_common import load_worker_environment
logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

async def main():
    load_worker_environment()
    with core.connect() as c:
        row=c.execute("SELECT * FROM analyses WHERE state='done' AND duplicate_of IS NOT NULL ORDER BY entry_id LIMIT 1").fetchone()
        before=c.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
    if row is None:raise RuntimeError("No real reusable entry available")
    original=dict(row)
    report={"at":time.time(),"entry_id":row["entry_id"],"kind":"real_duplicate_reuse_not_new_model_call"}
    try:
        async with httpx.AsyncClient(timeout=20,trust_env=False) as c:
            await worker.process_one(c,row,core.settings())
        with core.connect() as c:
            after=dict(c.execute("SELECT * FROM analyses WHERE entry_id=?",(row["entry_id"],)).fetchone())
            used=c.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        report.update(state=after["state"],duplicate_of=after["duplicate_of"],model_requests_delta=used-before)
        assert after["state"]=="done" and after["duplicate_of"] and used==before
    finally:
        with core.connect() as c:
            keys=[k for k in original if k!="entry_id"]
            c.execute("UPDATE analyses SET "+",".join(k+"=?" for k in keys)+" WHERE entry_id=?",[original[k] for k in keys]+[row["entry_id"]])
        report["row_restored"]=True
        (core.ROOT/"artifacts/worker-logging-acceptance.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report))

asyncio.run(main())
