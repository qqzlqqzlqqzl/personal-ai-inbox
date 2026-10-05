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
# Coordinator-observed article gates, not a general publisher/domain rule.
ZEPHR_SUBSCRIPTION_PROMPT = re.compile(
    r"\bsubscribe\s+to\s+the\s+verge\s+to\s+continue\s+reading\b"
    r"|\bcontinue\s+reading\s+with\s+a\s+verge\s+subscription\b", re.I)
LOGIN_PROMPT = re.compile(r"\b(?:sign in|log in|register) to (?:continue reading|read (?:this|the full) (?:article|story))\b|登录后(?:可)?阅读全文", re.I)
FREE_ACCESS = re.compile(r"\b(?:free (?:account|registration|subscription)|(?:subscribe|register|sign up) for free|it(?:'s| is) free)\b|免费(?:注册|订阅|账号)", re.I)
PAID_SUBSCRIBERS = re.compile(r'\b(?:this|the rest of this) (?:article|story|post) is (?:for )?(?:paid|paying|premium) (?:subscribers|members)(?: only)?\b|(?:全文|完整正文|剩余内容)(?:需要|需|仅限)付费订阅|付费订阅后', re.I)
PAID_ARTICLE = re.compile(r'\b(?:pay to read|purchase this article|buy this article)\b|(?:全文|完整正文|剩余内容)(?:需要|需|仅限)付费(?!订阅)', re.I)


def meaningful_short(text):
    """Allow a concrete risk/mitigation or technical action/effect, not a label.

    This only permits analysis of a short captured body; it does not assign a
    score or guarantee recommendation. A generic release subject stays excluded.
    """
    text = re.sub(r'\s+', ' ', text).strip()
    risk = re.search(r'\bCVE-\d{4}-\d+\b|remote code execution|data (?:loss|corruption)|安全漏洞|数据丢失', text, re.I)
    action = re.search(r'\b(?:fix\w*|upgrad\w*|updat\w*|disable|enable|avoid|prevent\w*|affect\w*|permit\w*|workaround)\b|升级|修复|避免|受影响|缓解', text, re.I)
    if risk and action:
        return True
    instruction = re.search(r'^(?:use|call|set|enable|disable|avoid|users? (?:must|should))\b|^(?:使用|设置|禁用|启用|避免)', text, re.I)
    effect = re.search(r'\b(?:before|after|because|so that|to (?:prevent|avoid|preserve|reduce|ensure))\b|以便|确保|从而|防止', text, re.I)
    return bool(instruction and effect) or _explained_technical_change(text)


def _explained_technical_change(text):
    """A named failure/effect plus its mechanism or triggering condition.

    Release subjects need not be imperatives. Keep this exception bounded:
    generic fixes, security labels and an unexplained defect name still fail.
    This permits assessment only; access gates and score rules remain separate.
    """
    clauses = re.split(r'\b(by|when)\b', text, maxsplit=1, flags=re.I)
    if len(clauses) != 3:
        return False
    change, relation, context = clauses
    action = re.search(r'\b(?:fix(?:es|ed|ing)?|prevent(?:s|ed|ing)?|avoid(?:s|ed|ing)?|preserv(?:e|es|ed|ing))\b', change, re.I)
    outcome = re.search(r'\b(?:request smuggling|use[- ]after[- ]free|null pointer dereference|out[- ]of[- ]bounds (?:read|write)|integer precision|deadlock|crash)\b', change, re.I)
    if not (action and outcome):
        return False
    subject = re.search(r'\b(?:headers?|content-length|transfer-encoding|requests?|devices?|buffers?|length|ids?|integers?|mutex|locks?|callbacks?|pointers?|connections?|sockets?|threads?)\b', context, re.I)
    if relation.lower() == 'by':
        explanation = re.search(r'\b(?:rejecting|validating|parsing|releasing|checking|serializing|initializing|retaining|locking)\b', context, re.I)
    else:
        explanation = re.search(r'\b(?:cancel(?:s|led|ed|ling|ing)?|disconnect(?:s|ed|ing)?|clos(?:e|es|ed|ing)|expires?|exceeded|overflows?)\b', context, re.I)
    return bool(subject and explanation)


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


def _known_hidden(node):
    """Only markup/inline declarations with known visibility; no CSS guessing."""
    visibility = None
    for element in [node, *node.parents]:
        if element.name in {'template', 'noscript', 'script', 'style'} or element.has_attr('hidden'):
            return True
        declarations = {}
        for declaration in str(element.get('style', '')).split(';'):
            name, separator, value = declaration.partition(':')
            name = name.strip().lower()
            if not separator or name not in {'display', 'visibility'}:
                continue
            value = value.strip().lower()
            important = '!important' in value
            value = value.replace('!important', '').strip()
            if name not in declarations or important or not declarations[name][0]:
                declarations[name] = (important, value)
        if declarations.get('display', (False, None))[1] == 'none':
            return True
        # Visibility inherits, but a nearer explicit visible value restores a
        # descendant even when the ancestor declaration is important. Display
        # none and native hidden suppress the subtree and cannot be restored.
        value = declarations.get('visibility', (False, None))[1]
        if visibility is None and value in {'hidden', 'visible'}:
            visibility = value
    return visibility == 'hidden'


def _visible_gate_chunks(node):
    """Keep an article requirement separate from a nearby newsletter offer."""
    blocks = {'p', 'div', 'section', 'aside', 'form', 'li', 'blockquote'}
    groups = {}
    for string in node.strings:
        if _known_hidden(string.parent) or not str(string).strip():
            continue
        scope = node
        for parent in string.parents:
            if parent is node:
                break
            if parent.name in blocks:
                scope = parent
                break
        groups.setdefault(id(scope), []).append(str(string).strip())
    return [' '.join(parts) for parts in groups.values()]


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
    article_gate = False
    login_gate = False
    for node in soup.find_all(True):
        tokens = set(node.get("class", [])) | {str(node.get("id", "")), str(node.get("data-testid", ""))}
        zephr_gate = node.get('id') == 'zephr-footer-body' or (
            node.get('id') == 'zephr-inline-body' and node.parent is not None
            and node.parent.get('id') == 'zephr-inline-container')
        if not GATE_TOKENS.intersection(tokens) and not zephr_gate:
            continue
        for text in _visible_gate_chunks(node):
            free_gate = bool(FREE_ACCESS.search(text))
            # An explicit paid-subscriber statement cannot be cancelled by a
            # free newsletter or unrelated free offer, even in the same block.
            direct_article = bool(PAID_ARTICLE.search(text))
            explicit_subscription = bool(PAID_SUBSCRIBERS.search(text))
            article_gate |= direct_article
            subscription_prompt = bool(PAID_PROMPT.search(text)) or (zephr_gate and bool(ZEPHR_SUBSCRIPTION_PROMPT.search(text)))
            paid_gate |= explicit_subscription or (subscription_prompt and not free_gate and not direct_article)
            login_gate |= bool(LOGIN_PROMPT.search(text)) or free_gate
    if paid_gate or article_gate:
        if True in flags:
            return 'unknown', 'conflicting_access_evidence'
        return ('paid_subscription' if paid_gate else 'paid_fulltext'), 'explicit_paid_gate'
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
    if template and len(lines) == 1 and re.search(r"\(#\d+\)$", material) and not meaningful_short(material):
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


def public_for_row(row, current_entry=None):
    """Invalid, stale or legacy receipts are null, never reused across sources."""
    row = dict(row)
    if current_entry is not None and any(
            current_entry.get(key) != row.get(column) or (key in {'id','user_id'} and type(current_entry.get(key)) is not int)
            for key,column in (('id','entry_id'),('user_id','user_id'),('url','url'))):
        return unknown('quality_source_changed')
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
