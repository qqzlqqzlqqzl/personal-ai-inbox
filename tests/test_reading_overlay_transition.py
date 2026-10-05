"""Exact prior-overlay admission, repeat application and unknown-source refusal."""
import hashlib
import importlib.util
import json
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("reading_overlay_installer", ROOT / "src/patch_interaction_review.py")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)
PRIOR = {
    "src/components/Ai/ReadingControls.jsx": "733c5102b090d4121afaa3635342dcfe05c13fdf823ab00547215d44d554eba7",
    "src/components/Ai/ReviewWorkflows.css": "1a6eb73d473aedc2373ca21fd746ff963390ccefb22c3e1fe21ede80b69b1ea4",
    "src/components/Article/ArticleDetail.jsx": "63007fdc9a3e706fcc352511ae9c2957b5cb2cfdc40d5db7881c08cdc36631aa",
}


def fixture():
    parent = ROOT / "runtime/reading-overlay-controls"
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="retained-", dir=parent))
    history = json.loads((ROOT / "frontend-review/previous-hashes.json").read_text())
    for name, sha in PRIOR.items():
        assert sha in history[name]
        for kind in ("after", "before"):
            source = ROOT / "frontend-review" / kind / name
            if source.is_file():
                target = root / "frontend-review" / kind / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
        target = root / "upstream/reactflux" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        # Read exact approved prior bytes from Git, not from a mutable generated tree.
        import subprocess
        raw = subprocess.check_output([
            "git", "show", "9a66803bdb191dce023dca9e3800821ee570203e:frontend-review/after/" + name,
        ], cwd=ROOT, timeout=15)
        assert hashlib.sha256(raw).hexdigest() == sha
        target.write_bytes(raw)
    (root / "frontend-review/previous-hashes.json").write_text(json.dumps(history))
    return root


def test_exact_prior_overlay_upgrade_and_repeat():
    root = fixture()
    assert set(INSTALLER.install(root)) == set(PRIOR)
    assert INSTALLER.install(root) == []
    for name in PRIOR:
        assert (root / "upstream/reactflux" / name).read_bytes() == (ROOT / "frontend-review/after" / name).read_bytes()


@pytest.mark.parametrize("name", list(PRIOR))
def test_unknown_preimage_rejected_before_any_write(name):
    root = fixture()
    target = root / "upstream/reactflux" / name
    target.write_bytes(target.read_bytes() + b"\n/* synthetic unreviewed drift */\n")
    before = {n: (root / "upstream/reactflux" / n).read_bytes() for n in PRIOR}
    with pytest.raises(RuntimeError, match="Unreviewed source drift"):
        INSTALLER.install(root)
    assert before == {n: (root / "upstream/reactflux" / n).read_bytes() for n in PRIOR}


def test_reading_keyboard_upgrade_from_exact_3349_and_unknown_drift(tmp_path):
    """The keyboard-only update admits the exact delivered control, nothing broader."""
    import subprocess
    name = 'src/components/Ai/ReadingControls.jsx'
    old = subprocess.check_output([
        'git', 'show', '3349cf22bb73a9b08c04e84e960048fb8e64a653:frontend-review/after/' + name,
    ], cwd=ROOT, timeout=15)
    old_sha = 'd6475801bc39d59dff9d732dd12cb5afd21fee3c0f91fa3e3f41e674add63680'
    assert hashlib.sha256(old).hexdigest() == old_sha
    history = json.loads((ROOT / 'frontend-review/previous-hashes.json').read_text())
    assert history[name].count(old_sha) == 1
    authored = ROOT / 'frontend-review/after' / name
    target = tmp_path / 'frontend-review/after' / name
    target.parent.mkdir(parents=True)
    target.write_bytes(authored.read_bytes())
    (tmp_path / 'frontend-review/previous-hashes.json').write_text(json.dumps(history))
    installed = tmp_path / 'upstream/reactflux' / name
    installed.parent.mkdir(parents=True)
    installed.write_bytes(old)
    assert INSTALLER.install(tmp_path) == [name]
    assert installed.read_bytes() == authored.read_bytes()
    assert INSTALLER.install(tmp_path) == []
    unknown = old + b'\n// Unknown adjacent content, not an approved baseline.\n'
    installed.write_bytes(unknown)
    with pytest.raises(RuntimeError, match='Unreviewed source drift'):
        INSTALLER.install(tmp_path)
    assert installed.read_bytes() == unknown
