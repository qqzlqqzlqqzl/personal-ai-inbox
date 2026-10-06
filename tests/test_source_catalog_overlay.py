"""Exact old Reader panel upgrades and fail-closed overlay bounds for #107."""
import ast
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from patch_interaction_review import install

# This exact panel baseline is an ancestor available to a normal Hosted checkout.
BASE = '5fc42928c2b0504603345f93a76a54fb66f1bfa5'
PANEL = 'src/components/Ai/AiPanel.jsx'
HELPER = 'src/components/Ai/SourceCatalogMetadata.jsx'
OLD_SHA = '06d5e523f593193302f73573f3ef37d068c73f93ec93e4e443084d91a64b9039'
OLD_INSTALLED_SHA = '1dfd59c172d1297aa03925c9030119746c62ff0c6d5637c0f298ef7b4ec53420'


class CatalogOverlayTests(unittest.TestCase):
    def fixture(self, kind):
        root = Path(tempfile.mkdtemp(prefix='source-catalog-overlay-'))
        for relative in (PANEL, HELPER):
            src = ROOT / 'frontend-review/after' / relative
            dst = root / 'frontend-review/after' / relative
            dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(src.read_bytes())
        shutil.copyfile(ROOT / 'frontend-review/previous-hashes.json', root / 'frontend-review/previous-hashes.json')
        original = subprocess.check_output(['git', 'show', BASE + ':frontend-review/after/' + PANEL], cwd=ROOT, timeout=15)
        self.assertEqual(hashlib.sha256(original).hexdigest(), OLD_SHA)
        if kind == 'installed':
            # Real reviewed additive status overlay byte transformation, whose
            # result was independently measured by baseline prepare as well.
            import install_agent_status as status
            original = (status.LINK_IMPORT + original.decode().replace(status.PANEL_ANCHOR, status.LINK + status.PANEL_ANCHOR, 1)).encode()
            self.assertEqual(hashlib.sha256(original).hexdigest(), OLD_INSTALLED_SHA)
        target = root / 'upstream/reactflux' / PANEL
        target.parent.mkdir(parents=True); target.write_bytes(original)
        return root, target

    def test_exact_plain_and_status_installed_upgrade_repeat(self):
        for kind in ('plain', 'installed'):
            with self.subTest(kind=kind):
                root, target = self.fixture(kind)
                self.assertEqual(set(install(root)), {PANEL, HELPER})
                self.assertEqual(target.read_bytes(), (ROOT / 'frontend-review/after' / PANEL).read_bytes())
                self.assertEqual(install(root), [])

    def test_unknown_panel_refuses_before_any_helper_write(self):
        root, target = self.fixture('plain'); target.write_text('unknown panel drift')
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed source drift'):
            install(root)
        self.assertFalse((root / 'upstream/reactflux' / HELPER).exists())
        self.assertEqual(target.read_text(), 'unknown panel drift')

    def test_only_exact_prior_panel_hashes_appended(self):
        old = json.loads(subprocess.check_output(['git', 'show', BASE + ':frontend-review/previous-hashes.json'], cwd=ROOT, timeout=15))
        new = json.loads((ROOT / 'frontend-review/previous-hashes.json').read_text())
        self.assertEqual(new[PANEL], old[PANEL] + [OLD_SHA, OLD_INSTALLED_SHA])
        old.pop(PANEL); new.pop(PANEL)
        utility = "src/components/Ai/review-utils.js"
        old.setdefault(utility, [])
        reviewed = "90fd8db5d91333cd4a4f1d6a7db37bcf37c6c0ae7c541967afa23b66a018f608"
        if reviewed not in old[utility]: old[utility].append(reviewed)
        # The reviewed focus slice adds only this prior ReadingControls hash.
        reading = "src/components/Ai/ReadingControls.jsx"
        reviewed_reading = "d6475801bc39d59dff9d732dd12cb5afd21fee3c0f91fa3e3f41e674add63680"
        prior_reading = subprocess.check_output(
            ['git', 'show', BASE + ':frontend-review/after/' + reading], cwd=ROOT, timeout=15)
        self.assertEqual(hashlib.sha256(prior_reading).hexdigest(), reviewed_reading)
        self.assertNotIn(reviewed_reading, old[reading])
        old[reading].append(reviewed_reading)
        # The reviewed quarantine slice adds this exact prior status helper hash.
        status = "src/components/Ai/kaggle-status.js"
        reviewed_status = "59fdd056a69c00c46be24ba524dcaa467736f7b5c70a48cc7274e1398b5ea515"
        prior_status = subprocess.check_output(
            ['git', 'show', '3608d0903177a67e23baa658bd9eb218e09303f1:frontend-review/after/' + status], cwd=ROOT, timeout=15)
        self.assertEqual(hashlib.sha256(prior_status).hexdigest(), reviewed_status)
        self.assertNotIn(reviewed_status, old[status])
        old[status].append(reviewed_status)
        # Only the deployed pre-variant image overlay needs upgrade admission.
        image = "src/components/Article/ImageOverlayButton.jsx"
        reviewed_image = "f39b91dbd225a108573c030c0361f06bdad5f32e2c6b97b1175803007261e0a2"
        prior_image = subprocess.check_output(
            ['git', 'show', '92c7f8c8423ee0e0b386b4413a072b27a4405a72:frontend-review/after/' + image], cwd=ROOT, timeout=15)
        self.assertEqual(hashlib.sha256(prior_image).hexdigest(), reviewed_image)
        self.assertNotIn(reviewed_image, old[image])
        old[image].append(reviewed_image)
        # PR #140 admits only this exact pre-thumbnail helper for upgrade.
        variants = "src/components/Article/reader-image-variants.js"
        reviewed_variants = "8e85c775606c8bf356cf2f310953ed4e0aeda5d0d8d5f7e11e8bbd87150cc2b3"
        prior_variants = subprocess.check_output(
            ['git', 'show', '67fdff4766d6ec498ee1be9518cee8d7fa563d16:frontend-review/after/' + variants], cwd=ROOT, timeout=15)
        self.assertEqual(hashlib.sha256(prior_variants).hexdigest(), reviewed_variants)
        self.assertNotIn(variants, old)
        old[variants] = [reviewed_variants]
        # #147 upgrades the exact deployed thumbnail helper, preserving old pins.
        deployed_variants = "ec75521da6aeae56af43e635c504fc270ade7d76502780ac70361eea236d7491"
        prior_variants = subprocess.check_output(
            ['git', 'show', 'bccac49365810b75d914460d5ce201a2e8fbc3ae:frontend-review/after/' + variants], cwd=ROOT, timeout=15)
        self.assertEqual(hashlib.sha256(prior_variants).hexdigest(), deployed_variants)
        self.assertNotIn(deployed_variants, old[variants])
        old[variants].append(deployed_variants)
        # #151 admits only the exact preceding ten-page prefetch helper blob.
        prefetch_variants = "a7b309af14b311630f9a8b70948a7c2a445686409f3bd6c1bf914c5456493c58"
        prefetch_base = 'da50fc6a9414a6bba21a2feac5db3d6223e6f104'
        subprocess.run(['git', 'merge-base', '--is-ancestor', prefetch_base, 'HEAD'], cwd=ROOT, check=True, timeout=15)
        prior_variants = subprocess.check_output(
            ['git', 'show', prefetch_base + ':frontend-review/after/' + variants], cwd=ROOT, timeout=15)
        self.assertEqual(hashlib.sha256(prior_variants).hexdigest(), prefetch_variants)
        self.assertNotIn(prefetch_variants, old[variants])
        old[variants].append(prefetch_variants)
        # #153 retires automatic raw-origin covers. Admit exactly the deployed
        # helper and component bytes; no other overlay histories may change.
        cover_base = '0d09a7d382c0f9a1927b4ec6564a5909d12cafc1'
        subprocess.run(['git', 'merge-base', '--is-ancestor', cover_base, 'HEAD'], cwd=ROOT, check=True, timeout=15)
        for name, expected in {
            variants: 'ecbb0b1b802c2bbafcc09235a2f0bb314c6ada7dbdb439719a9be08a6db7e6de',
            'src/components/Article/ReaderThumbnail.jsx': '597dbf6257b36c156ee9b65e747411789eefc234e112aee462116e175761894a',
        }.items():
            previous = subprocess.check_output(['git', 'show', cover_base + ':frontend-review/after/' + name], cwd=ROOT, timeout=15)
            self.assertEqual(hashlib.sha256(previous).hexdigest(), expected)
            self.assertNotIn(expected, old.get(name, []))
            old.setdefault(name, []).append(expected)
        self.assertEqual(old, new)

    def test_browser_entry_requires_explicit_sandbox_before_construction(self):
        tree = ast.parse((ROOT / 'tests/source_catalog_browser.py').read_text())
        gate = next(i for i, n in enumerate(tree.body) if isinstance(n, ast.If) and 'sandboxed' in ast.unparse(n.test))
        loop = next(i for i, n in enumerate(tree.body) if isinstance(n, ast.For))
        self.assertLess(gate, loop)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'Harness']
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(k.arg == 'sandboxed' and isinstance(k.value, ast.Constant) and k.value.value is True for k in calls[0].keywords))
        for constructor, accepted in ((lambda name, **kwargs: None, False), (lambda name, *, sandboxed=False: None, True)):
            self.assertEqual('sandboxed' in inspect.signature(constructor).parameters, accepted)


if __name__ == '__main__':
    unittest.main()
