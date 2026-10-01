"""Rebuild the overlay twice from the exact pinned upstream, in temporary files."""
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PIN = "534eeb97723ac11025de4ec1ac56335072e3be52"
STAGES = (
    "patch_frontend.py", "polish_frontend.py", "specialize_login.py",
    "patch_reading_session.py", "patch_ui_review.py", "patch_article_notes.py",
    "patch_reading_telemetry.py", "patch_scope_ai_filters.py",
    "patch_reader_detail_quality.py", "patch_reader_entry_defaults.py",
)


def test_pristine_pinned_overlay_and_repeat_are_equivalent(tmp_path, monkeypatch):
    upstream = ROOT / "upstream/reactflux"
    if not (upstream / ".git").exists():
        pytest.skip("Requires the documented pinned ReactFlux checkout; no network fetch in tests")
    subprocess.run(["git", "-C", str(upstream), "cat-file", "-e", PIN + "^{commit}"], check=True)
    archive = subprocess.check_output([
        "git", "-C", str(upstream), "archive", "--format=tar", "--prefix=reactflux/", PIN,
    ])
    isolated = tmp_path / "rebuild"
    web = isolated / "upstream/reactflux"
    web.parent.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(web.parent, filter="data")
    (web / "UPSTREAM_REVISION").write_text(PIN + "\n")
    (isolated / "runtime").mkdir()
    # polish_frontend uses this same pristine reference, not a live deployment.
    (isolated / "runtime/reactflux.tar.gz").write_bytes(archive)
    shutil.copytree(ROOT / "patches", isolated / "patches")
    monkeypatch.setenv("AI_NEWS_ROOT", str(isolated))

    def apply():
        for name in STAGES:
            script = ROOT / "src" / name
            code = script.read_text().replace("/home/ubuntu/ai-news", str(isolated))
            # Execute trusted checked-in scripts only in the isolated temporary checkout.
            exec(compile(code, str(script), "exec"), {"__name__": "__main__", "__file__": str(script)})  # noqa: S102

    apply()
    sorter = (web / "src/components/Article/SearchAndSortBar.jsx").read_text()
    assert sorter.count('aria-label="排序方式"') == 1
    assert sorter.count('event.isComposing || event.nativeEvent?.isComposing || event.keyCode === 229') == 1
    assert 'if (event.key === "Enter") {\n      handleConfirm()' in sorter
    assert sorter.count('searchOpenerRef.current = document.activeElement') == 1
    assert sorter.count('fallbackFocusSelector=".reader-search-trigger"') == 1
    assert sorter.count('returnFocusRef={searchOpenerRef}') == 1
    assert 'const sortDirection = aiList ? (ai.direction || "desc") : orderDirection' in sorter
    assert 'const { orderBy, orderDirection }' in sorter
    assert 'const displayTitle = title && aiModeLabel' in sorter
    assert 'const scoreOrder =' not in sorter
    assert 'sortLabel' not in sorter
    assert 'IconSortAscending' not in sorter
    assert 'toggleOrderDirection' not in sorter
    assert '笔记更新时间：新到旧' in sorter
    assert 'contentState.setKey("articleListOffset", response.entries.length)' in (web / "src/hooks/useArticleList.js").read_text()
    assert (web / "src/components/Ai/SourceHistory.jsx").is_file()
    assert (web / "src/components/Ai/source-history.js").is_file()
    first = {str(path.relative_to(web)): path.read_bytes() for path in web.rglob("*") if path.is_file()}
    apply()
    second = {str(path.relative_to(web)): path.read_bytes() for path in web.rglob("*") if path.is_file()}
    assert second == first
