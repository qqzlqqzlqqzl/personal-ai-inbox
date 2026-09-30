from pathlib import Path
import pytest
from build_frontend import publish, precompress, validate_reader_bundle


def fixture_build(root):
    root.mkdir()
    (root / "assets").mkdir()
    (root / "assets/new.js").write_text("new build")
    (root / "index.html").write_text('<script src="/assets/new.js"></script>')
    (root / "sw.js").write_text("new worker")


def test_publish_preserves_old_lazy_chunks(tmp_path):
    stage = tmp_path / "stage"
    live = tmp_path / "live"
    fixture_build(stage)
    live.mkdir()
    (live / "assets").mkdir()
    (live / "assets/old.js").write_text("old build")
    assert publish(stage, live) == 3
    assert (live / "assets/old.js").read_text() == "old build"
    assert (live / "assets/new.js").read_text() == "new build"
    assert "/assets/new.js" in (live / "index.html").read_text()


def test_broken_build_cannot_replace_live_index(tmp_path):
    stage = tmp_path / "stage"
    live = tmp_path / "live"
    fixture_build(stage)
    (stage / "assets/new.js").unlink()
    live.mkdir()
    (live / "index.html").write_text("working")
    with pytest.raises(ValueError):
        publish(stage, live)
    assert (live / "index.html").read_text() == "working"


def test_missing_index_rejected(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(ValueError):
        publish(stage, tmp_path / "live")


def test_publish_scoped_assets(tmp_path):
    stage, live = tmp_path / "stage", tmp_path / "build"
    fixture_build(stage)
    (stage / "index.html").write_text('<script src="/inbox/assets/new.js"></script>')
    assert publish(stage, live, "/inbox/") == 3
    assert (live / "assets/new.js").read_text() == "new build"
    assert not (live / "inbox").exists()


def test_scoped_missing_asset_cannot_replace_live(tmp_path):
    stage, live = tmp_path / "stage", tmp_path / "build"
    fixture_build(stage)
    (stage / "index.html").write_text('<script src="/inbox/assets/missing.js"></script>')
    live.mkdir()
    (live / "index.html").write_text("working")
    with pytest.raises(ValueError):
        publish(stage, live, "/inbox/")
    assert (live / "index.html").read_text() == "working"


@pytest.mark.parametrize("base", ["inbox/", "/inbox", "/../", "/inbox/../"])
def test_invalid_base_rejected(tmp_path, base):
    with pytest.raises(ValueError):
        publish(tmp_path, tmp_path / "live", base)


def test_precompresses_large_text_assets_but_not_service_worker(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "assets").mkdir()
    source = stage / "assets/app.js"
    source.write_text("const value = 'compress me';\n" * 80)
    (stage / "sw.js").write_text("self.skipWaiting();\n" * 80)
    assert precompress(stage) == 1
    gz = stage / "assets/app.js.gz"
    assert gz.is_file() and gz.stat().st_size < source.stat().st_size
    assert not (stage / "sw.js.gz").exists()


def test_reader_bundle_validation_accepts_current_semantics(tmp_path):
    stage = tmp_path / "stage"
    (stage / "assets").mkdir(parents=True)
    (stage / "assets/app.js").write_text("资源看板 AI 设置 · 来源")
    validate_reader_bundle(stage)


@pytest.mark.parametrize("stale", [
    "评分是模型判断",
    "依据抓取的原网页文本",
    "模型输入 3011 字符",
    "图片保留不代表模型理解了图片内容",
])
def test_reader_bundle_validation_rejects_stale_detail_copy(tmp_path, stale):
    stage = tmp_path / "stage"
    (stage / "assets").mkdir(parents=True)
    (stage / "assets/app.js").write_text("资源看板 AI 设置 · 来源 " + stale)
    with pytest.raises(ValueError):
        validate_reader_bundle(stage)


def test_reader_bundle_validation_requires_dashboard(tmp_path):
    stage = tmp_path / "stage"
    (stage / "assets").mkdir(parents=True)
    (stage / "assets/app.js").write_text("AI 设置 · 来源")
    with pytest.raises(ValueError):
        validate_reader_bundle(stage)
