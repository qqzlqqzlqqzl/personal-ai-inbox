import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import core


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB", tmp_path / "analysis.sqlite3")
    core.init_db()
    core.init_usage()
    core.migrate()
    return tmp_path


@pytest.fixture
def entry():
    return {
        "id": 1,
        "user_id": 1,
        "title": "An engineering case study",
        "url": "https://example.org/article",
        "feed_id": 1,
        "published_at": "2026-09-22T00:00:00Z",
        "status": "unread",
        "starred": False,
        "content": "<p>Only a teaser.</p>",
        "feed": {"id": 1, "title": "Example", "feed_url": "https://example.org/feed"},
    }


@pytest.fixture
def full_html():
    return (
        "<h1>Engineering case</h1><p>"
        + ("The prototype reduces latency by using a local cache. " * 8)
        + '</p><img src="https://example.org/image.jpg">'
    )


@pytest.fixture
def model_result():
    return {
        "summary": "使用本地缓存减少延迟的工程案例。",
        "technical_score": 8,
        "business_score": 5,
        "score": 8,
        "worth_reading": True,
        "reason": "正文给出缓存实现，可参考工程取舍。",
        "tags": ["缓存", "工程"],
        "content_type": "案例",
        "evidence": "The prototype reduces latency by using a local cache.",
    }
