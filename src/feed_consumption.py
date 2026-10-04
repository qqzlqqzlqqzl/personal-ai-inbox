"""Fixed feed identity restrictions, independent of project URL or catalog state.

The two HTTPS endpoints were captured as published Kicktraq RSS. Their HTTP
atom:self identities are observed but not authorized aliases: keep those closed
pending confirmation of the actual stored Miniflux feed URL. No wildcard/domain
classification or URL rewriting is used here.
"""
POLICY_VERSION = "kicktraq-rss-preview-only-v1"
SUMMARY_FEEDS = frozenset({
    "https://www.kicktraq.com/categories/technology/latest.rss",
    "https://www.kicktraq.com/categories/design/latest.rss",
})
UNVERIFIED_SELF_FEEDS = frozenset(url.replace("https://", "http://", 1) for url in SUMMARY_FEEDS)
PREVIEW_IMAGE_SOURCE = "cached_entry_image"
RSS_SUMMARY_ERROR = "rss_summary_only:" + POLICY_VERSION
UNVERIFIED_FEED_ERROR = "rss_feed_identity_unverified:" + POLICY_VERSION
RESTRICTED_ERRORS = (RSS_SUMMARY_ERROR, UNVERIFIED_FEED_ERROR)


def restricted_analysis_reason(error):
    # Exact persisted restriction markers only. Natural-language errors, URL
    # mentions and unknown versions must not classify other sources as these feeds.
    if error == RSS_SUMMARY_ERROR:
        return "rss_summary_only"
    if error == UNVERIFIED_FEED_ERROR:
        return "rss_feed_identity_unverified"
    return None


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
        return {"state": "requires_fulltext_adapter",
                "error": RSS_SUMMARY_ERROR}
    if policy == "identity_unverified":
        return {"state": "requires_source_review",
                "error": UNVERIFIED_FEED_ERROR}
    raise ValueError("Unknown feed restriction")
