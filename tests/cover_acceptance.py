"""Seeded 20-feed x 3-page cover QA. Read-only and no model calls."""
import sys, asyncio, json, time, random
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
import httpx
from bs4 import BeautifulSoup
import core
from content_input import select_cover_from_page, first_image_src
from initialize_secrets import read_env

async def main():
    with core.connect() as c:
        feeds=[r[0] for r in c.execute("SELECT feed_id FROM analyses GROUP BY feed_id HAVING COUNT(*)>=3 ORDER BY feed_id")]
        random.Random(20260922).shuffle(feeds)
        rows=[]
        for fid in feeds[:20]:
            choices=[dict(r) for r in c.execute("SELECT entry_id,feed_id,title,url,cover_url,cover_source FROM analyses WHERE feed_id=? ORDER BY entry_id",(fid,))]
            rows+=random.Random(fid).sample(choices,3)
    sem=asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=12,follow_redirects=True,trust_env=False,headers={"User-Agent":"Mozilla/5.0 (compatible; PersonalAIInbox/1.0)"}) as external, httpx.AsyncClient(timeout=15,trust_env=False,headers={"X-Auth-Token":read_env("ai.env")["MINIFLUX_API_KEY"]}) as mf:
        async def one(row):
            async with sem:
                row["page_status"]=None
                try:
                    async def fetch():
                        async with external.stream("GET",row["url"]) as r:
                            row["page_status"]=r.status_code
                            if r.status_code!=200:return
                            body=b""
                            async for chunk in r.aiter_bytes():
                                body+=chunk
                                if len(body)>3*1024*1024:raise ValueError("page_too_large")
                        soup=BeautifulSoup(body,"html.parser")
                        row["selected"],row["selected_source"]=select_cover_from_page(body,str(r.url),row["title"])
                        tag=soup.find("meta",attrs={"property":"og:image"})
                        row["og"]=tag.get("content") if tag else None
                        tag=soup.select_one("article img,main img,[role=main] img")
                        row["article_first_image"]=tag.get("src") if tag else None
                    await asyncio.wait_for(fetch(),timeout=18)
                except Exception as e:row["page_error"]=type(e).__name__
                try:
                    r=await mf.get(f"http://127.0.0.1:8091/mf/v1/entries/{row['entry_id']}");r.raise_for_status()
                    row["extracted_first_image"]=first_image_src(r.json().get("content",""))
                except httpx.HTTPError as e:row["entry_error"]=type(e).__name__
                return row
        results=await asyncio.gather(*(one(r) for r in rows))
    report={"at":time.time(),"seed":20260922,"feed_count":len(set(r["feed_id"] for r in results)),"requested":len(results),"pages_fetched":sum(r["page_status"]==200 and not r.get("page_error") for r in results),"ai_requests":0,"rows":results}
    (core.ROOT/"artifacts/cover-qa.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!="rows"}))
asyncio.run(main())
