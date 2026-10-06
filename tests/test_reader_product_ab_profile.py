"""No network/browser: bounded profile selection and immutable phase ownership."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import reader_loading_ab_ci as ab

ROOT = Path(__file__).resolve().parents[1]


class ProductProfileControls(unittest.TestCase):
    def test_original_regression_manifest_is_byte_identical(self):
        name = 'tests/fixtures/reader-loading-ab-inputs.json'
        original = subprocess.check_output(['git', 'show',
            '44d2c203b3a8734cdad890d1ca02306f3e29ab2e:' + name], cwd=ROOT, timeout=15)
        self.assertEqual((ROOT / name).read_bytes(), original)
        self.assertEqual(ab.load_spec()['inputs']['candidate']['producer_conclusion'], 'failure')

    def test_unbound_product_is_rejected_before_prepare_or_network(self):
        root, old, new, _ = self.bound_fixture()
        new.write_text(json.dumps({'schema': 1, 'repository': ab.base.REPOSITORY, 'binding_state': 'UNBOUND'}))
        with patch.object(ab, 'ROOT', root), patch.object(ab, 'SPEC', old), patch.object(ab, 'PRODUCT_SPEC', new), \
                patch.object(sys, 'argv', ['ab', 'prepare', '--comparison-profile', 'product']), \
                patch.object(ab.base, 'prepare') as prepare, patch.object(ab, 'gh_json') as gh:
            with self.assertRaisesRegex(ValueError, 'not bound'):
                ab.main()
        prepare.assert_not_called()
        gh.assert_not_called()

    def test_unknown_profile_never_becomes_a_path(self):
        for name in ('', '../fixture.json', 'https://outside.invalid/manifest', 'PRODUCT', None):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'unknown comparison profile'):
                ab.spec_path(name)

    def bound_fixture(self):
        root = Path(tempfile.mkdtemp(prefix='reader-product-profile-retained-'))
        directory = root / 'tests/fixtures'; directory.mkdir(parents=True)
        original = ab.load_spec()
        product = copy.deepcopy(original)
        product.update(binding_state='BOUND', purpose='Synthetic profile contract; no network execution')
        regression_path = directory / 'reader-loading-ab-inputs.json'
        product_path = directory / 'reader-loading-product-ab-inputs.json'
        regression_path.write_text(json.dumps(original))
        product_path.write_text(json.dumps(product))
        return root, regression_path, product_path, product

    def test_complete_bound_profile_records_exact_selected_bytes(self):
        root, old, new, _ = self.bound_fixture()
        with patch.object(ab, 'ROOT', root), patch.object(ab, 'SPEC', old), patch.object(ab, 'PRODUCT_SPEC', new):
            record = ab.profile_record('product')
            self.assertEqual(record, {'profile': 'product',
                'spec_path': 'tests/fixtures/reader-loading-product-ab-inputs.json',
                'spec_sha256': hashlib.sha256(new.read_bytes()).hexdigest()})
            evidence = root / 'evidence'; evidence.mkdir()
            (evidence / 'ab-selected-profile.json').write_text(json.dumps(record))
            ab.verify_profile(root, 'product')

    def test_missing_or_extra_product_identity_fields_are_rejected(self):
        root, old, new, spec = self.bound_fixture()
        keys = list(spec['inputs']['candidate'])
        with patch.object(ab, 'ROOT', root), patch.object(ab, 'SPEC', old), patch.object(ab, 'PRODUCT_SPEC', new):
            for key in keys + ['unexpected']:
                changed = copy.deepcopy(spec)
                if key == 'unexpected': changed['inputs']['candidate'][key] = 'ignored?'
                else: del changed['inputs']['candidate'][key]
                new.write_text(json.dumps(changed))
                with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'input fields'):
                    ab.load_spec('product')

    def test_product_cannot_change_repository_baseline_or_budget(self):
        root, old, new, spec = self.bound_fixture()
        variants = []
        v=copy.deepcopy(spec);v['repository']='other/repo';variants.append(v)
        v=copy.deepcopy(spec);v['inputs']['baseline']['artifact_id']=1;variants.append(v)
        v=copy.deepcopy(spec);v['inputs']['candidate']['artifact_bytes']=32*1024*1024+1;variants.append(v)
        v=copy.deepcopy(spec);v['inputs']['candidate']['producer_conclusion']='in_progress';variants.append(v)
        with patch.object(ab, 'ROOT', root), patch.object(ab, 'SPEC', old), patch.object(ab, 'PRODUCT_SPEC', new):
            for index, value in enumerate(variants):
                new.write_text(json.dumps(value))
                with self.subTest(index=index), self.assertRaises(ValueError): ab.load_spec('product')

    def test_phase_profile_or_manifest_swap_is_rejected(self):
        root, old, new, spec = self.bound_fixture()
        with patch.object(ab, 'ROOT', root), patch.object(ab, 'SPEC', old), patch.object(ab, 'PRODUCT_SPEC', new):
            evidence=root/'evidence';evidence.mkdir()
            (evidence/'ab-selected-profile.json').write_text(json.dumps(ab.profile_record('product')))
            with self.assertRaisesRegex(ValueError,'changed between phases'):ab.verify_profile(root,'regression')
            spec['purpose']='different manifest bytes'
            new.write_text(json.dumps(spec))
            with self.assertRaisesRegex(ValueError,'changed between phases'):ab.verify_profile(root,'product')

    def test_instrument_manifest_includes_only_selected_input_table(self):
        for profile, selected, absent in [('regression',ab.SPEC,ab.PRODUCT_SPEC),
                                          ('product',ab.PRODUCT_SPEC,ab.SPEC)]:
            value=ab.instrument_manifest(profile)
            names={x['path'] for x in value['files']}
            self.assertIn(selected.relative_to(ab.ROOT).as_posix(),names)
            self.assertNotIn(absent.relative_to(ab.ROOT).as_posix(),names)

    def test_workflow_default_and_pull_requests_measure_product(self):
        text=(ROOT/'.github/workflows/reader-loading-ab.yml').read_text()
        self.assertIn('type: choice\n        required: true\n        default: product',text)
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.comparison_profile || 'product'",text)
        self.assertIn('          - regression\n          - product',text)
        self.assertIn("      - 'tests/fixtures/reader-loading-product-ab-inputs.json'",text)
        self.assertEqual(text.count('--comparison-profile "$READER_AB_PROFILE"'),4)
        self.assertNotIn('secrets.',text)
        self.assertIn('mode: [keyboard, touch, weak-network]',text)


if __name__=='__main__':unittest.main()
