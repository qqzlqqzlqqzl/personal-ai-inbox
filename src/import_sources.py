"""Idempotent feed import. AI worker, not the RSS description, obtains original text."""

if __name__ == '__main__':
    import argparse
    argparse.ArgumentParser(description='Import the configured feed catalog.').parse_args()

import json, time, concurrent.futures
from pathlib import Path
from ops_common import client

ROOT = Path("/home/ubuntu/ai-news")


def main():
    import httpx

    for _ in range(20):
        try:
            if httpx.get("http://127.0.0.1:8092/readyz", timeout=5).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Gateway not ready; no feeds modified")
    catalog = json.loads((ROOT / "sources.catalog.json").read_text())
    # Paper metadata requires a separate PDF pipeline and is not passed off as full text.
    wanted = [
        s for s in catalog if s["status"] == "ok" and s["category"] != "论文与前沿"
    ]
    with client() as c:
        feeds = c.get("/v1/feeds").json()
        existing = {f["feed_url"]: f["id"] for f in feeds}
        cats = {x["title"]: x["id"] for x in c.get("/v1/categories").json()}
        for category in dict.fromkeys(s["category"] for s in wanted):
            if category not in cats:
                r = c.post("/v1/categories", json={"title": category})
                r.raise_for_status()
                cats[category] = r.json()["id"]

    def add(s):
        result = {"name": s["name"], "url": s["url"], "category": s["category"]}
        if s["url"] in existing:
            return {**result, "state": "exists", "id": existing[s["url"]]}
        try:
            with client(timeout=90) as c:
                r = c.post(
                    "/v1/feeds",
                    json={
                        "feed_url": s["url"],
                        "category_id": cats[s["category"]],
                        "crawler": False,
                    },
                )
                result.update(
                    state="added" if r.status_code in (200, 201) else "failed",
                    http=r.status_code,
                )
                if result["state"] == "added":
                    result["id"] = r.json().get("feed_id")
        except Exception as e:
            result.update(state="failed", error=type(e).__name__)
        print(result["name"], result["state"], flush=True)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(add, wanted))
    report = {"at": time.time(), "results": results, "paper_metadata_deferred": 3}
    (ROOT / "artifacts/source-import.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    print(
        json.dumps(
            {
                k: sum(x["state"] == k for x in results)
                for k in ["added", "exists", "failed"]
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
