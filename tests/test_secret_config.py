"""Test environment-file safety with synthetic values only."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import initialize_secrets as config


class SecretConfigTests(unittest.TestCase):
    def setUp(self):
        # Keep synthetic fixtures under ignored backups; no file deletion.
        (config.ROOT / "backups").mkdir(exist_ok=True)
        self.root = Path(
            tempfile.mkdtemp(prefix="secret-config-test-", dir=config.ROOT / "backups")
        )
        self.patch = patch.object(config, "PRIVATE", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_roundtrip_restricts_permissions(self):
        config.write_env("test.env", {"ARK_API_KEY": "synthetic-value"})
        self.assertEqual(
            config.read_env("test.env"), {"ARK_API_KEY": "synthetic-value"}
        )
        self.assertEqual((self.root / "test.env").stat().st_mode & 0o777, 0o600)

    def test_newline_rejected_without_overwriting(self):
        config.write_env("test.env", {"KEY": "original"})
        with self.assertRaises(RuntimeError):
            config.write_env("test.env", {"KEY": "value\nINJECTED=1"})
        self.assertEqual(config.read_env("test.env"), {"KEY": "original"})

    def test_symlink_refused(self):
        target = self.root / "target"
        target.write_text("original")
        (self.root / "test.env").symlink_to(target)
        with self.assertRaises(RuntimeError):
            config.write_env("test.env", {"KEY": "replacement"})
        self.assertEqual(target.read_text(), "original")


if __name__ == "__main__":
    unittest.main()


def test_reinitialization_preserves_proxy_and_stable_media_key():
    from initialize_secrets import preserve_runtime_config
    old = {'HTTPS_PROXY':'http://127.0.0.1:17890','MEDIA_PROXY_PRIVATE_KEY':'unit-test-stable-key'}
    new = preserve_runtime_config(old,{'MEDIA_PROXY_MODE':'all'})
    assert new['HTTPS_PROXY']==old['HTTPS_PROXY']
    assert new['MEDIA_PROXY_PRIVATE_KEY']==old['MEDIA_PROXY_PRIVATE_KEY']
    first=preserve_runtime_config({}, {})
    assert len(first['MEDIA_PROXY_PRIVATE_KEY']) >= 32
    assert preserve_runtime_config(first,{})['MEDIA_PROXY_PRIVATE_KEY']==first['MEDIA_PROXY_PRIVATE_KEY']
