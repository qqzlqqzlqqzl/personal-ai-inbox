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
        # #155 admits only the exact preceding detail overlay for bilingual reading.
        detail = 'src/components/Article/ArticleDetail.jsx'
        detail_base = '9208fbee81e6e3c0a2ab762cad827bce22531d6f'
        subprocess.run(['git', 'merge-base', '--is-ancestor', detail_base, 'HEAD'],
                       cwd=ROOT, check=True, timeout=15)
        prior_detail = subprocess.check_output(
            ['git', 'show', detail_base + ':frontend-review/after/' + detail],
            cwd=ROOT, timeout=15)
        expected_detail = '7d01753a874395ccd366f851ccfb1c6eea76c212f9fae648812eb90c4f96529a'
        self.assertEqual(hashlib.sha256(prior_detail).hexdigest(), expected_detail)
        self.assertNotIn(expected_detail, old[detail])
        old[detail].append(expected_detail)
        # #165 restores on-demand translation over the exact currently deployed overlays.
        demand_base = '49edbf6af48d4c56862a36afab9bcd829690eeef'
        subprocess.run(['git', 'merge-base', '--is-ancestor', demand_base, 'HEAD'],
                       cwd=ROOT, check=True, timeout=15)
        for name, expected in {
            detail: '7f05ef9ecfe3fb368f951495b0b8b6fe800fb0b0b61cdbc704e9ecd05214db42',
            reading: '85fa06d76bfcbdab2c485d507da5578bf6803be80e7dc8b4b970fc16bc585ab2',
        }.items():
            previous = subprocess.check_output(
                ['git', 'show', demand_base + ':frontend-review/after/' + name],
                cwd=ROOT, timeout=15)
            self.assertEqual(hashlib.sha256(previous).hexdigest(), expected)
            self.assertNotIn(expected, old[name])
            old[name].append(expected)
        # #191 admits the exact two-slot helper preceding six-slot cover prefetch.
        slots_base = '1a50f1b6b3811e287e9fc6c76fcd7a7e78927ac4'
        subprocess.run(['git', 'merge-base', '--is-ancestor', slots_base, 'HEAD'],
                       cwd=ROOT, check=True, timeout=15)
        prior_slots = subprocess.check_output(
            ['git', 'show', slots_base + ':frontend-review/after/' + variants],
            cwd=ROOT, timeout=15)
        expected_slots = '6eca73734c049c3d0076c29f1c229051f1332ef3b0fdfed4ed4a186bda2bbef0'
        self.assertEqual(hashlib.sha256(prior_slots).hexdigest(), expected_slots)
        self.assertNotIn(expected_slots, old[variants])
        old[variants].append(expected_slots)
        # #199 admits only the exact deployed notes-sort control for upgrade.
        sort_name = 'src/components/Article/SearchAndSortBar.jsx'
        sort_base = '8db6f0f69974b299d0a7a732e9b07d4b392f66ed'
        sort_sha = '25e801d1c55b2597f614d1a2e5a7a141d6217d417044f999a0d6d493f6559f2a'
        prior_sort = subprocess.check_output(
            ['git', 'show', sort_base + ':frontend-review/after/' + sort_name],
            cwd=ROOT, timeout=15)
        self.assertEqual(hashlib.sha256(prior_sort).hexdigest(), sort_sha)
        self.assertNotIn(sort_sha, old.get(sort_name, []))
        old.setdefault(sort_name, []).append(sort_sha)
        # #244 upgrades only these exact prior/current generated overlays.
        feedback_base = 'ff0994047d4dccb88a3c79f68c47299e30660394'
        baseline_history = json.loads(subprocess.check_output(
            ['git', 'show', feedback_base + ':frontend-review/previous-hashes.json'], cwd=ROOT, timeout=15))
        self.assertEqual(old, baseline_history)
        approved_feedback = {'src/components/Ai/AiToolbar.jsx': ['ca8723c283ac71c1965674da6f73025f91f62656dc8b7d886ccacc07f6f178d6', 'dd13ef0e1d8f10d438812f2d8199602573e901821e776b394265985be3d9ea46'], 'src/utils/settings-schema.js': ['dc38ce53e52b6a9ff447a16dd0fd63b2812088bf4bed24cdfc27cf7e2581c29c', 'd94904e9091156c5ac88a6365bae60f5a14d89d980f34066b5f173be80f750c2'], 'src/components/Article/reader-image-variants.js': ['eb741399390c6713d63e238d776b3dc7bbfdbcf170792a6e1e1b764db1e015e8'], 'src/components/Article/SearchAndSortBar.jsx': ['a8ba0973ae7f193e23e6b9cccc3906de07e52c78365ba64ca5f93c1578f07dd9', '6fad0bdafcd4d034537dbf19c65ca4c8e179b2c910d9df48b8c290131d5279af'], 'src/components/Article/ArticleGridCard.jsx': ['05e2eefa2edee425c27fa1f35675fc70cb41cf90ea449c52805eb94b9f7632d9'], 'src/components/HomeRedirect.jsx': ['7d1eaf7710294dc5cd9f3fdf53ca92d849988d00a6a969f7bd0e389e25869159', 'ba4981906190599d87de175a95cfeef19bef891cc23467fac43b31597ca8392c']}
        for name, values in approved_feedback.items():
            for value in values:
                self.assertNotIn(value, old.get(name, []))
                old.setdefault(name, []).append(value)
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
