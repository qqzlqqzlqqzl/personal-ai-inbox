"""Project persisted vendor health into catalog rows; no IO or collection here."""
import math
from urllib.parse import unquote, urlsplit

SUMMARY_POLICY_VERSION = "kicktraq-rss-preview-only-v1"
SUMMARY_FEEDS = frozenset({
    "https://www.kicktraq.com/categories/technology/latest.rss",
    "https://www.kicktraq.com/categories/design/latest.rss",
})


def is_kicktraq_url(value):
    try:
        host = urlsplit(value).hostname if isinstance(value, str) else None
        # Normalization is used only to reject aliases, never to authorize them.
        return isinstance(host, str) and unquote(host).rstrip('.').lower() in {"www.kicktraq.com", "kicktraq.com"}
    except ValueError:
        return False


def summary_policy_ready():
    # This is a same-release code capability, not proof that production workers
    # have restarted. Deployment still requires the independently reviewed chain.
    try:
        from feed_consumption import POLICY_VERSION, SUMMARY_FEEDS as available, summary_feed_policy
        return (POLICY_VERSION == SUMMARY_POLICY_VERSION and type(available) is frozenset and available == SUMMARY_FEEDS and
                all(summary_feed_policy({"feed": {"feed_url": url}}) == "summary_only" for url in SUMMARY_FEEDS))
    except Exception:
        return False


def annotate_subscription_support(rows, *, summary_ready):
    for row in rows:
        row["subscription_supported"] = row.get("status") == "ok" and row.get("analysis_supported") is True
        url = row.get("url")
        if not is_kicktraq_url(url):
            continue
        exact = url in SUMMARY_FEEDS
        published = exact or url in {u.replace('https:', 'http:', 1) for u in SUMMARY_FEEDS}
        row.update(provider="Kicktraq", source_kind="third_party_project_feed" if published else "unverified_source",
                   coverage="recent_category_window" if published else "unknown", rss_summary_only=published,
                   subscription_block_reason=None if exact and summary_ready is True else "source_identity_or_policy_unverified",
                   direct_kickstarter_url_available=False, analysis_supported=False,
                   summary_policy_ready=summary_ready is True and exact,
                   subscription_supported=summary_ready is True and exact and row.get("status") == "ok")


def _timestamp(value, now):
    if type(value) not in (int, float) or not 0 < value <= now or not math.isfinite(value):
        return None
    return value


def annotate_vendor_sources(rows, configs, states, *, now, interval=6 * 3600):
    # Bind only our exact configured loopback feed URLs. A matching suffix on an
    # external URL, archive page, or project URL is not a vendor feed identity.
    urls = {f"http://127.0.0.1:8092/internal/vendor-feeds/{key}.xml": key for key in configs}
    for row in rows:
        key = urls.get(row.get("url"))
        if key is None:
            continue
        cfg = configs[key]
        state = states.get(key)
        health = {
            "source_kind": "newsletter_archive" if cfg.get("kind") == "newsletter" else "official_listing",
            "coverage": "selected_newsletters" if cfg.get("kind") == "newsletter" else "column_updates",
            "freshness": "unknown", "stale": None, "last_success_at": None,
            "last_attempt_at": None, "next_run_at": None, "last_error": None,
            "state_available": False,
        }
        if cfg.get("kind") == "newsletter":
            health["project_links_available"] = False
        row["vendor_snapshot"] = health
        if not isinstance(state, dict) or state.get("state") == "invalid_state":
            continue
        if type(state.get("seeded")) is not bool:
            continue
        health["state_available"] = True
        health["last_success_at"] = _timestamp(state.get("last_success_at"), now)
        health["last_attempt_at"] = _timestamp(state.get("last_attempt_at"), now)
        next_run = state.get("next_run_at")
        if type(next_run) in (int, float) and 0 < next_run <= 253402300799 and math.isfinite(next_run):
            health["next_run_at"] = next_run
        error = state.get("last_error")
        if error is not None:
            # Persisted exception messages/types can contain arbitrary upstream
            # text. Expose only a fixed reason and bounded numeric metadata.
            error = error if isinstance(error, dict) else {}
            status = error.get("http_status")
            health["last_error"] = {
                "reason": "upstream_error",
                "at": _timestamp(error.get("at"), now),
                "http_status": status if type(status) is int and 100 <= status <= 599 else None,
            }
        if not state["seeded"]:
            if state.get("last_success_at") is not None:
                health["state_available"] = False
                health["last_success_at"] = None
            else:
                health["freshness"] = "never_collected"
        elif health["last_success_at"] is not None:
            health["stale"] = now - health["last_success_at"] >= interval
            health["freshness"] = "stale" if health["stale"] else "fresh"
