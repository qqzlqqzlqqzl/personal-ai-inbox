from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text()


def test_ai_detail_hides_internal_scoring_metadata():
    badge = read("patches/AiBadge.jsx")
    for text in ("模型输入", "正文包含", "依据抓取的原网页文本", "评分是模型判断"):
        assert text not in badge
    assert "ai.summary" in badge and "ai.evidence" in badge


def test_runtime_dashboard_is_reader_facing():
    panel = read("patches/AiPanel.jsx")
    for text in ("资源看板", "已收录文章", "订阅来源", "AI 已完成", "长正文已抓取", "磁盘已用", "内存已用", "分析数据库", "Kaggle 计算资源", "GPU 周额度每 5 分钟缓存刷新", "详细诊断"):
        assert text in panel


def test_reader_quality_patch_targets_inline_code_and_only_safe_proxy_paths():
    patch = read("src/patch_reader_detail_quality.py")
    assert ".article-content :not(pre) > code" in patch
    assert 'url.startsWith("/mf/proxy/")' in patch
    assert 'sameOriginProxy ? "https"' in patch
    assert "data:" not in patch
    assert "javascript:" not in patch


def test_frontend_build_runs_reader_quality_overlay():
    build = read("src/build_frontend.py")
    assert "patch_reader_detail_quality.py" in build
