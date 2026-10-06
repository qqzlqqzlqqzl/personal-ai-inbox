"""Warm the newest All / AI recommended >=8 covers, independently of page opens.

Only the existing signed native image proxy is used. Analysis SQLite is read-only;
the only writes are the existing bounded image cache and one private status JSON.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import stat
import time
from urllib.parse import urlsplit

MAX_CANDIDATES = 240
MAX_MISSES = 30
NATIVE_BASE = "http://127.0.0.1:8091/mf"
WIDTHS = (480, 960)


class RoundStop(Exception):
    pass


class SlowSource(Exception):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def version(row):
    return digest([row.get(k) for k in ("entry_id", "user_id", "url", "cover_url",
                                       "content_hash", "content_quality", "state", "score", "updated_at")])


def select_candidates(rows, uid, quality):
    """Input is newest first; retain the real list's explicit quality exclusion."""
    result = []
    for source in rows:
        row = dict(source)
        score = row.get("score")
        if (type(row.get("entry_id")) is not int or row["entry_id"] <= 0
                or row.get("user_id") != uid or row.get("state") != "done"
                or type(score) not in (int, float) or not math.isfinite(score) or score < 8
                or not isinstance(row.get("cover_url"), str) or not row["cover_url"]
                or len(row["cover_url"]) > 4000
                or quality(row).get("recommendation_eligible") is False):
            continue
        row["version"] = version(row)
        result.append(row)
        if len(result) == MAX_CANDIDATES:
            break
    return result


def readonly_database(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def database_candidates(db, uid, quality):
    # First ten 24-entry pages, matching the actual default publication sort.
    rows = db.execute("""SELECT entry_id,user_id,url,feed_id,published_at,state,score,
        content_hash,cover_url,content_quality,updated_at,
        CASE WHEN content_quality IS NOT NULL THEN source_text END AS source_text
        FROM analyses WHERE user_id=? AND state='done' AND score>=8
        ORDER BY julianday(published_at) DESC,entry_id DESC LIMIT 240""", (uid,))
    return select_candidates(rows, uid, quality)


def bind_jobs(rows, metadata, feeds, sign, verify, *, quality):
    """Missing/removed, moved, cross-account or stale native identities cannot warm."""
    current = {entry["id"]: entry for entry in metadata}
    jobs, seen = [], set()
    for row in rows:
        entry = current.get(row["entry_id"])
        if (not entry or any(entry.get(k) != row.get(v) for k, v in
                (("id", "entry_id"), ("user_id", "user_id"), ("url", "url"), ("feed_id", "feed_id")))):
            continue
        feed = feeds.get(entry["feed_id"])
        if (not feed or feed["hide_globally"] or feed["category"]["hide_globally"]
                or quality(row, current_entry=entry).get("recommendation_eligible") is False):
            continue
        signed = sign(entry, row)
        path = signed[4:] if isinstance(signed, str) and signed.startswith("/mf/proxy/") else None
        target = verify(path) if path else None
        if not target or target in seen:
            continue
        seen.add(target)
        jobs.append({"row": row, "path": path, "key": digest([target, row["version"]]),
                     "source": digest(urlsplit(target).hostname)})
    return jobs


def warm_round(jobs, state, fetch, is_current, *, clock=time.time, monotonic=time.monotonic,
               deadline, max_misses=MAX_MISSES):
    """One worker; fetch's on_miss runs only when native HTTP actually starts."""
    items = state.get("items", {})
    sources = state.get("sources", {})
    counters = dict(selected=len(jobs), hits=0, misses=0, fetched=0, failed=0,
                    stale=0, deferred=0, stop="complete")
    active_keys = {job["key"] for job in jobs}
    active_sources = {job["source"] for job in jobs}
    state["items"] = items = {k: v for k, v in items.items() if k in active_keys}
    state["sources"] = sources = {k: v for k, v in sources.items() if k in active_sources}
    # Fresh/unattempted candidates retain newest-first priority; previously slow
    # sources cannot repeatedly consume the first 30 misses and starve later pages.
    ordered = sorted(jobs, key=lambda job: items.get(job["key"], {}).get("attempt_at", 0))

    def on_miss():
        if monotonic() >= deadline:
            raise RoundStop("deadline")
        if counters["misses"] >= max_misses:
            raise RoundStop("miss_limit")
        counters["misses"] += 1

    for job in ordered:
        if monotonic() >= deadline:
            counters["stop"] = "deadline"
            break
        now = clock()
        item = items.get(job["key"], {})
        if max(item.get("retry_at", 0), sources.get(job["source"], {}).get("retry_at", 0)) > now:
            counters["deferred"] += 1
            continue
        if not is_current(job["row"]):
            counters["stale"] += 1
            continue
        try:
            for width in WIDTHS:
                before = counters["misses"]
                ok = fetch(job["path"], width, on_miss)
                if counters["misses"] != before:
                    item["attempt_at"] = clock()
                    items[job["key"]] = item
                if not ok:
                    raise ValueError("image_unavailable")
                counters["hits" if counters["misses"] == before else "fetched"] += 1
            item.pop("retry_at", None)
            item.pop("failures", None)
        except RoundStop as exc:
            counters["stop"] = exc.args[0]
            break
        except Exception as exc:
            failures = min(6, item.get("failures", 0) + 1)
            retry_at = clock() + min(21600, 600 * 2 ** (failures - 1))
            item.update(attempt_at=clock(), failures=failures, retry_at=retry_at)
            items[job["key"]] = item
            if isinstance(exc, SlowSource):
                sources[job["source"]] = {"retry_at": retry_at}
            counters["failed"] += 1
    state["last_run"] = counters
    return counters


def json_response(client, method, suffix, *, deadline, **kwargs):
    if time.monotonic() >= deadline:
        raise RoundStop("deadline")
    with client.stream(method, NATIVE_BASE + suffix, **kwargs) as response:
        response.raise_for_status()
        if not response.headers.get("content-type", "").lower().startswith("application/json"):
            raise ValueError("native_json_required")
        body = bytearray()
        for chunk in response.iter_bytes():
            if time.monotonic() >= deadline or len(body) + len(chunk) > 4 << 20:
                raise RoundStop("metadata_limit")
            body.extend(chunk)
        return bytes(body), response.headers


def save_state(path, state):
    data = json.dumps(state, separators=(",", ":"), allow_nan=False).encode()
    if len(data) > 128 << 10:
        raise ValueError("warm_state_limit")
    temporary = path.with_suffix(".new")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(data)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", required=True)
    parser.parse_args()
    import fcntl
    import httpx
    from content_quality import public_for_row
    from notes_metadata import decode_feeds, decode_metadata
    from reader_cover_proxy import media_proxy_key, stored_cover_proxy, verified_proxy_target
    from reader_image_proxy import fetch_variant, FETCH_SECONDS, NATIVE_IMAGE_ACCEPT, image_cache
    from reader_image_cache import variant_key
    root = Path(os.environ.get("AI_NEWS_ROOT", "/home/ubuntu/ai-news"))
    folder = root / "state/reader-cover-warm"
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("warm_state_not_private")
    status_path = folder / "status.json"
    started, state = time.monotonic(), {"items": {}, "sources": {}}
    deadline = started + 110  # Leave time for the one private final receipt.
    lock = os.fdopen(os.open(folder / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return 0
    def alarm(_signum, _frame):
        raise RoundStop("deadline")
    signal.signal(signal.SIGALRM, alarm)
    signal.alarm(115)
    try:
        if status_path.exists():
            if status_path.stat().st_size > 128 << 10:
                raise ValueError("warm_state_limit")
            state = json.loads(status_path.read_text())
            if not isinstance(state.get("items"), dict) or not isinstance(state.get("sources"), dict):
                raise ValueError("invalid_warm_state")
        token, key = os.environ.get("MINIFLUX_API_KEY"), media_proxy_key()
        if not token or not key:
            raise ValueError("missing_warm_configuration")
        with httpx.Client(headers={"X-Auth-Token": token}, timeout=httpx.Timeout(8, connect=3),
                          trust_env=False, follow_redirects=False) as client:
            body, _ = json_response(client, "GET", "/v1/me", deadline=deadline)
            uid = json.loads(body).get("id")
            if type(uid) is not int or uid <= 0:
                raise ValueError("invalid_native_identity")
            db = readonly_database(root / "state/analysis.sqlite3")
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
            try:
                rows = database_candidates(db, uid, public_for_row)
                body, _ = json_response(client, "GET", "/v1/feeds", deadline=deadline)
                feeds = decode_feeds(body, uid)
                ids = [row["entry_id"] for row in rows]
                body, headers = json_response(client, "POST", "/v1/entries/metadata", deadline=deadline,
                                              json={"entry_ids": ids})
                if headers.get("X-Reader-Entry-Metadata") != "1":
                    raise ValueError("native_metadata_required")
                metadata = decode_metadata(body, uid, ids)
                jobs = bind_jobs(rows, metadata, feeds,
                    lambda entry, row: stored_cover_proxy(entry, row["cover_url"], row, uid, key, native_base=NATIVE_BASE),
                    lambda path: verified_proxy_target(path, key), quality=public_for_row)

                def current(row):
                    live = db.execute("SELECT * FROM analyses WHERE entry_id=? AND user_id=?",
                                      (row["entry_id"], uid)).fetchone()
                    return bool(live and version(dict(live)) == row["version"])

                def fetch(path, width, on_miss):
                    def factory(**kwargs):
                        if time.monotonic() + FETCH_SECONDS + 1 >= deadline:
                            raise RoundStop("deadline")
                        on_miss()
                        return httpx.AsyncClient(**kwargs)
                    try:
                        response = fetch_variant(NATIVE_BASE, path, width, "image/*", client_factory=factory)
                    except (httpx.TimeoutException, httpx.NetworkError):
                        raise SlowSource from None
                    if response.status_code == 429 or response.status_code >= 500:
                        raise SlowSource
                    target = verified_proxy_target(path, key)
                    # Use the exact existing key function, including canonical Accept.
                    # A plain 200 (unsupported media or failed cache write) is not a warm hit.
                    return bool(response.status_code == 200 and target and image_cache.get(
                        variant_key(NATIVE_BASE, target, width, NATIVE_IMAGE_ACCEPT)))
                counters = warm_round(jobs, state, fetch, current, deadline=deadline)
            finally:
                db.close()
        counters["seconds"] = round(time.monotonic() - started, 3)
        counters["at"] = int(time.time())
        save_state(status_path, state)
        print(json.dumps(counters, separators=(",", ":")))
        return 0
    except Exception as exc:
        reason = exc.args[0] if isinstance(exc, RoundStop) else "round_failed"
        state["last_run"] = {"stop": reason, "seconds": round(time.monotonic() - started, 3)}
        save_state(status_path, state)
        print(json.dumps(state["last_run"]))
        return 1
    finally:
        signal.alarm(0)
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
