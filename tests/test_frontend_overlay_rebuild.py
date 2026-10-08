"""Rebuild the overlay twice from the exact pinned upstream, in temporary files."""
import ast
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


NATIVE_LOAD_MORE = '  const handleLoadMore = async (getEntries) => {'
PREFETCH_LOAD_MORE = '  const handleLoadMore = async (getEntries, { prefetch = false } = {}) => {'


def _ai_revision_fixture(root, signature):
    # Exercise the actual revision installer without a network/upstream checkout.
    tree = ast.parse((ROOT / 'src/patch_frontend.py').read_text())
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'install_ai_pagination_revision')
    namespace = {'shutil': shutil}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), 'patch_frontend.py', 'exec'), namespace)
    web = root / 'upstream/reactflux'
    sources = {
        'src/store/contentState.js': '  articleListOffset: 0,\nexport const invalidateArticleList = () => {\n}\n',
        'src/hooks/useArticleList.js': '''  contentState.setKey("articleListOffset", response.entries.length)
    currentRequestKey.current = automaticRequestKey
      const filterParams = content.filterString ? { search: content.filterString } : {}
''',
        'src/hooks/useLoadMore.js': '''      return { offset: contentState.get().articleListOffset, limit: AI_PAGE_SIZE }
SIGNATURE
    const requestKey = getCurrentArticleListRequestKey()
    const requestSessionRevision = contentState.get().articleListSnapshotRevision
      const isAiPagination = aiFilterEnabled()
      if (isAiPagination) {
      }
    if (!prefetch) markDuplicatesAsRead(duplicateEntries)
    updateEntries(newEntries, prefetch)
'''.replace('SIGNATURE', signature),
    }
    for relative, value in sources.items():
        path = web / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    return web, namespace['install_ai_pagination_revision']


@pytest.mark.parametrize('signature', [NATIVE_LOAD_MORE, PREFETCH_LOAD_MORE])
def test_ai_revision_composes_with_exact_prefetch_signature_and_repeats(tmp_path, signature):
    web, apply = _ai_revision_fixture(tmp_path, signature)
    apply(tmp_path, web)
    hook = web / 'src/hooks/useLoadMore.js'
    text = hook.read_text()
    assert text.count(signature) == 1 and text.count('  const restartChangedList = () => {') == 1
    assert 'if (!prefetch) markDuplicatesAsRead(duplicateEntries)' in text
    assert 'updateEntries(newEntries, prefetch)' in text
    if signature == NATIVE_LOAD_MORE:
        # The real stage order installs AI revision first, then reading-session
        # changes just this header before patch_frontend is executed again.
        hook.write_text(text.replace(NATIVE_LOAD_MORE, PREFETCH_LOAD_MORE, 1))
    first = _source_bytes(web)
    backups = _source_bytes(tmp_path / 'runtime/reactflux-original')
    apply(tmp_path, web)
    assert _source_bytes(web) == first
    assert _source_bytes(tmp_path / 'runtime/reactflux-original') == backups


@pytest.mark.parametrize('signature', [
    PREFETCH_LOAD_MORE.replace('false', 'true'),
    PREFETCH_LOAD_MORE.replace('prefetch = false', 'prefetch = false, unknown = true'),
    PREFETCH_LOAD_MORE + '\n' + PREFETCH_LOAD_MORE,
    NATIVE_LOAD_MORE + '\n' + PREFETCH_LOAD_MORE,
])
def test_ai_revision_unknown_or_ambiguous_prefetch_signature_refuses_all_writes(tmp_path, signature):
    web, apply = _ai_revision_fixture(tmp_path, signature)
    before = _source_bytes(web)
    with pytest.raises(RuntimeError, match='Unreviewed AI pagination source: src/hooks/useLoadMore.js'):
        apply(tmp_path, web)
    assert _source_bytes(web) == before
    assert not (tmp_path / 'runtime/reactflux-original').exists()


def test_ai_revision_repeat_with_drifted_prefetch_header_refuses_all_writes(tmp_path):
    web, apply = _ai_revision_fixture(tmp_path, PREFETCH_LOAD_MORE)
    apply(tmp_path, web)
    hook = web / 'src/hooks/useLoadMore.js'
    hook.write_text(hook.read_text().replace(PREFETCH_LOAD_MORE, PREFETCH_LOAD_MORE.replace('false', 'true'), 1))
    before = _source_bytes(web)
    backups = _source_bytes(tmp_path / 'runtime/reactflux-original')
    with pytest.raises(RuntimeError, match='Unreviewed AI pagination source: src/hooks/useLoadMore.js'):
        apply(tmp_path, web)
    assert _source_bytes(web) == before
    assert _source_bytes(tmp_path / 'runtime/reactflux-original') == backups


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


def _recorded_search_baseline(isolated, web, temporary):
    # The checked-in da5 aggregate patch records the historical selector spelling.
    # Apply only this public component hunk to pinned source, then run the old
    # formal stages. The result independently matches the captured baseline SHA.
    source = temporary / SORTER
    source.parent.mkdir(parents=True)
    source.write_bytes((web / SORTER).read_bytes())
    subprocess.run([
        "git", "apply", "--no-index", f"--include={SORTER}", "-",
    ], cwd=temporary, input=(isolated / "patches/reactflux.patch").read_bytes(),
        check=True, capture_output=True)
    original = isolated / "runtime/reactflux-original" / SORTER
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes((web / SORTER).read_bytes())
    (web / SORTER).write_bytes(source.read_bytes())


@pytest.mark.parametrize("baseline_kind", ["script", "recorded-patch"])
def test_public_production_baseline_upgrade_and_unknown_drift(tmp_path, monkeypatch, baseline_kind):
    """Keep the old generated tree/backups; only replace tracked authoring files.

    The historical public patch plus old stages reproduces the exact captured
    production component bytes without committing a server-captured fixture.
    """
    isolated = tmp_path / "upgrade"
    web = _pinned_checkout(isolated)
    _historical_authoring(isolated)
    monkeypatch.setenv("AI_NEWS_ROOT", str(isolated))
    if baseline_kind == "recorded-patch":
        _recorded_search_baseline(isolated, web, tmp_path / "recorded-source")
    _apply_authoring(isolated)
    baseline_hashes = {
        "script": "91b9d316610966db26b5cb5e6d1278ed0b94f53bbe397c5d19955352d21a3734",
        "recorded-patch": "65a3e7ce6a2c75b31994a5442054267beea9d8e7a602d371cd9787d7a9e18977",
    }
    assert hashlib.sha256((web / SORTER).read_bytes()).hexdigest() == baseline_hashes[baseline_kind]
    backups = _source_bytes(isolated / "runtime/reactflux-original")
    for name in ("src", "patches", "frontend-review"):
        (isolated / name).rename(tmp_path / ('retained-authoring-' + name))
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


CANONICAL_SELECTOR = '<select className="ai-sort-select" aria-label="排序方式" value={sortValue} onChange={changeSort}>'
HISTORICAL_SELECTOR = '<select className="article-sort-select" aria-label="文章排序" value={sortValue} onChange={changeSort}>'


@pytest.mark.parametrize(("variant", "normalization_rejects"), [
    (HISTORICAL_SELECTOR.replace('aria-label="文章排序"', 'aria-label="排序方式"'), True),
    (HISTORICAL_SELECTOR.replace('className="article-sort-select"', 'className="ai-sort-select"'), True),
    (HISTORICAL_SELECTOR.replace(' onChange=', ' data-owner="unknown" onChange='), True),
    (HISTORICAL_SELECTOR + "\n" + HISTORICAL_SELECTOR, True),
    (HISTORICAL_SELECTOR + "{/* unreviewed adjacent source */}", False),
], ids=["mixed-label", "mixed-class", "extra-attribute", "duplicate-tag", "adjacent-source"])
def test_historical_selector_nearby_unknown_variants_fail_closed(tmp_path, monkeypatch, variant, normalization_rejects):
    isolated = tmp_path / "unknown-variant"
    web = _pinned_checkout(isolated)
    for name in ("src", "patches", "frontend-review"):
        shutil.copytree(ROOT / name, isolated / name)
    monkeypatch.setenv("AI_NEWS_ROOT", str(isolated))
    _apply_authoring(isolated, STAGES[:-1])
    source = web / SORTER
    text = source.read_text()
    assert text.count(CANONICAL_SELECTOR) == 1
    source.write_text(text.replace(CANONICAL_SELECTOR, variant, 1))
    drifted = _source_bytes(web)
    if normalization_rejects:
        with pytest.raises(RuntimeError, match="scope/AI normalize anchor mismatch: .*SearchAndSortBar"):
            _apply_authoring(isolated, ("patch_scope_ai_filters.py",))
        # Earlier owned stages can refresh their components before detecting
        # drift. This unknown component remains intact; the final installer
        # must make no writes across the whole tree in either rejection path.
        assert source.read_bytes() == drifted[str(SORTER)]
        drifted = _source_bytes(web)
    else:
        # A known tag may be normalized, but surrounding unknown source remains.
        _apply_authoring(isolated, ("patch_scope_ai_filters.py",))
        assert "unreviewed adjacent source" in source.read_text()
        drifted = _source_bytes(web)
    with pytest.raises(RuntimeError, match="Unreviewed source drift, refusing overwrite: .*SearchAndSortBar"):
        install(isolated)
    assert _source_bytes(web) == drifted

SESSION_PRIVACY_BASELINE = "0d77969743868cd4ff859a4aec2784115c8c49c8"

@pytest.mark.parametrize("baseline", [SESSION_PRIVACY_BASELINE, "8faaefaf58fa8aff6c598bb5e29c8a3b2224784c"])
def test_known_closed_draft_baseline_upgrade_preserves_backups_and_equals_pristine(tmp_path, monkeypatch, baseline):
    isolated = tmp_path / "released-draft-upgrade"
    web = _pinned_checkout(isolated)
    archive = subprocess.check_output([
        "git", "-C", str(ROOT), "archive", "--format=tar", baseline,
        "src", "patches", "frontend-review",
    ])
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(isolated, filter="data")
    monkeypatch.setenv("AI_NEWS_ROOT", str(isolated))
    _apply_authoring(isolated)
    expected_helper = {
        SESSION_PRIVACY_BASELINE: "632c6ee65f2ea9369bcfc89e896db490e36b3eba8c36b904e21f041c10bded51",
        "8faaefaf58fa8aff6c598bb5e29c8a3b2224784c": "726fdf1937518a6bbbd418716c8a303a406cfe4a37c0ea3184fa84310806995a",
    }
    assert hashlib.sha256((web / "src/components/Ai/note-drafts.js").read_bytes()).hexdigest() == expected_helper[baseline]
    backups = _source_bytes(isolated / "runtime/reactflux-original")
    for name in ("src", "patches", "frontend-review"):
        (isolated / name).rename(tmp_path / ('retained-authoring-' + name))
        shutil.copytree(ROOT / name, isolated / name)
    _apply_authoring(isolated)
    for name, value in backups.items():
        assert (isolated / "runtime/reactflux-original" / name).read_bytes() == value
    upgraded = _source_bytes(web)
    _apply_authoring(isolated)
    assert _source_bytes(web) == upgraded
    pristine = tmp_path / "privacy-target-pristine"
    pristine_web = _pinned_checkout(pristine)
    for name in ("src", "patches", "frontend-review"):
        shutil.copytree(ROOT / name, pristine / name)
    monkeypatch.setenv("AI_NEWS_ROOT", str(pristine))
    _apply_authoring(pristine)
    assert _source_bytes(pristine_web) == upgraded


@pytest.mark.parametrize("relative", [
    "src/utils/session.js", "src/apis/ofetch.js", "src/components/Ai/note-drafts.js",
    "src/components/HomeRedirect.jsx", "src/pages/RouterProtect.jsx", "src/components/Sidebar/Profile.jsx",
    "src/components/Ai/note-session-core.js", "src/utils/note-session.js", "src/components/Ai/note-request.js",
])
def test_session_privacy_unknown_drift_refuses_every_overlay_write(tmp_path, relative):
    isolated = tmp_path / "privacy-drift"
    web = isolated / "upstream/reactflux"
    shutil.copytree(ROOT / "frontend-review/after", web)
    shutil.copytree(ROOT / "frontend-review", isolated / "frontend-review")
    target = web / relative
    target.write_bytes(target.read_bytes() + b"\n// unknown session privacy drift\n")
    drifted = _source_bytes(web)
    with pytest.raises(RuntimeError, match="Unreviewed source drift, refusing overwrite"):
        install(isolated)
    assert _source_bytes(web) == drifted

def test_scope_normalizer_preserves_only_exact_authoritative_overlay(tmp_path):
    # Re-running a historical construction stage must not undo a current reviewed
    # component, but a changed look-alike cannot bypass its normal anchor refusal.
    tree = ast.parse((ROOT / 'src/patch_scope_ai_filters.py').read_text())
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ('backup', 'patch', 'normalize')]
    web = tmp_path / 'upstream/reactflux'
    name = 'src/components/Article/SearchAndSortBar.jsx'
    installed = web / name
    authored = tmp_path / 'frontend-review/after' / name
    installed.parent.mkdir(parents=True)
    authored.parent.mkdir(parents=True)
    authored.write_text('current reviewed sorter')
    namespace = {'ROOT': tmp_path, 'WEB': web,
                 'BACK': tmp_path / 'runtime/reactflux-original', 'shutil': shutil}
    exec(compile(ast.Module(body=functions, type_ignores=[]),
                 'patch_scope_ai_filters.py', 'exec'), namespace)
    installed.write_text(authored.read_text())
    namespace['normalize'](name, ['legacy anchor'], 'historical final anchor')
    assert installed.read_text() == authored.read_text()
    assert not namespace['BACK'].exists()
    installed.write_text('current reviewed sorter plus unknown drift')
    with pytest.raises(RuntimeError, match='normalize anchor mismatch'):
        namespace['normalize'](name, ['legacy anchor'], 'historical final anchor')
    assert not namespace['BACK'].exists()
    installed.write_text('legacy anchor')
    namespace['normalize'](name, ['legacy anchor'], 'historical final anchor')
    assert installed.read_text() == 'historical final anchor'
    assert (namespace['BACK'] / name).read_text() == 'legacy anchor'
