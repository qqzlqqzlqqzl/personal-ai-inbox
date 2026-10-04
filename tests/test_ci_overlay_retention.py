"""Exercise failed overlay writes without deleting their synthetic evidence."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import patch_interaction_review as overlay


class OverlayRetentionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='reader-overlay-retained-'))
        for path, data in [('frontend-review/before/src/fixture.txt', b'old'),
                           ('frontend-review/after/src/fixture.txt', b'new'),
                           ('upstream/reactflux/src/fixture.txt', b'old')]:
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        self.target = self.root / 'upstream/reactflux/src/fixture.txt'

    def test_failed_atomic_replace_keeps_generated_bytes_and_original(self):
        with mock.patch.object(overlay.os, 'replace', side_effect=OSError('synthetic replace refusal')):
            with self.assertRaisesRegex(OSError, 'synthetic replace refusal'):
                overlay.install(self.root)
        self.assertEqual(self.target.read_bytes(), b'old')
        retained = list(self.target.parent.glob('.review-*'))
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_bytes(), b'new')

    def test_successful_atomic_replace_has_no_orphan_temporary(self):
        self.assertEqual(overlay.install(self.root), ['src/fixture.txt'])
        self.assertEqual(self.target.read_bytes(), b'new')
        self.assertEqual(list(self.target.parent.glob('.review-*')), [])
        self.assertEqual(overlay.install(self.root), [])

    def test_unknown_drift_still_refuses_before_temporary_write(self):
        self.target.write_bytes(b'unknown')
        with self.assertRaisesRegex(RuntimeError, 'Unreviewed source drift'):
            overlay.install(self.root)
        self.assertEqual(self.target.read_bytes(), b'unknown')
        self.assertEqual(list(self.target.parent.glob('.review-*')), [])


if __name__ == '__main__':
    unittest.main()
