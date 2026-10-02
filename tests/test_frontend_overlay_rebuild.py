"""Rebuild the overlay twice from the exact pinned upstream, in temporary files."""
import hashlib
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from patch_interaction_review import install

ROOT = Path(__file__).resolve().parents[1]
PIN = "534eeb97723ac11025de4ec1ac56335072e3be52"
PRODUCTION_BASELINE = "da5a6222779517b92782bf19e2a2166801676fb4"
SORTER = Path("src/components/Article/SearchAndSortBar.jsx")
STAGES = (
    "patch_frontend.py", "polish_frontend.py", "specialize_login.py",
    "patch_reading_session.py", "patch_ui_review.py", "patch_article_notes.py",
    "patch_reading_telemetry.py", "patch_scope_ai_filters.py",
    "patch_reader_detail_quality.py", "patch_reader_entry_defaults.py", "patch_interaction_review.py",
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
    shutil.copytree(ROOT / "frontend-review", isolated / "frontend-review")
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
    assert sorter.count('  invalidateArticleList,') == 1
    assert sorter.count('    invalidateArticleList()') == 1
    assert sorter.count('event.isComposing || event.nativeEvent?.isComposing || event.keyCode === 229') == 1
    assert 'if (event.key === "Enter") {\n      handleConfirm()' in sorter
    assert sorter.count('searchOpenerRef.current = document.activeElement') == 1
    assert sorter.count('fallbackFocusSelector=".reader-search-trigger"') == 1
    assert sorter.count('returnFocusRef={searchOpenerRef}') == 1
    assert 'aria-label={tooltip}\n      className="reader-search-trigger"' in sorter
    assert 'title={tooltip}' in sorter
    assert '<CustomTooltip mini content={selectDateLabel}>' in sorter
    assert 'search.syntax_help' in sorter
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


def _pinned_checkout(isolated):
    upstream = ROOT / "upstream/reactflux"
    subprocess.run(["git", "-C", str(upstream), "cat-file", "-e", PIN + "^{commit}"], check=True)
    archive = subprocess.check_output([
        "git", "-C", str(upstream), "archive", "--format=tar", "--prefix=reactflux/", PIN,
    ])
    web = isolated / "upstream/reactflux"
    web.parent.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(web.parent, filter="data")
    (web / "UPSTREAM_REVISION").write_text(PIN + "\n")
    (isolated / "runtime").mkdir()
    (isolated / "runtime/reactflux.tar.gz").write_bytes(archive)
    return web


def _historical_authoring(isolated):
    # Exact public source provenance, never copied from a deployment or runtime.
    archive = subprocess.check_output([
        "git", "-C", str(ROOT), "archive", "--format=tar", PRODUCTION_BASELINE,
        "src", "patches", "frontend-review",
    ])
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(isolated, filter="data")


def _apply_authoring(isolated, stages=STAGES):
    for name in stages:
        script = isolated / "src" / name
        code = script.read_text().replace("/home/ubuntu/ai-news", str(isolated))
        exec(compile(code, str(script), "exec"), {"__name__": "__main__", "__file__": str(script)})  # noqa: S102


def _source_bytes(web):
    return {str(path.relative_to(web)): path.read_bytes() for path in web.rglob("*") if path.is_file()}


def test_public_production_baseline_upgrade_and_unknown_drift(tmp_path, monkeypatch):
    """Keep the old generated tree/backups; only replace tracked authoring files.

    A pristine public reconstruction cannot stand in for an independently captured
    production input. This scenario covers the reproducible public da5 baseline.
    """
    isolated = tmp_path / "upgrade"
    web = _pinned_checkout(isolated)
    _historical_authoring(isolated)
    monkeypatch.setenv("AI_NEWS_ROOT", str(isolated))
    _apply_authoring(isolated)
    assert hashlib.sha256((web / SORTER).read_bytes()).hexdigest() == (
        "91b9d316610966db26b5cb5e6d1278ed0b94f53bbe397c5d19955352d21a3734"
    )
    backups = _source_bytes(isolated / "runtime/reactflux-original")
    for name in ("src", "patches", "frontend-review"):
        shutil.rmtree(isolated / name)
        shutil.copytree(ROOT / name, isolated / name)
    _apply_authoring(isolated, STAGES[:-1])
    before = (isolated / "frontend-review/before" / SORTER).read_bytes()
    assert (web / SORTER).read_bytes() == before
    # Preserve every original backup: an upgrade must not recreate the tree.
    for name, data in backups.items():
        assert (isolated / "runtime/reactflux-original" / name).read_bytes() == data

    # An unknown variation is not an approved historical overlay. The final
    # installer must refuse all writes, including files ordered before Search.
    (web / SORTER).write_bytes(before + b"\n// unreviewed drift\n")
    drifted = _source_bytes(web)
    with pytest.raises(RuntimeError, match="Unreviewed source drift, refusing overwrite: .*SearchAndSortBar"):
        install(isolated)
    assert _source_bytes(web) == drifted
    (web / SORTER).write_bytes(before)
    _apply_authoring(isolated, STAGES[-1:])
    assert (web / SORTER).read_bytes() == (ROOT / "frontend-review/after" / SORTER).read_bytes()
    upgraded = _source_bytes(web)
    _apply_authoring(isolated)
    assert _source_bytes(web) == upgraded

    pristine = tmp_path / "target-pristine"
    pristine_web = _pinned_checkout(pristine)
    for name in ("src", "patches", "frontend-review"):
        shutil.copytree(ROOT / name, pristine / name)
    monkeypatch.setenv("AI_NEWS_ROOT", str(pristine))
    _apply_authoring(pristine)
    assert _source_bytes(pristine_web) == upgraded
