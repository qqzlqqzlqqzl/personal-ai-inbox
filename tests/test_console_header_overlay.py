"""Exact former-overlay upgrade and untouched reading styles; no browser claim."""
import hashlib
import json
from pathlib import Path

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


def test_exact_f_preimage_and_reading_suffix_preserved():
    old = OLD.read_bytes()
    assert hashlib.sha256(old).hexdigest() == OLD_SHA
    history = json.loads((ROOT / 'frontend-review/previous-hashes.json').read_text())
    assert history[str(NAME)].count(OLD_SHA) == 1
    updated = (ROOT / 'frontend-review/after' / NAME).read_bytes()
    # Includes every reading/navigation/resource rule after the console segment.
    assert updated.split(b'\n\n.review-reading-bar', 1)[1] == old.split(b'\n\n.review-reading-bar', 1)[1]


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
