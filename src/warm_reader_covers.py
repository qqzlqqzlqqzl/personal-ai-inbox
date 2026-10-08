"""Warm common Reader sort heads before deeper recommended article images.

Only the existing signed native image proxy is used. Analysis SQLite is read-only;
the only writes are the existing bounded image cache and one private status JSON.
"""
import argparse
from contextlib import contextmanager
from html.parser import HTMLParser
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

PAGE_SIZE = 24
HEAD_ENTRIES = 2 * PAGE_SIZE
HEAD_SORTS = ("time", "score", "technical", "business")
MAX_PAGES = 16
MAX_MISSES = 60
MAX_STATE_ITEMS = 128
MAX_ARTICLE_RECEIPTS = 512
MAX_STATE_BYTES = 128 << 10
NATIVE_BASE = "http://127.0.0.1:8091/mf"
WIDTHS = (480, 960)


class RoundStop(Exception):
    pass


class SlowSource(Exception):
    pass


class CacheFull(Exception):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, separators=(",", ":")).encode()).hexdigest()


def version(row):
    return digest([row.get(k) for k in ("entry_id", "user_id", "url", "cover_url",
                                       "content_hash", "content_quality", "state", "score", "updated_at", "published_at")])


def select_candidates(rows, uid, quality):
    """Input is newest first; retain the real list's explicit quality exclusion."""
    result = []
    for source in rows:
        row = dict(source)
        score = row.get("score")
        if (type(row.get("entry_id")) is not int or row["entry_id"] <= 0
                or row.get("user_id") != uid or row.get("state") != "done"
                or type(score) not in (int, float) or not math.isfinite(score) or score < 8
                or quality(row).get("recommendation_eligible") is False):
            continue
        row["version"] = version(row)
        result.append(row)
    return result


def readonly_database(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def database_candidates(db, uid, quality, *, after=None, limit=PAGE_SIZE):
    """Bounded keyset page; the cursor follows scanned rows, even excluded ones."""
    params = [uid]
    clause = ""
    if after is not None:
        clause = " AND (COALESCE(julianday(published_at),0),entry_id) < (?,?)"
        params.extend(after)
    params.append(limit)
    rows = list(db.execute("""SELECT *,COALESCE(julianday(published_at),0) AS published_order
        FROM analyses WHERE user_id=? AND state='done' AND score>=8""" + clause +
        " ORDER BY COALESCE(julianday(published_at),0) DESC,entry_id DESC LIMIT ?", params))
    cursor = [rows[-1]["published_order"], rows[-1]["entry_id"]] if rows else after
    return select_candidates(rows, uid, quality), cursor, len(rows) < limit


def database_heads(db, uid, quality, *, limit=HEAD_ENTRIES):
    """Live, interleaved sort heads: no fixed feeds, dates or article IDs."""
    columns = {"time": "julianday(published_at)", "score": "score",
               "technical": "technical_score", "business": "business_score"}
    lanes = []
    for field in HEAD_SORTS:
        rows = db.execute("SELECT *,COALESCE(julianday(published_at),0) AS published_order "
                          "FROM analyses WHERE user_id=? AND state='done' AND score>=8 "
                          "ORDER BY " + columns[field] + " DESC,entry_id DESC LIMIT ?",
                          (uid, limit * 4)).fetchall()
        lanes.append(select_candidates(rows, uid, quality)[:limit])
    result, seen = [], set()
    for index in range(limit):
        for lane in lanes:
            if index < len(lane) and lane[index]["entry_id"] not in seen:
                seen.add(lane[index]["entry_id"])
                result.append(lane[index])
    return result


NON_IMAGES = {".svg", ".svgz", ".mp4", ".webm", ".mov", ".m4v", ".mp3", ".m4a", ".ogg", ".wav", ".pdf"}


def signed_image_path(value, verify):
    from reader_cover_proxy import _native_proxy
    proxy = _native_proxy(value, (NATIVE_BASE, "http://127.0.0.1:8092/mf"))
    if not proxy:
        return None
    path = proxy[0][4:]
    target = verify(path)
    if not target or Path(urlsplit(target).path.lower()).suffix in NON_IMAGES:
        return None
    return path, target


class BodyImages(HTMLParser):
    """Only image-bearing HTML attributes; never video sources, links or scripts."""
    def __init__(self, verify):
        super().__init__(convert_charrefs=True)
        self.verify, self.picture_depth, self.images = verify, 0, []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "picture":
            self.picture_depth += 1
        if tag != "img" and not (tag == "source" and self.picture_depth):
            return
        mime = (values.get("type") or "").lower().split(";", 1)[0]
        if mime and mime not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}:
            return
        urls = [values.get("src")]
        urls.extend(part.strip().split()[0] for part in (values.get("srcset") or "").split(",") if part.strip())
        for value in urls:
            matched = signed_image_path(value, self.verify)
            if matched:
                self.images.append(matched)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "picture":
            self.picture_depth = max(0, self.picture_depth - 1)


def bind_jobs(rows, metadata, feeds, sign, verify, *, quality, seen=None):
    """Current native entries may contain the exact decorated detail body."""
    from notes_metadata import published_timestamp
    current = {entry["id"]: entry for entry in metadata}
    jobs = []
    seen = set() if seen is None else seen
    for row in rows:
        entry = current.get(row["entry_id"])
        if (not entry or any(entry.get(k) != row.get(v) for k, v in
                (("id", "entry_id"), ("user_id", "user_id"), ("url", "url"), ("feed_id", "feed_id")))):
            continue
        feed = feeds.get(entry["feed_id"])
        if (not feed or feed["hide_globally"] or feed["category"]["hide_globally"]
                or quality(row, current_entry=entry).get("recommendation_eligible") is False):
            continue
        images = {}
        if isinstance(row.get("cover_url"), str) and 0 < len(row["cover_url"]) <= 4000:
            matched = signed_image_path(sign(entry, row), verify)
            if matched:
                path, target = matched
                images[target] = {"path": path, "widths": list(WIDTHS)}
        content = entry.get("content")
        if isinstance(content, str):
            parser = BodyImages(verify)
            parser.feed(content)
            for path, target in parser.images:
                item = images.setdefault(target, {"path": path, "widths": []})
                if 0 not in item["widths"]:
                    item["widths"].append(0)
        for target, item in images.items():
            widths = [width for width in item["widths"] if (target, width) not in seen]
            seen.update((target, width) for width in widths)
            if not widths:
                continue
            jobs.append({"row": row, "path": item["path"], "widths": widths,
                         "priority": published_timestamp(row),
                         "key": digest([target, row["version"]]), "source": digest(urlsplit(target).hostname)})
    return jobs


def prune_state(state, now):
    # Keep cross-page backoff, bounded independently of the number of articles.
    for name in ("items", "sources"):
        values = state.get(name, {})
        values = {k: v for k, v in values.items() if isinstance(v, dict)
                  and v.get("retry_at", 0) > now - 86400}
        state[name] = dict(sorted(values.items(), key=lambda item: item[1]["retry_at"], reverse=True)[:MAX_STATE_ITEMS])
    articles = {key: value for key, value in state.get('articles', {}).items()
                if isinstance(value, dict) and value.get('until', 0) > now}
    state['articles'] = dict(sorted(articles.items(), key=lambda item: item[1].get('priority', 0),
                                   reverse=True)[:MAX_ARTICLE_RECEIPTS])


def warm_round(jobs, state, fetch, is_current, *, clock=time.time, monotonic=time.monotonic,
               deadline, max_misses=MAX_MISSES, counters=None):
    """One worker; fetch's on_miss runs only when native HTTP actually starts."""
    items = state.setdefault("items", {})
    sources = state.setdefault("sources", {})
    if counters is None:
        counters = dict(selected=0, hits=0, misses=0, fetched=0, failed=0,
                        stale=0, deferred=0, full=0, stop="complete")
    counters["selected"] += len(jobs)

    def on_miss():
        if monotonic() >= deadline:
            raise RoundStop("deadline")
        if counters["misses"] >= max_misses:
            raise RoundStop("miss_limit")
        counters["misses"] += 1

    for job in jobs:
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
            for width in job.get("widths", WIDTHS):
                before = counters["misses"]
                if monotonic() >= deadline:
                    raise RoundStop("deadline")
                ok = fetch(job["path"], width, on_miss)
                if counters["misses"] != before:
                    item["attempt_at"] = clock()
                    items[job["key"]] = item
                if not ok:
                    raise ValueError("image_unavailable")
                counters["hits" if counters["misses"] == before else "fetched"] += 1
            items.pop(job["key"], None)
        except RoundStop as exc:
            counters["stop"] = exc.args[0]
            break
        except CacheFull:
            counters["full"] += 1
            counters["stop"] = "capacity"
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
    if len(data) > MAX_STATE_BYTES:
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
    from notes_metadata import decode_feeds, decode_metadata, body_matches, published_timestamp
    from reader_cover_proxy import media_proxy_key, stored_cover_proxy, verified_proxy_target
    from reader_image_proxy import fetch_variant, FETCH_SECONDS, NATIVE_IMAGE_ACCEPT, image_cache, BackgroundCacheFull
    from reader_image_cache import variant_key, cache_lifetime
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
            if status_path.stat().st_size > MAX_STATE_BYTES:
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
            if state.get("owner") != digest(uid):
                state = {"owner": digest(uid), "items": {}, "sources": {}, "cursor": None}
            db = readonly_database(root / "state/analysis.sqlite3")
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
            try:
                import core
                # This CLI is a single-threaded separate process. Route every
                # decorate dependency through this already read-only connection;
                # no migrations, WAL setup, card enqueue or business writes occur.
                @contextmanager
                def read_connection():
                    yield db
                original_connect = core.connect
                core.connect = read_connection
                try:
                    body, _ = json_response(client, "GET", "/v1/feeds", deadline=deadline)
                    feeds = decode_feeds(body, uid)
                    verify = lambda path: verified_proxy_target(path, key)
                    counters = warm_round([], state, None, None, deadline=deadline)
                    prune_state(state, time.time())
                    seen = set()

                    def current(row):
                        live = db.execute("SELECT * FROM analyses WHERE entry_id=? AND user_id=?",
                                          (row["entry_id"], uid)).fetchone()
                        return bool(live and version(dict(live)) == row["version"])

                    def advance(cursor):
                        if cursor is not None and (state.get("cursor") is None or tuple(cursor) < tuple(state["cursor"])):
                            state["cursor"] = list(cursor)

                    def fetch_image(path, width, on_miss, *, priority, hot_until=0):
                        def factory(**kwargs):
                            if time.monotonic() + FETCH_SECONDS + 1 >= deadline:
                                raise RoundStop("deadline")
                            on_miss()
                            return httpx.AsyncClient(**kwargs)
                        try:
                            response = fetch_variant(NATIVE_BASE, path, width, NATIVE_IMAGE_ACCEPT,
                                client_factory=factory, priority=priority, background=True,
                                hot_until=hot_until)
                        except BackgroundCacheFull:
                            raise CacheFull from None
                        except (httpx.TimeoutException, httpx.NetworkError, TimeoutError):
                            raise SlowSource from None
                        if response.status_code == 429 or response.status_code >= 500:
                            raise SlowSource
                        target = verify(path)
                        hit = image_cache.get(variant_key(NATIVE_BASE, target, width, NATIVE_IMAGE_ACCEPT)) if target else None
                        return bool(response.status_code == 200 and hit)

                    # Cover-only heads run first; large body images cannot consume
                    # this front-of-list budget. Hits reuse the same signed variants
                    # as browser requests and refresh only a shared five-minute bucket.
                    hot_until = (int(time.time()) // 300) * 300 + 900
                    head_rows = database_heads(db, uid, public_for_row)
                    head_jobs = []
                    for start in range(0, len(head_rows), PAGE_SIZE):
                        group = head_rows[start:start + PAGE_SIZE]
                        ids = [row["entry_id"] for row in group]
                        body, headers = json_response(client, "POST", "/v1/entries/metadata", deadline=deadline,
                                                      json={"entry_ids": ids})
                        if headers.get("X-Reader-Entry-Metadata") != "1":
                            raise ValueError("native_metadata_required")
                        metadata = decode_metadata(body, uid, ids)
                        head_jobs.extend(bind_jobs(group, metadata, feeds,
                            lambda entry, row: stored_cover_proxy(entry, row.get("cover_url"), row, uid, key, native_base=NATIVE_BASE),
                            verify, quality=public_for_row, seen=seen))
                    priorities = {job["path"]: job["priority"] for job in head_jobs}
                    counters["head_entries"] = len(head_rows)
                    counters["head_images"] = len(head_jobs)
                    warm_round(head_jobs, state,
                        lambda path, width, miss: fetch_image(path, width, miss,
                            priority=priorities[path], hot_until=hot_until),
                        current, deadline=deadline, counters=counters)
                    counters["head_misses"] = counters["misses"]

                    after, pages = None, 0
                    # Every round revisits the newest page first (new arrivals and
                    # expired originals), then resumes its persisted deeper page.
                    while counters["stop"] == "complete" and pages < MAX_PAGES and time.monotonic() < deadline:
                        rows, page_cursor, ended = database_candidates(db, uid, public_for_row, after=after)
                        pages += 1
                        ids = [row["entry_id"] for row in rows]
                        metadata = {}
                        if ids:
                            body, headers = json_response(client, "POST", "/v1/entries/metadata", deadline=deadline,
                                                          json={"entry_ids": ids})
                            if headers.get("X-Reader-Entry-Metadata") != "1":
                                raise ValueError("native_metadata_required")
                            metadata = {entry["id"]: entry for entry in decode_metadata(body, uid, ids)}
                        for row in rows:
                            entry = metadata.get(row["entry_id"])
                            row_cursor = [row["published_order"], row["entry_id"]]
                            detail_key = digest(["detail", row["version"]])
                            if state["items"].get(detail_key, {}).get("retry_at", 0) > time.time():
                                advance(row_cursor)
                                continue
                            try:
                                feed = feeds.get(row["feed_id"])
                                if (not entry or not feed or feed["hide_globally"] or feed["category"]["hide_globally"]
                                        or any(entry.get(k) != row.get(v) for k, v in
                                               (("id", "entry_id"), ("user_id", "user_id"), ("url", "url"), ("feed_id", "feed_id")))
                                        or not current(row)):
                                    counters["stale"] += 1
                                    advance(row_cursor)
                                    continue
                                receipt_key = digest([row['version'], entry])
                                if state['articles'].get(receipt_key, {}).get('until', 0) > time.time():
                                    advance(row_cursor)
                                    continue
                                priority = published_timestamp(row)
                                if not image_cache.can_admit(priority):
                                    counters['full'] += 1
                                    counters['stop'] = 'capacity'
                                    break  # Never retrieve bodies/images further down a full cache.
                                recheck_at = [time.time() + 600]
                                body, _ = json_response(client, "GET", "/v1/entries/" + str(row["entry_id"]), deadline=deadline)
                                native = json.loads(body)
                                if (not isinstance(native, dict) or native.get("id") != entry["id"]
                                        or native.get("user_id") != uid or not body_matches(native, entry, feeds, {})):
                                    raise ValueError("native_body_identity_changed")
                                decorated = core.decorate(native, uid, include_source_fallback=True)
                                jobs = bind_jobs([row], [decorated], feeds,
                                    lambda entry, row: stored_cover_proxy(entry, row.get("cover_url"), row, uid, key, native_base=NATIVE_BASE),
                                    verify, quality=public_for_row, seen=seen)

                                def fetch(path, width, on_miss):
                                    ok = fetch_image(path, width, on_miss, priority=priority)
                                    target = verify(path)
                                    hit = image_cache.get(variant_key(NATIVE_BASE, target, width, NATIVE_IMAGE_ACCEPT)) if target else None
                                    if hit:
                                        recheck_at[0] = min(recheck_at[0], time.time() + cache_lifetime(hit[1]))
                                    return ok

                                warm_round(jobs, state, fetch, current, deadline=deadline, counters=counters)
                                if counters["stop"] != "complete":
                                    break  # Keep this article at the continuation boundary.
                                state["items"].pop(detail_key, None)
                                state['articles'][receipt_key] = {'until': recheck_at[0], 'priority': priority}
                            except RoundStop:
                                raise
                            except Exception:
                                old = state["items"].get(detail_key, {})
                                failures = min(6, old.get("failures", 0) + 1)
                                state["items"][detail_key] = {"failures": failures,
                                    "retry_at": time.time() + min(21600, 600 * 2 ** (failures - 1))}
                                counters["failed"] += 1
                            advance(row_cursor)
                        if counters["stop"] != "complete":
                            break
                        advance(page_cursor)
                        if ended:
                            state["cursor"] = None
                            break
                        after = state.get("cursor")
                    else:
                        if counters["stop"] == "complete":
                            counters["stop"] = "deadline" if time.monotonic() >= deadline else "page_limit"
                    counters["pages"] = pages
                finally:
                    core.connect = original_connect

            finally:
                db.close()
        counters["seconds"] = round(time.monotonic() - started, 3)
        counters["at"] = int(time.time())
        prune_state(state, time.time())
        save_state(status_path, state)
        print(json.dumps(counters, separators=(",", ":")))
        return 0
    except Exception as exc:
        reason = exc.args[0] if isinstance(exc, RoundStop) else "round_failed"
        state["last_run"] = {"stop": reason, "seconds": round(time.monotonic() - started, 3)}
        prune_state(state, time.time())
        save_state(status_path, state)
        print(json.dumps(state["last_run"]))
        return 1
    finally:
        signal.alarm(0)
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
