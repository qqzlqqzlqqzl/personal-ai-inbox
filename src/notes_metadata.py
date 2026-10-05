"""Request-local, body-free notes selection. Run these helpers in ReaderWorkPool."""
import json
import math
from datetime import datetime

MAX_IDS = 10000
MAX_REQUEST_BYTES = 256 << 10
FIELDS = {"id", "user_id", "feed_id", "title", "url", "published_at"}


def changed_bounds(params):
    """Miniflux 2.3.3 applies positive Unix-second bounds strictly to changed_at."""
    bounds = []
    for key, op in (("changed_after", ">"), ("changed_before", "<")):
        if params.get(key):
            value = int(params[key])
            if not -(2**63) <= value < 2**63:
                raise ValueError("Invalid changed date filter")
            if value > 0:
                bounds.append((op, value))
    return bounds


def changed_timestamp(entry):
    value = entry.get("changed_at")
    if not isinstance(value, str):
        raise ValueError("missing changed_at metadata")
    date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if date.tzinfo is None or date.utcoffset() is None:
        raise ValueError("changed_at must include a timezone")
    stamp = date.timestamp()
    if not math.isfinite(stamp):
        raise ValueError("invalid changed_at metadata")
    return stamp


def matches_changed(entry, bounds):
    if not bounds:
        return True
    stamp = changed_timestamp(entry)
    return all(stamp > bound if op == ">" else stamp < bound for op, bound in bounds)


def published_timestamp(entry):
    value = entry.get("published_at")
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return 0.0


def request_body(notes, allowed_ids):
    ids = sorted(row["entry_id"] for row in notes if row["entry_id"] in allowed_ids)
    if len(ids) > MAX_IDS:
        raise ValueError("notes metadata candidate limit exceeded (10000)")
    if any(type(eid) is not int or not 0 < eid < 2**63 for eid in ids):
        raise ValueError("invalid noted entry ID")
    body = json.dumps({"entry_ids": ids}, separators=(",", ":")).encode()
    if len(body) > MAX_REQUEST_BYTES:
        raise ValueError("notes metadata request limit exceeded (256 KiB)")
    return ids, body


def decode_metadata(content, uid, ids, *, require_changed=False):
    data = json.loads(content)
    if not isinstance(data, dict) or set(data) != {"entries"} or not isinstance(data["entries"], list):
        raise ValueError("invalid notes metadata envelope")
    allowed, seen, entries = set(ids), set(), []
    for entry in data["entries"]:
        if (not isinstance(entry, dict) or set(entry) not in (FIELDS, FIELDS | {"changed_at"})
                or any(type(entry.get(k)) is not int or not 0 < entry[k] < 2**63
                       for k in ("id", "user_id", "feed_id"))
                or entry["user_id"] != uid or entry["id"] not in allowed
                or entry["id"] in seen or not isinstance(entry["title"], str)
                or not isinstance(entry.get("url"), str) or not entry["url"].strip()
                or not isinstance(entry["published_at"], (str, type(None)))):
            raise ValueError("invalid or unscoped notes metadata row")
        if require_changed or "changed_at" in entry:
            changed_timestamp(entry)
        seen.add(entry["id"])
        entries.append(entry)
    return entries


def decode_feeds(content, uid):
    feeds = json.loads(content)
    if not isinstance(feeds, list):
        raise ValueError("invalid notes feed visibility")
    result = {}
    for feed in feeds:
        if (not isinstance(feed, dict) or type(feed.get("id")) is not int
                or feed["id"] <= 0 or feed["id"] in result
                or type(feed.get("user_id")) is not int or feed["user_id"] != uid
                or type(feed.get("hide_globally")) is not bool
                or not isinstance(feed.get("category"), dict)):
            raise ValueError("invalid or unscoped notes feed visibility")
        category = feed["category"]
        if (type(category.get("user_id")) is not int or category["user_id"] != uid
                or type(category.get("id")) is not int or category["id"] <= 0
                or type(category.get("hide_globally")) is not bool):
            raise ValueError("invalid notes category ownership")
        result[feed["id"]] = feed
    return result


def select_page(notes, entries, feeds, params, *, feed_id=None, category_id=None):
    """Keep inclusive publication bounds and strict native changed-at bounds separate."""
    limit = max(1, min(100, int(params.get("limit", 40))))
    offset = max(0, int(params.get("offset", 0)))
    direction = params.get("direction", "desc")
    sort_key = params.get("ai_sort") or "note_updated"
    if direction not in {"asc", "desc"} or sort_key not in {"note_updated", "time"}:
        raise ValueError("Invalid sort field or direction")
    bounds = [(op, int(params[key]))
              for key, op in [("published_after", ">="), ("published_before", "<="),
                              ("after", ">="), ("before", "<=")]
              if params.get(key)]
    changed = changed_bounds(params)
    hidden = {fid for fid, feed in feeds.items()
              if feed.get("hide_globally") or (feed.get("category") or {}).get("hide_globally")}
    scope = None
    if feed_id is not None:
        scope = {int(feed_id)}
    elif category_id is not None:
        scope = {fid for fid, feed in feeds.items()
                 if int((feed.get("category") or {}).get("id", 0)) == int(category_id)}
    by_id = {row["entry_id"]: row for row in notes}
    term = (params.get("search") or "")[:200].casefold()
    filtered = []
    for entry in entries:
        row = by_id[entry["id"]]
        current_feed = entry["feed_id"]
        if scope is not None and current_feed not in scope:
            continue
        if params.get("globally_visible") == "true" and current_feed in hidden:
            continue
        published = published_timestamp(entry)
        if any((op == ">=" and published < bound) or (op == "<=" and published > bound)
               for op, bound in bounds):
            continue
        if not matches_changed(entry, changed):
            continue
        if term and term not in entry["title"].casefold() and term not in row["note"].casefold():
            continue
        filtered.append((row, entry, published))
    filtered.sort(key=lambda item: (float(item[0]["updated_at"]) if sort_key == "note_updated"
                                   else item[2], int(item[1]["id"])), reverse=direction == "desc")
    return len(filtered), [entry for _, entry, _ in filtered[offset:offset + limit]]


def body_matches(entry, metadata, feeds, params):
    """Detect selected-row churn before returning a stale selection/total."""
    feed_id = int(entry.get("feed_id") or (entry.get("feed") or {}).get("id", 0))
    if changed_bounds(params) and changed_timestamp(entry) != changed_timestamp(metadata):
        return False
    if (entry.get("title", "") != metadata["title"] or feed_id != metadata["feed_id"]
            or entry.get("url") != metadata["url"]
            or published_timestamp(entry) != published_timestamp(metadata)
            or entry.get("status") not in {"read", "unread"}
            or (params.get("status") and entry["status"] != params["status"])
            or (params.get("starred") in {"true", "false"}
                and entry.get("starred") != (params["starred"] == "true"))):
        return False
    # Entry and feed enumeration are separate snapshots. Compare available parent
    # facts too; Miniflux's full entry includes these, synthetic legacy rows may not.
    current, selected = entry.get("feed") or {}, feeds.get(feed_id, {})
    for key in ("hide_globally", "user_id"):
        if key in current and current[key] != selected.get(key, False if key == "hide_globally" else entry.get("user_id")):
            return False
    category, old_category = current.get("category") or {}, selected.get("category") or {}
    for key in ("id", "hide_globally"):
        if key in category and category[key] != old_category.get(key, False if key == "hide_globally" else 0):
            return False
    return True
