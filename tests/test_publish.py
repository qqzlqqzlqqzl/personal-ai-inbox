from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
import build_frontend as builder
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


def publish_failure_fixture(root):
    stage, live = root / "stage", root / "live"
    fixture_build(stage)
    (live / "assets").mkdir(parents=True)
    (live / "assets/new.js").write_text("previous live asset")
    (live / "assets/old.js").write_text("retained lazy chunk")
    (live / "assets/new.js.new-historical").write_text("unrelated historical temporary")
    (live / "index.html").write_text("previous live index")
    return stage, live, live / "assets/new.js.new-owned"


@pytest.mark.parametrize("phase", ["copy", "replace"])
def test_publish_failure_cleans_only_its_owned_temp(tmp_path, phase):
    stage, live, temp = publish_failure_fixture(tmp_path)
    failure = OSError("synthetic " + phase + " failure")

    def fail(source, target):
        if phase == "copy":
            Path(target).write_text("partial copy")
        raise failure

    with patch.object(builder.uuid, "uuid4", return_value=SimpleNamespace(hex="owned")), \
         patch.object(builder.shutil if phase == "copy" else builder.os,
                      "copy2" if phase == "copy" else "replace", side_effect=fail):
        try:
            publish(stage, live)
        except OSError as caught:
            assert caught is failure
        else:
            raise AssertionError("Expected the original publish failure")
    assert not temp.exists()
    assert (live / "assets/new.js").read_text() == "previous live asset"
    assert (live / "index.html").read_text() == "previous live index"
    assert (live / "assets/old.js").read_text() == "retained lazy chunk"
    assert (live / "assets/new.js.new-historical").read_text() == "unrelated historical temporary"
    assert (stage / "assets/new.js").read_text() == "new build"


@pytest.mark.parametrize("symlink", [False, True])
def test_publish_never_overwrites_or_cleans_preexisting_temp(tmp_path, symlink):
    stage, live, temp = publish_failure_fixture(tmp_path)
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("unrelated file")
    if symlink:
        temp.symlink_to(sentinel)
    else:
        temp.write_text("unrelated file")
    with patch.object(builder.uuid, "uuid4", return_value=SimpleNamespace(hex="owned")), \
         patch.object(builder.shutil, "copy2") as copy:
        try:
            publish(stage, live)
        except FileExistsError:
            pass
        else:
            raise AssertionError("Expected exclusive creation to reject the collision")
        copy.assert_not_called()
    assert temp.is_symlink() == symlink
    assert temp.read_text() == sentinel.read_text() == "unrelated file"
    assert (live / "assets/new.js").read_text() == "previous live asset"


@pytest.mark.parametrize("phase", ["copy", "replace"])
@pytest.mark.parametrize("symlink", [False, True])
def test_publish_does_not_clean_a_replacement_at_its_temp_name(tmp_path, phase, symlink):
    stage, live, temp = publish_failure_fixture(tmp_path)
    moved = temp.with_name("moved-owned-file")
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("unrelated replacement")
    failure = OSError("synthetic failure after replacement")

    def replace_then_fail(source, target):
        temp.rename(moved)
        if symlink:
            temp.symlink_to(sentinel)
        else:
            temp.write_text("unrelated replacement")
        raise failure

    with patch.object(builder.uuid, "uuid4", return_value=SimpleNamespace(hex="owned")), \
         patch.object(builder.shutil if phase == "copy" else builder.os,
                      "copy2" if phase == "copy" else "replace", side_effect=replace_then_fail):
        try:
            publish(stage, live)
        except OSError as caught:
            assert caught is failure
        else:
            raise AssertionError("Expected the original publish failure")
    assert temp.is_symlink() == symlink
    assert temp.read_text() == sentinel.read_text() == "unrelated replacement"
    assert moved.exists()
    assert (live / "assets/new.js").read_text() == "previous live asset"
    assert (live / "index.html").read_text() == "previous live index"


def test_cleanup_error_does_not_replace_the_publish_error(tmp_path):
    stage, live, temp = publish_failure_fixture(tmp_path)
    failure = OSError("original copy failure")
    with patch.object(builder.uuid, "uuid4", return_value=SimpleNamespace(hex="owned")), \
         patch.object(builder.shutil, "copy2", side_effect=failure), \
         patch.object(Path, "unlink", side_effect=PermissionError("cleanup failed")):
        try:
            publish(stage, live)
        except OSError as caught:
            assert caught is failure
        else:
            raise AssertionError("Expected the original publish failure")
    assert temp.exists()  # Failed cleanup is not reported as successful removal.
    assert (live / "assets/new.js").read_text() == "previous live asset"


def test_replaced_temp_is_not_published_even_when_copy_returns(tmp_path):
    stage, live, temp = publish_failure_fixture(tmp_path)
    moved = temp.with_name("moved-owned-file")

    def replace_temp(source, target):
        temp.rename(moved)
        temp.write_text("unrelated replacement")

    with patch.object(builder.uuid, "uuid4", return_value=SimpleNamespace(hex="owned")), \
         patch.object(builder.shutil, "copy2", side_effect=replace_temp), \
         patch.object(builder.os, "replace") as replace:
        try:
            publish(stage, live)
        except RuntimeError as caught:
            assert str(caught) == "Temporary publish file changed"
        else:
            raise AssertionError("Expected changed temporary identity to fail closed")
        replace.assert_not_called()
    assert temp.read_text() == "unrelated replacement"
    assert moved.exists()
    assert (live / "assets/new.js").read_text() == "previous live asset"
