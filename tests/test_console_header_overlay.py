"""Exact historical overlay upgrade and bounded reading change; no browser claim."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from patch_interaction_review import install

ROOT = Path(__file__).resolve().parents[1]
NAME = Path('src/components/Ai/ReviewWorkflows.css')
OLD = ROOT / 'tests/fixtures/console-header-F110255.css'
OLD_SHA = 'd043b3b48c99076fa9cfdf83ee7c95509c5fa9ac3d75504414dd5c49a2e39686'


def fixture(tmp_path, current):
    for folder in ('before', 'after'):
        target = tmp_path / 'frontend-review' / folder / NAME
        target.parent.mkdir(parents=True)
        source = ROOT / 'frontend-review' / folder / NAME
        if source.exists():
            target.write_bytes(source.read_bytes())
    history = ROOT / 'frontend-review/previous-hashes.json'
    (tmp_path / 'frontend-review/previous-hashes.json').write_bytes(history.read_bytes())
    destination = tmp_path / 'upstream/reactflux' / NAME
    destination.parent.mkdir(parents=True)
    if current is not None:
        destination.write_bytes(current)
    return destination


def test_exact_f_preimage_and_bounded_reading_suffix_change():
    old = OLD.read_bytes()
    assert hashlib.sha256(old).hexdigest() == OLD_SHA
    history = json.loads((ROOT / 'frontend-review/previous-hashes.json').read_text())
    assert history[str(NAME)].count(OLD_SHA) == 1
    updated = (ROOT / 'frontend-review/after' / NAME).read_bytes()
    # Preserve the historical fixture and every rule outside the authorized
    # full-width, right-aligned icon toolbar. Do not freeze the old text buttons.
    baseline = (ROOT / 'tests/fixtures/reading-toolbar-34ad.css').read_bytes()
    assert hashlib.sha256(baseline).hexdigest() == '4093e7792ca6c8be2bc60dfd6cbc85f8ed1456631bfbebb84504b846fbbb7a2d'
    allowed = baseline
    changes = [
        (b'.review-reading-bar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:14px auto;padding:8px 0;',
         b'.review-reading-bar{box-sizing:border-box;width:100%;display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:flex-end;margin:0 auto;padding:4px 8px;'),
        ('.review-reading-controls>summary::before{content:"▸";margin-right:6px}'.encode(),
         b'.review-reading-controls>summary::before{content:none}'),
        (b'.review-reading-bar>span[role=status]{flex-basis:100%;font-size:12px;line-height:1.4}',
         b'.review-reading-bar>span[role=status]{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}'),
        (b'.review-reading-options{position:absolute;top:calc(100% + 8px);left:0;',
         b'.review-reading-options{position:absolute;top:calc(100% + 8px);right:0;'),
    ]
    for before, after in changes:
        assert allowed.count(before) == 1
        allowed = allowed.replace(before, after, 1)
    allowed += (
        b'\n.review-reading-bar>button,.review-reading-controls>summary{width:36px;height:36px;padding:0;flex:none}\n'
        b'.review-reading-bar>button[aria-pressed=true]{color:rgb(var(--primary-6,57,122,184));border-color:currentColor;background:var(--color-fill-2,#eef3fa)}\n'
    )
    assert updated == allowed


def test_reviewed_34ad_three_file_upgrade_and_repeat(tmp_path):
    old_commit = '34ad59892bdb99b19c3919eeb18a5fbfd5b8d98e'
    names = [NAME, Path('src/components/Ai/ReadingControls.jsx'),
             Path('src/components/Article/ArticleDetail.jsx')]
    history = json.loads((ROOT / 'frontend-review/previous-hashes.json').read_text())
    for name in names:
        old = subprocess.check_output(['git', 'show', old_commit+':frontend-review/after/'+name.as_posix()],
                                      cwd=ROOT, timeout=15)
        assert history[name.as_posix()].count(hashlib.sha256(old).hexdigest()) == 1
        for folder in ('before', 'after'):
            source = ROOT/'frontend-review'/folder/name
            if source.exists():
                target = tmp_path/'frontend-review'/folder/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
        destination = tmp_path/'upstream/reactflux'/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(old)
    (tmp_path/'frontend-review/previous-hashes.json').write_bytes(
        (ROOT/'frontend-review/previous-hashes.json').read_bytes())
    assert set(install(tmp_path)) == {str(name) for name in names}
    for name in names:
        assert (tmp_path/'upstream/reactflux'/name).read_bytes() == (ROOT/'frontend-review/after'/name).read_bytes()
    assert install(tmp_path) == []


@pytest.mark.parametrize('source', ['clean', 'F110255'])
def test_clean_or_f_overlay_upgrade_and_repeat(tmp_path, source):
    assert not (ROOT / 'frontend-review/before' / NAME).exists()
    target = fixture(tmp_path, None if source == 'clean' else OLD.read_bytes())
    assert install(tmp_path) == [str(NAME)]
    assert target.read_bytes() == (ROOT / 'frontend-review/after' / NAME).read_bytes()
    assert install(tmp_path) == []


def test_unknown_overlay_is_rejected_without_overwrite(tmp_path):
    unreviewed = OLD.read_bytes() + b'\n/* unreviewed */\n'
    target = fixture(tmp_path, unreviewed)
    with pytest.raises(RuntimeError, match='Unreviewed source drift'):
        install(tmp_path)
    assert target.read_bytes() == unreviewed
