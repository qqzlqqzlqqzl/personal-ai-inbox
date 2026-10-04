"""Fixed feed identity restrictions, independent of project URL or catalog state.

The two HTTPS endpoints were captured as published Kicktraq RSS. Their HTTP
atom:self identities are observed but not authorized aliases: keep those closed
pending confirmation of the actual stored Miniflux feed URL. No wildcard/domain
classification or URL rewriting is used here.
"""
SUMMARY_FEEDS = frozenset({
    "https://www.kicktraq.com/categories/technology/latest.rss",
    "https://www.kicktraq.com/categories/design/latest.rss",
})
UNVERIFIED_SELF_FEEDS = frozenset(url.replace("https://", "http://", 1) for url in SUMMARY_FEEDS)
SUMMARY_SOURCE = "kicktraq_rss_summary"


def summary_feed_policy(entry):
    feed = entry.get("feed")
    value = feed.get("feed_url") if isinstance(feed, dict) else None
    if not isinstance(value, str):
        return None
    if value in SUMMARY_FEEDS:
        return "summary_only"
    if value in UNVERIFIED_SELF_FEEDS:
        return "identity_unverified"
    return None


def is_summary_only_feed(entry):
    return summary_feed_policy(entry) == "summary_only"


def restricted_analysis_fields(policy):
    if policy == "summary_only":
        return {"state": "requires_fulltext_adapter", "content_source": SUMMARY_SOURCE,
                "error": "Kicktraq RSS 摘要；未接入项目全文，不生成全文价值评分"}
    if policy == "identity_unverified":
        return {"state": "requires_source_review", "content_source": "unverified_rss_feed",
                "error": "Kicktraq RSS HTTP self 身份待核实；禁止扩展抓取"}
    raise ValueError("Unknown feed restriction")
