"""Aggregate one authorized, body-free AI sidebar snapshot without side effects."""
import json
import math
from datetime import datetime
import notes_metadata


def validate_params(params):
    if params.get("ai_view") not in {"recommended", "pending", "notes"}:
        raise ValueError("Invalid AI view")
    if params.get("status") not in {None, "read", "unread"}:
        raise ValueError("Invalid status")
    if params.get("starred") not in {None, "true", "false"}:
        raise ValueError("Invalid starred filter")
    if params.get("has_note") not in {None, "true", "false"}:
        raise ValueError("Invalid note filter")
    # Today is an account-calendar window, never an All-scope date predicate.
    int(params["today_after"])
    for key in ("today_after", "date_after", "date_before", "published_after",
                "published_before", "after", "before"):
        if params.get(key) is not None:
            value = int(params[key])
            if not -(2**63) <= value < 2**63:
                raise ValueError("Invalid date filter")
    dated = params.get("date_after") is not None or params.get("date_before") is not None
    if dated and (params.get("date_after") is None or params.get("date_before") is None
                  or params.get("date_field") not in {"published_at", "changed_at"}
                  or int(params["date_after"]) > int(params["date_before"])):
        raise ValueError("Invalid sidebar date filter")
    notes_metadata.changed_bounds(params)


def decode_id_page(content, offset, seen, expected_total=None):
    data = json.loads(content)
    if not isinstance(data, dict) or set(data) != {"entry_ids", "total"}:
        raise ValueError("Invalid readable-ID envelope")
    ids, total = data["entry_ids"], data["total"]
    if (not isinstance(ids, list) or type(total) is not int or total < 0
            or len(ids) > notes_metadata.MAX_IDS
            or any(type(eid) is not int or not 0 < eid < 2**63 for eid in ids)
            or len(set(ids)) != len(ids) or seen.intersection(ids)
            or offset + len(ids) > total or (not ids and offset < total)
            or (expected_total is not None and total != expected_total)):
        raise ValueError("Incomplete or changing readable-ID snapshot")
    return ids, total


def decode_categories(content, uid):
    rows = json.loads(content)
    if not isinstance(rows, list):
        raise ValueError("Invalid category snapshot")
    categories = {}
    for row in rows:
        if (not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0
                or row["id"] in categories or type(row.get("user_id")) is not int
                or row["user_id"] != uid or type(row.get("hide_globally")) is not bool):
            raise ValueError("Invalid or unowned category")
        categories[row["id"]] = row
    return categories


def aggregate(entries, feeds, categories, candidates, main_ids, history_ids, starred_ids, params):
    validate_params(params)
    result = {"all": 0, "today": 0, "starred": 0, "history": 0,
              "category": {str(cid): 0 for cid in categories},
              "feed": {str(fid): 0 for fid in feeds}}
    changed = notes_metadata.changed_bounds(params)
    published = [(op, int(params[key])) for key, op in
                 (("published_after", ">="), ("published_before", "<="),
                  ("after", ">="), ("before", "<=")) if params.get(key)]
    term = (params.get("search") or "")[:200].casefold()
    publication = {}
    for entry in entries:
        value = entry.get("published_at")
        if value in (None, ""):
            publication[entry["id"]] = 0.0
            continue
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None or date.utcoffset() is None or not math.isfinite(date.timestamp()):
            raise ValueError("Invalid publication metadata")
        publication[entry["id"]] = date.timestamp()

    def matches_date(entry, scope):
        if scope == "today":
            return publication[entry["id"]] >= int(params["today_after"])
        if params.get("date_after") is None:
            return True
        field = "changed_at" if scope in {"starred", "history"} else params["date_field"]
        stamp = (notes_metadata.changed_timestamp(entry) if field == "changed_at"
                 else publication[entry["id"]])
        lower, upper = int(params["date_after"]), int(params["date_before"])
        return lower < stamp < upper if field == "changed_at" else lower <= stamp <= upper

    for entry in entries:
        eid, fid = entry["id"], entry["feed_id"]
        if fid not in feeds or feeds[fid]["category"]["id"] not in categories:
            raise ValueError("Entry/feed/category snapshot changed")
        cid = feeds[fid]["category"]["id"]
        if any((op == ">=" and publication[eid] < bound)
               or (op == "<=" and publication[eid] > bound)
               for op, bound in published) or not notes_metadata.matches_changed(entry, changed):
            continue
        if params["ai_view"] == "notes" and term:
            if term not in entry["title"].casefold() and term not in candidates[eid]["note"].casefold():
                continue
        visible = params.get("globally_visible") != "true" or not (
            feeds[fid]["hide_globally"] or categories[cid]["hide_globally"])
        if eid in main_ids:
            if matches_date(entry, "all"):
                result["feed"][str(fid)] += 1
                result["category"][str(cid)] += 1
                if visible:
                    result["all"] += 1
            if visible and matches_date(entry, "today"):
                result["today"] += 1
        if visible and eid in starred_ids and matches_date(entry, "starred"):
            result["starred"] += 1
        if visible and eid in history_ids and matches_date(entry, "history"):
            result["history"] += 1
    return result
