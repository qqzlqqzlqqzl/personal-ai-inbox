"""Offline safety checks: no unverified cache bytes reach a sudo invocation."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("ci_cjk_font", Path(__file__).with_name("ci_cjk_font.py"))
font = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(font)


class CjkFontCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.data = b"synthetic font archive"
        self.record = dict(package=font.PACKAGE, version="1:20220127+repack1-1",
                           architecture="all", size=len(self.data), sha256=hashlib.sha256(self.data).hexdigest(),
                           filename="fonts-noto-cjk_20220127+repack1-1_all.deb")
        self.archive = self.cache / font.CACHED_FILE

    def reject_before_sudo(self):
        with patch.object(font, "command") as command:
            with self.assertRaises((ValueError, OSError)):
                font.install(self.cache, self.record)
            command.assert_not_called()

    def test_valid_cached_bytes_are_copied_before_install_without_download(self):
        self.archive.write_bytes(self.data)
        def execute(*args, **kwargs):
            if args[1] == "install":
                self.assertEqual(args[:5], ("sudo", "install", "-m", "0644", "--"))
                staged = Path(args[5])
                self.assertNotEqual(staged, self.archive)
                self.assertEqual(staged.read_bytes(), self.data)
                self.assertEqual(args[6], "/var/cache/apt/archives/fonts-noto-cjk_1%3a20220127+repack1-1_all.deb")
                # Mutation after validation cannot change the independent install copy.
                self.archive.write_bytes(b"X" * len(self.data))
                self.assertEqual(staged.read_bytes(), self.data)
            else:
                self.assertEqual(args, ("sudo", "apt-get", "install", "-y", "--no-download",
                                        f"{font.PACKAGE}={self.record['version']}"))
        with patch.object(font, "command", side_effect=execute) as command:
            font.install(self.cache, self.record)
            self.assertEqual(command.call_count, 2)

    def test_cold_cache_downloads_exact_version_and_verifies_before_sudo(self):
        calls = []
        def execute(*args, **kwargs):
            calls.append(args)
            if args[0] == "apt-get":
                self.assertEqual(args, ("apt-get", "download", f"{font.PACKAGE}={self.record['version']}"))
                Path(kwargs["cwd"], "fonts-noto-cjk_1%3a20220127+repack1-1_all.deb").write_bytes(self.data)
            elif args[1] == "install":
                self.assertEqual(Path(args[5]).read_bytes(), self.data)
            else:
                self.assertIn("--no-download", args)
        with patch.object(font, "command", side_effect=execute):
            font.install(self.cache, self.record)
        self.assertEqual([call[0] for call in calls], ["apt-get", "sudo", "sudo"])
        self.assertEqual(self.archive.read_bytes(), self.data)

    def test_download_failure_stays_failure(self):
        with patch.object(font, "command", side_effect=subprocess.CalledProcessError(100, "apt-get")) as command:
            with self.assertRaises(subprocess.CalledProcessError):
                font.install(self.cache, self.record)
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[0], "apt-get")

    def test_same_size_tampering_is_rejected(self):
        self.archive.write_bytes(b"X" * len(self.data))
        self.reject_before_sudo()

    def test_wrong_size_is_rejected(self):
        self.archive.write_bytes(self.data + b"X")
        self.reject_before_sudo()

    def test_symlink_file_is_rejected(self):
        target = self.root / "valid.deb"
        target.write_bytes(self.data)
        self.archive.symlink_to(target)
        self.reject_before_sudo()

    def test_symlink_directory_is_rejected(self):
        self.archive.write_bytes(self.data)
        link = self.root / "link"
        link.symlink_to(self.cache, target_is_directory=True)
        self.cache = link
        self.reject_before_sudo()

    def test_symlink_ancestor_is_rejected(self):
        self.archive.write_bytes(self.data)
        link = self.root / "link"
        link.symlink_to(self.root, target_is_directory=True)
        self.cache = link / "cache"
        self.reject_before_sudo()

    def test_hardlink_is_rejected(self):
        self.archive.write_bytes(self.data)
        os.link(self.archive, self.root / "other.deb")
        self.reject_before_sudo()

    def test_fifo_is_rejected_without_blocking(self):
        os.mkfifo(self.archive)
        self.reject_before_sudo()

    def test_extra_file_is_rejected(self):
        self.archive.write_bytes(self.data)
        (self.cache / "unexpected").write_text("no")
        self.reject_before_sudo()

    def test_version_hash_distribution_and_architecture_change_cache_key(self):
        release = {"ID": "ubuntu", "VERSION_ID": "24.04"}
        key = font.cache_key(self.record, release, "amd64")
        for field, value in [("version", "2:20260101-1"), ("sha256", "f" * 64)]:
            self.assertNotEqual(key, font.cache_key(dict(self.record, **{field: value}), release, "amd64"))
        self.assertNotEqual(key, font.cache_key(self.record, dict(release, VERSION_ID="26.04"), "amd64"))
        self.assertNotEqual(key, font.cache_key(self.record, release, "arm64"))
        self.assertIn(self.record["sha256"], key)

    def test_old_version_bytes_fail_new_version_hash(self):
        self.archive.write_bytes(self.data)
        self.record.update(version="2:20260101-1", sha256="f" * 64)
        self.reject_before_sudo()

    def test_changed_apt_candidate_is_rejected_before_install(self):
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps(self.record))
        argv = ["ci_cjk_font.py", "install", "--cache", str(self.cache), "--manifest", str(manifest)]
        with patch.object(sys, "argv", argv), patch.object(font, "metadata", return_value=dict(self.record, version="2:1")), \
                patch.object(font, "install") as install:
            with self.assertRaises(ValueError):
                font.main()
            install.assert_not_called()

    def test_metadata_requires_full_consistent_apt_digest_record(self):
        stanza = (f"Package: {font.PACKAGE}\nVersion: {self.record['version']}\nArchitecture: all\n"
                  f"Size: {self.record['size']}\nSHA256: {self.record['sha256']}\n"
                  f"Filename: pool/main/f/fonts-noto-cjk/{self.record['filename']}\n")
        def responses(body):
            return [subprocess.CompletedProcess([], 0, f"  Candidate: {self.record['version']}\n"),
                    subprocess.CompletedProcess([], 0, body)]
        with patch.object(font, "command", side_effect=responses(stanza)):
            self.assertEqual(font.metadata(), self.record)
        with patch.object(font, "command", side_effect=responses(stanza + "\n" + stanza)):
            self.assertEqual(font.metadata(), self.record)
        status_only = (f"Package: {font.PACKAGE}\nStatus: install ok installed\n"
                       f"Version: {self.record['version']}\nArchitecture: all\n")
        for invalid in [stanza.replace("SHA256:", "MD5sum:"), stanza.replace("Architecture: all", "Architecture: amd64"),
                        stanza + "\n" + stanza.replace(self.record["sha256"], "f" * 64),
                        status_only, stanza + "\n" + status_only]:
            with patch.object(font, "command", side_effect=responses(invalid)):
                with self.assertRaises(ValueError):
                    font.metadata()


if __name__ == "__main__":
    unittest.main()
