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
