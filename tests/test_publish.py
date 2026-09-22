from pathlib import Path
import pytest
from build_frontend import publish


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
