"""Read-only live acceptance against production data; no synthetic articles inserted."""

import json, sys, time, math
from pathlib import Path
import httpx
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from ops_common import client, GATEWAY
from core import connect, evidence_matches

ROOT = Path("/home/ubuntu/ai-news")
report = {
    "at": time.time(),
    "kind": "live production HTTP and persisted data",
    "checks": [],
}


def check(name, passed, detail=None):
    report["checks"].append({"name": name, "passed": bool(passed), "detail": detail})


with httpx.Client(timeout=15, trust_env=False) as anonymous:
    health = anonymous.get(GATEWAY + "/healthz").json()
    check(
        "all_services_ready",
        health["ready"]
        and health["model_configured"]
        and health["reader_worker_configured"],
        health,
    )
    for path in ["/mf/v1/entries", "/mf/v1/ai/settings", "/mf/v1/ai/status"]:
        check("unauthorized_" + path, anonymous.get(GATEWAY + path).status_code == 401)
    for path in ["/.private/ai.env", "/.git/config", "/does-not-exist.js"]:
        check("not_served_" + path, anonymous.get(GATEWAY + path).status_code == 404)
with client() as c:
    feeds = c.get("/v1/feeds").json()
    entries = c.get("/v1/entries?limit=1").json()
    check(
        "broad_real_sources",
        len(feeds) >= 40,
        {"feeds": len(feeds), "entries": entries["total"]},
    )
    xml = c.get("/v1/export")
    xml.raise_for_status()
    outlines = ET.fromstring(xml.content).findall(".//outline[@xmlUrl]")
    check("opml_export_matches_sources", len(outlines) == len(feeds), len(outlines))
    (ROOT / "artifacts/current-subscriptions.opml").write_bytes(xml.content)
    for order, field in [
        ("score", "score"),
        ("technical", "technical_score"),
        ("business", "business_score"),
    ]:
        result = c.get(
            "/v1/entries",
            params={
                "ai_view": "recommended",
                "ai_min": 6,
                "ai_sort": order,
                "limit": 20,
            },
        ).json()
        values = [e["ai"][field] for e in result["entries"]]
        check(
            "live_ranking_" + order,
            len(values) > 0 and values == sorted(values, reverse=True),
        )
        check(
            "live_threshold_" + order,
            all(
                e["ai"]["score"] >= 6 and e["ai"]["worth_reading"]
                for e in result["entries"]
            ),
        )
    first = c.get("/v1/entries?ai_view=recommended&ai_min=6&limit=5&offset=0").json()
    second = c.get("/v1/entries?ai_view=recommended&ai_min=6&limit=5&offset=5").json()
    check(
        "pagination_no_duplicates",
        not (
            {e["id"] for e in first["entries"]} & {e["id"] for e in second["entries"]}
        ),
    )
    for path in ["/v1/ai/retry", "/v1/ai/feedback"]:
        check(
            "bad_entry_id_" + path,
            c.post(path, json={"entry_id": "invalid", "value": "useful"}).status_code
            == 400,
        )
with connect() as c:
    rows = [dict(r) for r in c.execute("SELECT * FROM analyses WHERE state='done'")]
    counts = dict(
        c.execute("SELECT state,COUNT(*) FROM analyses GROUP BY state").fetchall()
    )
    sources = len({r["feed_id"] for r in rows})
    usage = [
        dict(r)
        for r in c.execute(
            "SELECT day,COUNT(*) calls,SUM(COALESCE(actual,reserved)) tokens FROM usage GROUP BY day"
        )
    ]
check(
    "real_ark_multiple_sources",
    len(rows) >= 10 and sources >= 3,
    {"analyzed": len(rows), "distinct_sources": sources},
)
check(
    "every_success_has_input_evidence",
    all(
        r["source_text"]
        and evidence_matches(json.loads(r["result"])["evidence"], r["source_text"])
        for r in rows
    ),
)
check(
    "original_input_lengths_recorded",
    all(r["source_chars"] >= r["input_chars"] > 0 and r["extracted_at"] for r in rows),
)
check("real_images_preserved", sum(r["image_count"] > 0 for r in rows) >= 3)
check("no_error_misrepresented_as_done", all(r["error"] is None for r in rows))
report.update(
    counts=counts,
    usage=usage,
    model_names=sorted({r["model"] for r in rows}),
    samples=[
        {
            k: r[k]
            for k in [
                "entry_id",
                "title",
                "url",
                "input_chars",
                "image_count",
                "content_source",
                "model",
            ]
        }
        for r in rows[:5]
    ],
)
report["passed"] = all(x["passed"] for x in report["checks"])
(ROOT / "artifacts/live-acceptance.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(json.dumps(report, ensure_ascii=False))
sys.exit(0 if report["passed"] else 1)
