"""Synthetic consumer contract only; no production history, fetch, or quality classifier."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import hashlib
import json
import re
import struct
import zlib

POLICY = "reader-content-quality-v1"
MINIMUM = 8
RECOMMENDED_IDS = [704, 706]
CASES = {
    701: {"key": "publisher-done", "title": "合成非免费声明 A：已有评分", "state": "done", "score": 9.1,
          "quality": {"access": "unknown", "information": "substantive", "recommendation_eligible": False,
                      "reason_codes": ["publisher_nonfree_pending_review"]},
          "required": ["发布方标注非免费，访问条件待核实", "暂不推荐", "AI 评分 9.1/10"],
          "forbidden": ["原站全文需付费", "原站需付费订阅", "原文有效信息不足"]},
    702: {"key": "publisher-unscored", "title": "合成非免费声明 B：未评分", "state": "content_excluded",
          "quality": {"access": "unknown", "information": "unknown", "recommendation_eligible": False,
                      "reason_codes": ["publisher_nonfree_pending_review"]},
          "required": ["发布方标注非免费，访问条件待核实", "暂不推荐", "未评分"],
          "forbidden": ["原站全文需付费", "原站需付费订阅", "等待后台处理", "/10"]},
    703: {"key": "low-information", "title": "合成发布条目：仅下载模板", "state": "content_excluded",
          "quality": {"access": "public", "information": "low_information", "recommendation_eligible": False,
                      "reason_codes": ["release_template_only"]},
          "required": ["原文有效信息不足", "暂不推荐", "未评分"],
          "forbidden": ["等待后台处理", "付费", "/10"]},
    704: {"key": "substantive-short", "title": "合成短文：修补越界读取", "state": "done", "score": 9.0,
          "quality": {"access": "public", "information": "substantive", "recommendation_eligible": True,
                      "reason_codes": ["substantive_short_notice"]},
          "required": ["推荐 9/10"], "forbidden": ["暂不推荐", "有效信息不足", "付费"]},
    705: {"key": "extraction-failed", "title": "合成条目：正文提取失败", "state": "fetch_error",
          "quality": {"access": "unknown", "information": "unknown", "recommendation_eligible": None,
                      "reason_codes": ["extraction_failed"]},
          "required": ["内容资格待核实", "正文提取失败，等待重试；不代表文章价值低"],
          "forbidden": ["原文有效信息不足", "暂不推荐", "原站全文需付费", "/10"]},
    706: {"key": "legacy-unassessed", "title": "合成历史评分：资格尚未评估", "state": "done", "score": 8.6,
          "quality": None, "required": ["推荐 8.6/10"],
          "forbidden": ["暂不推荐", "有效信息不足", "付费", "已验证全文"]},
    707: {"key": "explicit-paid-control", "title": "合成明确付费门槛对照", "state": "done", "score": 8.2,
          "quality": {"access": "paid_fulltext", "information": "substantive", "recommendation_eligible": False,
                      "reason_codes": ["explicit_paid_gate"]},
          "required": ["原站全文需付费", "暂不推荐", "AI 评分 8.2/10"],
          "forbidden": ["发布方标注非免费，访问条件待核实", "原文有效信息不足"]},
}


def make_entries(feed):
    # This tracked input contains selected PUBLIC metadata, not user-history/body exports.
    public = json.loads((Path(__file__).parent / "fixtures/publisher-nonfree-statements.json").read_text())
    if public["visible_payment_prompt_observed"] is not False or len(public["statements"]) != 2:
        raise ValueError("public mixed-signal fixture changed")
    result = []
    for entry_id, case in CASES.items():
        quality = deepcopy(case["quality"])
        if quality is not None:
            quality["policy_version"] = POLICY
        ai = {"state": case["state"], "has_note": False, "content_quality": quality,
              "reason": "合成既有评分说明，不代表真实用户推荐记录", "tags": ["合成"],
              "summary": "仅用于消费侧回归的合成简介", "evidence": "合成证据"}
        if case["state"] == "done":
            ai.update(score=case["score"], technical_score=8, business_score=7)
        if entry_id == 705:
            ai["processing"] = {"reason_code": "extraction_failed", "reason_codes": ["extraction_failed"], "stale": False}
        url = f"https://example.test/quality/{case['key']}"
        if entry_id in (701, 702):
            statement = public["statements"][entry_id - 701]
            if not (statement["article"]["isAccessibleForFree"] is False and
                    statement["canonical"] == statement["article"]["url"] == statement["article"]["mainEntityOfPage"]):
                raise ValueError("publisher identity contract changed")
            url = statement["canonical"]
        body = "这是合成消费侧样本。保留公开标题、简介和原文入口，不声称抓到了原站全文。"
        if entry_id == 704:
            body = "修复越界读取：请升级到安全版本，阻止恶意输入泄露内存。"
        if entry_id == 703:
            body = "合成下载条目：Website / Attestations / 下载链接。"
        result.append({"id": entry_id, "user_id": 1, "feed_id": feed["id"], "title": case["title"],
                       "url": url, "comments_url": "", "author": "Synthetic", "hash": str(entry_id),
                       "content": "<p>" + body + "</p>", "status": "read", "starred": False,
                       "published_at": "2026-10-04T01:00:00Z", "created_at": "2026-10-04T01:00:00Z",
                       "changed_at": "2026-10-04T01:00:00Z", "reading_time": 1, "enclosures": [],
                       "feed": deepcopy(feed), "ai": ai})
    return result


def validate_badge(entry_id, text):
    """Assertions on actual consumer text; used by browser and rejection controls."""
    text = " ".join(text.split())
    case = CASES[entry_id]
    for expected in case["required"]:
        if expected not in text:
            raise AssertionError(f"{case['key']}: missing {expected}")
    for unexpected in [*case["forbidden"], "$2", "两美元", "每月两美元"]:
        if unexpected in text:
            raise AssertionError(f"{case['key']}: forbidden {unexpected}")
    return text


class QualityConsumerFixture:
    """Exact small synthetic response set. Does NOT test the real backend classifier."""
    def __init__(self, entries):
        self.entries = deepcopy(entries)
        self.requests = []
        self.violations = []
        self.telemetry = []

    def select(self, query, *, ids=False):
        allowed = {"ai_view", "ai_min", "ai_sort", "status", "starred", "order", "direction", "limit",
                   "offset", "globally_visible", "published_after", "published_before", "ai_revision"}
        if set(query) - allowed or any(not isinstance(v, list) or len(v) != 1 for v in query.values()):
            raise ValueError("unsupported or repeated query")
        view = query.get("ai_view", ["all"])[0]
        if view not in ("all", "recommended"):
            raise ValueError("unsupported view")
        expected_revision = query.get("ai_revision", [None])[0]
        if expected_revision is not None and expected_revision != "initial" and not re.fullmatch(r"[0-9a-f]{64}", expected_revision):
            raise ValueError("invalid AI list revision")
        for key, values in (("ai_sort", ("score", "technical", "business", "time", "note_updated")),
                            ("order", ("published_at", "id", "created_at")), ("direction", ("asc", "desc")),
                            ("globally_visible", ("true", "false"))):
            if key in query and query[key][0] not in values:
                raise ValueError("unsupported " + key)
        status = query.get("status", [None])[0]
        starred = query.get("starred", [None])[0]
        if status not in (None, "read", "unread") or starred not in (None, "true", "false"):
            raise ValueError("unsupported filter")
        limit = int(query.get("limit", ["10000" if ids else "24"])[0])
        offset = int(query.get("offset", ["0"])[0])
        if not 1 <= limit <= (10000 if ids else 24) or not 0 <= offset <= 10000:
            raise ValueError("unbounded pagination")
        minimum = int(query.get("ai_min", [str(MINIMUM)])[0])
        if not 0 <= minimum <= 10:
            raise ValueError("invalid minimum")
        rows = [e for e in self.entries if (status is None or e["status"] == status)
                and (starred is None or e["starred"] == (starred == "true"))]
        for key, lower in (("published_after", True), ("published_before", False)):
            if key in query:
                boundary = int(query[key][0])
                if not 0 <= boundary <= 4102444800:
                    raise ValueError("invalid date boundary")
                rows = [e for e in rows if
                        (datetime.fromisoformat(e["published_at"].replace("Z", "+00:00")).timestamp() > boundary
                         if lower else datetime.fromisoformat(e["published_at"].replace("Z", "+00:00")).timestamp() < boundary)]
        if view == "recommended":
            rows = [e for e in rows if e["ai"]["state"] == "done" and e["ai"]["score"] >= minimum
                    and (e["ai"].get("content_quality") or {}).get("recommendation_eligible") is not False]
        if ids:
            rows = sorted(rows, key=lambda e: e["id"], reverse=True)
        result = {"total": len(rows)}
        if view == "recommended" and not ids and expected_revision is not None:
            # Match the real versioned-list contract, retaining legacy responses.
            scope = sorted((key, value) for key, value in query.items()
                           if key not in {"offset", "limit", "ai_revision"})
            ranking = [[e["id"], e["ai"]["score"]] for e in rows]
            revision = hashlib.sha256(json.dumps([scope, ranking], separators=(",", ":")).encode()).hexdigest()
            result["ai_revision"] = revision
            if expected_revision not in ("initial", revision):
                return {**result, "entries": [], "ai_result_changed": True}
        result["entry_ids" if ids else "entries"] = ([e["id"] for e in rows] if ids else deepcopy(rows))[offset:offset + limit]
        if not ids:
            for entry in result["entries"]:
                entry["content"] = ""
                entry["content_deferred"] = True
        return result

    def respond(self, path, method, query, body=None):
        if method == "GET" and path in ("/mf/v1/entries", "/mf/v1/entries/ids"):
            response = self.select(query, ids=path.endswith("/ids"))
            self.requests.append({"path": path, "query": deepcopy(query), "total": response["total"],
                                  "ids": response.get("entry_ids", [e["id"] for e in response.get("entries", [])])})
            return 200, response
        match = re.fullmatch(r"/mf/v1/entries/(\d+)", path)
        if method == "GET" and match:
            if query:
                raise ValueError("detail query must be empty")
            item = next((e for e in self.entries if e["id"] == int(match[1])), None)
            if item is None:
                return 404, {"error_message": "unknown synthetic entry"}
            self.requests.append({"path": path, "query": {}, "detail_id": item["id"]})
            return 200, deepcopy(item)
        if method == "POST" and path == "/mf/v1/ai/reading-session":
            if not isinstance(body, dict) or body.get("entry_id") not in CASES or body.get("action") not in ("open", "heartbeat", "close"):
                raise ValueError("unexpected telemetry")
            self.telemetry.append(deepcopy(body))
            return 200, {"ok": True}
        if method == "GET" and path == "/mf/v1/integrations/status":
            return 200, {"has_integrations": False}
        return None

    def principal_requests(self, view):
        return [r for r in self.requests if r["path"] == "/mf/v1/entries"
                and r["query"].get("ai_view", ["all"])[0] == view
                and r["query"].get("limit") == ["24"] and not r["query"].get("starred")
                and not r["query"].get("published_after") and r["query"].get("status") != ["unread"]]


# Same bounded RGB/RGBA algorithm as native_zoom_browser.surface_png; kept pure here
# because importing that browser entrypoint would execute its top-level scenario.
def decode_capture_png(data):
    """Bounded stdlib decoder for the 8-bit RGB/RGBA PNG emitted by Chromium."""
    assert len(data)<=16*1024*1024 and data[:8]==b'\x89PNG\r\n\x1a\n','invalid PNG signature/budget'
    offset=8;compressed=bytearray();size=None;ended=False
    while offset<len(data):
        assert offset+12<=len(data),'truncated PNG chunk'
        length=struct.unpack('>I',data[offset:offset+4])[0]
        kind=data[offset+4:offset+8];payload=data[offset+8:offset+8+length]
        assert offset+12+length<=len(data),'truncated PNG payload'
        crc=struct.unpack('>I',data[offset+8+length:offset+12+length])[0]
        assert zlib.crc32(kind+payload)&0xffffffff==crc,'PNG CRC mismatch'
        if kind==b'IHDR':
            assert size is None and offset==8 and length==13,'invalid IHDR'
            width,height,depth,color,method,filtering,interlace=struct.unpack('>IIBBBBB',payload)
            assert 0<width<=8192 and 0<height<=8192 and width*height<=8_000_000,'PNG dimensions exceed budget'
            assert depth==8 and color in (2,6) and (method,filtering,interlace)==(0,0,0),'unsupported PNG encoding'
            size=(width,height,3 if color==2 else 4)
        elif kind==b'IDAT':
            assert size is not None,'IDAT before IHDR'
            compressed.extend(payload)
        elif kind==b'IEND':
            assert length==0 and offset+12==len(data),'invalid IEND/trailing bytes'
            ended=True;break
        offset+=length+12
    assert ended and size and compressed,'incomplete PNG'
    width,height,channels=size;stride=width*channels;budget=(stride+1)*height
    decoder=zlib.decompressobj();raw=decoder.decompress(bytes(compressed),budget+1)
    assert len(raw)==budget and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,'PNG decoded size mismatch'
    rows=[];previous=bytes(stride)
    for y in range(height):
        start=y*(stride+1);mode=raw[start];row=bytearray(raw[start+1:start+1+stride])
        assert mode<=4,'unsupported PNG row filter'
        for x in range(stride):
            left=row[x-channels] if x>=channels else 0;up=previous[x];upper_left=previous[x-channels] if x>=channels else 0
            if mode==1:predict=left
            elif mode==2:predict=up
            elif mode==3:predict=(left+up)//2
            elif mode==4:
                p=left+up-upper_left;dist=(abs(p-left),abs(p-up),abs(p-upper_left))
                predict=(left,up,upper_left)[dist.index(min(dist))]
            else:predict=0
            row[x]=(row[x]+predict)&255
        rows.append(bytes(row));previous=row
    return {'width':width,'height':height,'channels':channels,'rows':rows}


def validate_capture_png(data, expected_size):
    image = decode_capture_png(data)
    assert (image["width"], image["height"]) == tuple(expected_size), "capture dimensions mismatch"
    channels = image["channels"]
    colors = set()
    for row in image["rows"]:
        for x in range(0, len(row), channels):
            if channels == 4 and row[x + 3] != 255:
                raise AssertionError("capture contains transparent pixels")
            colors.add(row[x:x+3])
            if len(colors) >= 32:
                break
        if len(colors) >= 32:
            break
    assert len(colors) >= 32, "capture is blank or has too few distinct colors"
    return {"width": image["width"], "height": image["height"], "distinct_colors_lower_bound": len(colors)}
