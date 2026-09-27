"""X preflight through the local x-cli guest provider and Atom adapter."""
import re, time, xml.etree.ElementTree as ET
import httpx

PROVIDER = "http://127.0.0.1:17910"
FEEDS = "http://127.0.0.1:17911"

def handle(value):
    value = str(value or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", value):
        raise ValueError("请输入 X 用户名，例如 @OpenAI")
    return value

async def probe(value):
    name = handle(value)
    feed_url = f"{FEEDS}/x/user/{name}"
    result = {
        "at": time.time(),
        "handle": name,
        "adapter_configured": True,
        "adapter": "x_cli_guest",
        "network_reachable": False,
        "profile_valid": False,
        "feed_ready": False,
        "posts_returned": False,
        "post_count": 0,
        "route": f"/x/user/{name}",
        "feed_url": feed_url,
        "experimental": False,
    }
    async with httpx.AsyncClient(timeout=35, follow_redirects=False, trust_env=False) as client:
        try:
            profile = await client.get(f"{PROVIDER}/v1/user/{name}")
            result["profile_http"] = profile.status_code
            result["profile_valid"] = profile.status_code == 200
            result["network_reachable"] = profile.status_code in (200, 404)
        except httpx.HTTPError as exc:
            result["profile_error"] = type(exc).__name__
        try:
            feed = await client.get(feed_url)
            result["feed_http"] = feed.status_code
            if feed.status_code == 200:
                root = ET.fromstring(feed.content)
                ns = {"a": "http://www.w3.org/2005/Atom"}
                entries = root.findall("a:entry", ns)
                result.update(feed_ready=True, post_count=len(entries), posts_returned=bool(entries))
                result["feed_source"] = feed.headers.get("x-x-feed-source")
        except (httpx.HTTPError, ET.ParseError) as exc:
            result["feed_error"] = type(exc).__name__

    if result["posts_returned"]:
        result["message"] = f"X guest Provider 正常，本次取得 {result['post_count']} 条公开帖子，可直接订阅。"
    elif result["feed_ready"] and result["profile_valid"]:
        result["message"] = "X guest Provider 正常；账号有效，但当前时间线窗口为空或处于限流缓存期，订阅仍可创建。"
    elif not result["profile_valid"]:
        result["message"] = "未能从 X guest Provider 验证该账号，暂不创建 X 订阅。"
    else:
        result["message"] = "X guest Provider 暂不可用，请查看本地 Provider 健康状态。"
    return result
