"""Offline contracts for the snapshot overlay's generated and reviewed inputs."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from build_ci_reader import STAGES
from patch_interaction_review import install as install_review
from patch_reader_snapshots import CHANGES, install


class ReaderSnapshotInstallerTests(unittest.TestCase):
    def fixture(self):
        root = Path(tempfile.mkdtemp(prefix="reader-snapshot-"))
        web = root / "upstream/reactflux"
        for name, replacements in CHANGES.items():
            path = web / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(before for before, _ in replacements), encoding="utf-8")
        return root, web

    def test_installs_once_at_both_build_entry_points(self):
        root, web = self.fixture()
        self.assertEqual(set(install(root)), set(CHANGES))
        installed = {name: (web / name).read_bytes() for name in CHANGES}
        self.assertEqual(install(root), [])
        self.assertEqual(installed, {name: (web / name).read_bytes() for name in CHANGES})
        stage = "patch_reader_snapshots.py"
        self.assertEqual(STAGES.count(stage), 1)
        self.assertLess(STAGES.index("patch_reader_entry_defaults.py"), STAGES.index(stage))
        self.assertLess(STAGES.index(stage), STAGES.index("patch_interaction_review.py"))
        deployment = (ROOT / "src/build_frontend.py").read_text(encoding="utf-8")
        self.assertEqual(deployment.count(stage), 1)
        self.assertLess(deployment.index("patch_reader_entry_defaults.py"), deployment.index(stage))
        self.assertLess(deployment.index(stage), deployment.index("patch_interaction_review.py"))

    def test_drift_is_refused_before_the_stage_writes(self):
        root, web = self.fixture()
        target = web / "src/components/Sidebar/Sidebar.jsx"
        target.write_text("unreviewed replacement", encoding="utf-8")
        previous = {name: (web / name).read_bytes() for name in CHANGES}
        with self.assertRaisesRegex(RuntimeError, "patch anchor mismatch"):
            install(root)
        self.assertEqual(previous, {name: (web / name).read_bytes() for name in CHANGES})

    def test_interaction_installer_matches_portable_exact_history_keys(self):
        root, web = self.fixture()
        name = "src/utils/session.js"
        entry = root / "frontend-review/after" / name
        entry.parent.mkdir(parents=True)
        entry.write_bytes((ROOT / "frontend-review/after" / name).read_bytes())
        previous = b"exact previously reviewed bytes"
        history = root / "frontend-review/previous-hashes.json"
        history.write_text(json.dumps({name: [hashlib.sha256(previous).hexdigest()]}), encoding="utf-8")
        installed = web / name
        installed.parent.mkdir(parents=True)
        installed.write_bytes(previous + b" ")
        with self.assertRaisesRegex(RuntimeError, "Unreviewed source drift"):
            install_review(root)
        installed.write_bytes(previous)
        self.assertEqual(install_review(root), [name])
        self.assertEqual(installed.read_bytes(), entry.read_bytes())
        self.assertEqual(install_review(root), [])
