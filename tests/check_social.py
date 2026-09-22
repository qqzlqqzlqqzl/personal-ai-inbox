"""Live public Telegram adapter check; never assumes account access exists."""

import json, time, sys
from pathlib import Path
import httpx
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ops_common import client

ROOT = Path("/home/ubuntu/ai-news")
url = "http://127.0.0.1:1200/telegram/channel/telegram"
report = {
    "at": time.time(),
    "route": "/telegram/channel/telegram",
    "private_accounts": "not_authorized",
}
try:
    with httpx.Client(timeout=55, trust_env=False) as c:
        r = c.get(url)
    report["http"] = r.status_code
    if r.status_code == 200:
        tree = ET.fromstring(r.content)
        items = tree.findall(".//item")
        report["items"] = len(items)
        report["passed"] = len(items) > 0
        with client() as c:
            cats = c.get("/v1/categories").json()
            cat = next((x for x in cats if x["title"] == "社交动态"), None)
            if not cat:
                cat = c.post("/v1/categories", json={"title": "社交动态"}).json()
            feeds = c.get("/v1/feeds").json()
            if not any(f["feed_url"] == url for f in feeds):
                added = c.post(
                    "/v1/feeds",
                    json={"feed_url": url, "category_id": cat["id"], "crawler": False},
                )
                report["subscribe_http"] = added.status_code
    else:
        report["passed"] = False
except Exception as e:
    report.update(passed=False, error=type(e).__name__)
(ROOT / "artifacts/social-live.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(json.dumps(report, ensure_ascii=False))
