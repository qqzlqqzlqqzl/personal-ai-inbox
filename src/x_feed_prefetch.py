"""Refresh a bounded slice of X feeds so Miniflux usually reads local cache."""

if __name__ == '__main__':
    import argparse
    argparse.ArgumentParser(allow_abbrev=False, description='Refresh one bounded slice of configured X feeds.').parse_args()
import json, time, urllib.request
from pathlib import Path

ROOT = Path("/home/ubuntu/ai-news")
ROSTER = ROOT / "x_sources.catalog.json"
STATE = ROOT / "state/x-prefetch-state.json"
REPORT = ROOT / "artifacts/x-prefetch-last.json"
BATCH = 18

def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"cursor": 0}

def main():
    roster = json.loads(ROSTER.read_text()).get("sources", [])
    roster = [x for x in roster if x.get("x_status") != "profile_unavailable"]
    if not roster:
        return
    state = load_state()
    cursor = int(state.get("cursor", 0)) % len(roster)
    chosen = [roster[(cursor + i) % len(roster)] for i in range(min(BATCH, len(roster)))]
    results = []
    for item in chosen:
        handle = item["handle"]
        started = time.time()
        url = f"http://127.0.0.1:17911/x/user/{handle}?refresh=true"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PersonalAIInbox-XPrefetch/1.0"})
            with urllib.request.urlopen(req, timeout=50) as response:
                response.read()
                source = response.headers.get("X-X-Feed-Source")
                count = int(response.headers.get("X-X-Feed-Items") or 0)
            results.append({"handle": handle, "ok": True, "source": source,
                            "items": count, "seconds": round(time.time() - started, 2)})
        except Exception as exc:
            results.append({"handle": handle, "ok": False,
                            "error": type(exc).__name__ + ": " + str(exc)[:180],
                            "seconds": round(time.time() - started, 2)})
        time.sleep(0.4)

    STATE.parent.mkdir(parents=True, exist_ok=True)
    next_cursor = (cursor + len(chosen)) % len(roster)
    STATE.write_text(json.dumps({"cursor": next_cursor, "updated_at": time.time()}))
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    report = {"at": time.time(), "cursor": cursor, "next_cursor": next_cursor,
              "batch_size": len(chosen), "results": results}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({
        "ok": sum(x["ok"] for x in results),
        "failed": [x["handle"] for x in results if not x["ok"]],
        "live": sum(x.get("source") == "live" for x in results),
        "stale": sum(x.get("source") == "stale-cache" for x in results),
        "deferred": sum(x.get("source") == "deferred-empty" for x in results),
        "next_cursor": next_cursor,
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
