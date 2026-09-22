"""X connectivity and real timeline preflight; never equate configuration with posts."""
import asyncio, re, time, xml.etree.ElementTree as ET
import httpx
from initialize_secrets import read_env

def handle(value):
    value = str(value or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", value):
        raise ValueError("请输入 X 用户名，例如 @OpenAI")
    return value

async def probe(value):
    name = handle(value)
    cfg = read_env("rsshub.env")
    third = bool(cfg.get("TWITTER_THIRD_PARTY_API"))
    configured = third or bool(cfg.get("TWITTER_AUTH_TOKEN")) or bool(cfg.get("TWITTER_CONSUMER_KEY") and cfg.get("TWITTER_CONSUMER_SECRET"))
    result = {"at":time.time(), "handle":name, "adapter_configured":configured,
              "adapter":"third_party" if third else "account" if configured else "unconfigured",
              "network_reachable":False, "posts_returned":False, "post_count":0,
              "route":"/twitter/user/"+name, "experimental":True}
    async with httpx.AsyncClient(timeout=12,follow_redirects=True,trust_env=False) as c:
        async def network():
            try:
                async with c.stream("GET", "https://x.com/"+name) as r:
                    result.update(network_reachable=True,network_http=r.status_code)
            except httpx.HTTPError as e:
                result["network_error"]=type(e).__name__
        async def timeline():
            try:
                async with c.stream("GET","http://127.0.0.1:1200"+result["route"]) as r:
                    result["route_http"]=r.status_code
                    if r.status_code!=200: return
                    body=b""
                    async for chunk in r.aiter_bytes():
                        body+=chunk
                        if len(body)>2*1024*1024: return
                root=ET.fromstring(body)
                valid=[i for i in root.findall("./channel/item") if re.search(r"https://(?:x|twitter)\.com/[^/]+/status/\d+", i.findtext("link") or "")]
                result.update(post_count=len(valid),posts_returned=bool(valid))
            except (httpx.HTTPError,ET.ParseError) as e:
                result["route_error"]=type(e).__name__
        try:
            await asyncio.wait_for(asyncio.gather(network(),timeline()),timeout=18)
        except asyncio.TimeoutError:
            result["probe_timeout"]=True
    if result["posts_returned"]:
        result["message"]="本次已取得公开帖子，可尝试订阅；长期稳定性仍待验证。"
    elif not configured:
        result["message"]="X 适配器未配置。可接第三方服务以免提供 X 账号 Token；尚未取得帖子。"
    else:
        result["message"]="适配器已配置，但本次未取得帖子，未创建订阅。"
    if not result["network_reachable"]:
        result["message"] += " 服务器直连 X 不通；直接抓取需要可用出站代理。"
    return result
