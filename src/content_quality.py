"""Deterministic content eligibility, distinct from scores and access claims.

Assessments are durable source-bound receipts. This module has no filesystem,
network, provider, model or database side effects.
"""
import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup

POLICY = "reader-content-quality-v1"
ARTICLE_TYPES = {"Article", "NewsArticle", "BlogPosting", "TechArticle", "Report", "ScholarlyArticle"}
GATE_TOKENS = {"paywall", "paywall-content", "subscription-required", "subscriber-only", "paid-content"}
PAID_PROMPT = re.compile(
    r"\bsubscribe to (?:continue reading|read (?:the )?(?:full|rest of (?:the|this)) (?:article|story|post)|unlock (?:the |this )?(?:article|story|post))\b"
    r"|\b(?:this|the rest of this) (?:article|story|post) is (?:for )?(?:paid|paying|premium) (?:subscribers|members)(?: only)?\b"
    r"|(?:全文|完整正文|剩余内容)(?:需要|需|仅限)付费(?:订阅)?|付费订阅后(?:可)?(?:继续阅读|阅读全文)",
    re.I,
)
LOGIN_PROMPT = re.compile(r"\b(?:sign in|log in|register) to (?:continue reading|read (?:this|the full) (?:article|story))\b|登录后(?:可)?阅读全文", re.I)
FREE_ACCESS = re.compile(r"\b(?:free (?:account|registration|subscription)|(?:subscribe|register|sign up) for free|it(?:'s| is) free)\b|免费(?:注册|订阅|账号)", re.I)
HIGH_VALUE_SHORT = re.compile(
    r"\bCVE-\d{4}-\d+\b|\b(?:security|vulnerabilit\w*|remote code execution|data loss|data corruption|breaking change|backward.incompatible|workaround)\b"
    r"|\b(?:users? (?:must|should)|upgrade to|affects? versions?)\b|安全漏洞|数据丢失|不兼容|缓解措施|受影响版本", re.I,
)


def _url(value):
    try:
        parsed = urlsplit(value)
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.query, ""))
    except (TypeError, ValueError):
        return ""


def _articles(value):
    if isinstance(value, list):
        for item in value:
            yield from _articles(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        types = [types] if isinstance(types, str) else types
        if isinstance(types, list) and ARTICLE_TYPES.intersection(item for item in types if isinstance(item, str)):
            yield value
        # A Product/recommendation's nested Article is not the current document.
        # Root Article objects and explicit @graph nodes have separate identity.
        graph = value.get("@graph")
        if isinstance(graph, list):
            yield from _articles(graph)


def _article_matches(article, url):
    declared = article.get("url") or article.get("mainEntityOfPage") or article.get("@id")
    if isinstance(declared, dict):
        declared = declared.get("@id") or declared.get("url")
    return isinstance(declared, str) and bool(_url(declared)) and _url(declared) == _url(url)


def _access(soup, url):
    flags = set()
    canonical = {_url(link.get('href')) for link in soup.find_all('link', rel='canonical') if link.get('href')}
    identity_matches = not canonical or canonical == {_url(url)}
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text())
        except (ValueError, TypeError, RecursionError):
            continue
        for article in _articles(data):
            if identity_matches and _article_matches(article, url) and type(article.get("isAccessibleForFree")) is bool:
                flags.add(article["isAccessibleForFree"])
    paid_gate = False
    login_gate = False
    for node in soup.find_all(True):
        tokens = set(node.get("class", [])) | {str(node.get("id", "")), str(node.get("data-testid", ""))}
        if not GATE_TOKENS.intersection(tokens):
            continue
        text = node.get_text(" ", strip=True)
        free_gate = bool(FREE_ACCESS.search(text))
        paid_gate |= bool(PAID_PROMPT.search(text)) and not free_gate
        login_gate |= bool(LOGIN_PROMPT.search(text)) or free_gate
    if paid_gate:
        if True in flags:
            return 'unknown', 'conflicting_access_evidence'
        return "paid_subscription", "explicit_paid_gate"
    if flags == {False}:
        return "unknown", "publisher_nonfree_pending_review"
    if login_gate:
        return "login_required", "free_login_prompt"
    if flags == {True}:
        return "public", "article_schema_public"
    return "unknown", "conflicting_access_evidence" if len(flags) > 1 else None


def _release_information(url, html_body, text):
    soup = BeautifulSoup(html_body or text, "html.parser")
    for node in soup(["script", "style", "noscript"]):
        node.decompose()
    body = soup.get_text("\n", strip=True)
    try:
        parsed = urlsplit(url)
        release = parsed.hostname == "github.com" and bool(re.fullmatch(r"/[^/]+/[^/]+/releases/tag/[^/]+/?", parsed.path))
    except (TypeError, ValueError):
        release = False
    if not release:
        return ("substantive", None) if body else ("unknown", "source_missing")
    # This boundary is accepted only for the release template which has both
    # website/attestation labels and actual download links, never by domain alone.
    downloads = re.findall(r"https?://[^\s\"<>)]*/releases/download/[^\s\"<>)]*", html_body or text)
    platform_labels = sum(bool(re.search(label, body, re.I)) for label in
        (r'\bmacOS/iOS\s*:', r'\bLinux\s*:', r'\bAndroid\s*:', r'\bWindows\s*:', r'\bUI\s*:'))
    # Legacy source_text has neither link hrefs nor paragraph boundaries.
    flat_template = (bool(re.search(r'\bWebsite\s*:', body, re.I)) and
                     bool(re.search(r'\bAttestations\s*:', body, re.I)) and platform_labels >= 3)
    template = flat_template or (bool(downloads) and bool(re.search(r"\bWebsite\s*:|\bAttestations\s*:", body, re.I)))
    if template:
        body = re.split(r"\*{0,2}(?:Website|Attestations)\s*:", body, maxsplit=1, flags=re.I)[0]
    body = re.sub(r"(?is)\bSigned-off-by:.*$", "", body) if template else re.sub(r"(?im)^\s*Signed-off-by:.*$", "", body)
    lines = [re.sub(r"^[#*\s]+|[*\s]+$", "", line) for line in body.splitlines()]
    lines = [line for line in lines if line and line not in {"<details open>", "</details>"}]
    material = " ".join(lines).strip()
    if template and not material:
        return "low_information", "release_template_only"
    if template and len(lines) == 1 and re.search(r"\(#\d+\)$", material) and not HIGH_VALUE_SHORT.search(material):
        return "low_information", "release_subject_without_explanation"
    return ("substantive", None) if material else ("unknown", "source_missing")


def assess(*, url, html_body="", text="", extraction_state="unknown", observed_at=None):
    """Create a durable, source-bound record; presentation can project public fields.

    Caller supplies observed_at. No clock lookup or request-time state mutation.
    Failed extraction is unknown, never paid or low information by inference.
    """
    source = html_body or text
    record = {
        "policy_version": POLICY,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "observed_at": observed_at,
        "recommendation_eligible": None,
        "reason_codes": [],
        "access": "unknown",
        "information": "unknown",
    }
    if extraction_state != "available" or not source.strip():
        record["reason_codes"] = ["extraction_failed" if extraction_state == "failed" else "source_unassessed"]
        return record
    soup = BeautifulSoup(html_body or text, "html.parser")
    access, access_evidence = _access(soup, url)
    info, info_evidence = _release_information(url, html_body, text)
    record.update(access=access, information=info, recommendation_eligible=info == "substantive")
    if info == "unknown":
        record["recommendation_eligible"] = None
    if access_evidence:
        record["reason_codes"].append(access_evidence)
    if info_evidence:
        record["reason_codes"].append(info_evidence)
    if access in {"paid_fulltext", "paid_subscription"}:
        record["recommendation_eligible"] = False
    if access_evidence in {"publisher_nonfree_pending_review", "conflicting_access_evidence"}:
        record["recommendation_eligible"] = False
    if access == "login_required":
        record["recommendation_eligible"] = None
    return record


def visible_recommendation(*, state, score, minimum, quality):
    """Legacy null remains compatible; explicit false is the only new exclusion."""
    return state == "done" and score is not None and score >= minimum and quality.get("recommendation_eligible") is not False


PUBLIC_FIELDS = ('policy_version', 'recommendation_eligible', 'reason_codes', 'access', 'information')


def unknown(reason='source_unassessed'):
    return {'policy_version': POLICY, 'recommendation_eligible': None,
            'reason_codes': [reason], 'access': 'unknown', 'information': 'unknown'}


def bind(record, *, entry_id, user_id, url, content_hash, source_text):
    """Bind already assessed evidence to one existing analysis input identity."""
    return {**record, 'binding': {'entry_id': entry_id, 'user_id': user_id, 'url': url,
            'content_hash': content_hash,
            'source_text_sha256': hashlib.sha256((source_text or '').encode()).hexdigest()}}


def public_for_row(row):
    """Invalid, stale or legacy receipts are null, never reused across sources."""
    row = dict(row)
    raw = row.get('content_quality')
    if not raw:
        return unknown()
    try:
        record = json.loads(raw) if isinstance(raw, str) else raw
        binding = record['binding']
        if record.get('policy_version') != POLICY or not isinstance(binding, dict):
            return unknown('quality_unverified')
        expected = {'entry_id': row.get('entry_id'), 'user_id': row.get('user_id'),
                    'url': row.get('url'), 'content_hash': row.get('content_hash'),
                    'source_text_sha256': hashlib.sha256((row.get('source_text') or '').encode()).hexdigest()}
        if (type(binding.get('entry_id')) is not int or type(binding.get('user_id')) is not int
                or binding != expected):
            return unknown('quality_source_changed')
        eligibility = record.get('recommendation_eligible')
        if eligibility is not None and type(eligibility) is not bool:
            return unknown('quality_unverified')
        if record.get('access') not in {'public', 'paid_fulltext', 'paid_subscription', 'login_required', 'unknown'}:
            return unknown('quality_unverified')
        if record.get('information') not in {'substantive', 'low_information', 'unknown'}:
            return unknown('quality_unverified')
        reasons = record.get('reason_codes')
        if not isinstance(reasons, list) or len(reasons) > 8 or any(
                not isinstance(reason, str) or not re.fullmatch(r'[a-z_]{1,80}', reason) for reason in reasons):
            return unknown('quality_unverified')
        return {key: record[key] for key in PUBLIC_FIELDS}
    except (TypeError, ValueError, KeyError, AttributeError):
        return unknown('quality_unverified')


def serialized(record, row):
    """Serialize a receipt only after caller's owner/source CAS has succeeded."""
    row = dict(row)
    return json.dumps(bind(record, entry_id=row['entry_id'], user_id=row['user_id'],
        url=row['url'], content_hash=row.get('content_hash'), source_text=row.get('source_text')),
        ensure_ascii=False, sort_keys=True)
