#!/usr/bin/env python3
"""Offline negative tests for fail-closed source/package admission."""
import copy
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest import mock

import verify


class CompatibilityGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".verify-test-", dir=verify.ROOT)
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        (self.source / "go.mod").write_text("module miniflux.app/v2\n\ngo 1.26.0\n")
        self.head = verify.PINS["upstream_commit"]
        self.origin = verify.PINS["upstream_repository"]
        self.blobs = copy.deepcopy(verify.PINS["upstream_blobs"])
        self.status = ""
        self.others = ""

    def git(self, *args, cwd=None):
        self.assertEqual(cwd, self.source)
        if args == ("git", "rev-parse", "HEAD"):
            return self.head
        if args == ("git", "remote", "get-url", "origin"):
            return self.origin
        if args[:2] == ("git", "rev-parse") and args[2].startswith("HEAD:"):
            return self.blobs[args[2][5:]]
        if args == ("git", "status", "--porcelain", "--untracked-files=all"):
            return self.status
        if args == ("git", "ls-files", "--others"):
            return self.others
        self.fail(f"unexpected gate command: {args}")

    def check_source(self):
        with mock.patch.object(verify, "run", side_effect=self.git):
            verify.verify_source(self.source)

    def test_exact_pristine_pin_is_accepted(self):
        self.check_source()

    def test_other_commit_is_rejected_even_with_same_files(self):
        self.head = "0" * 40
        with self.assertRaisesRegex(ValueError, "unsupported upstream commit"):
            self.check_source()

    def test_other_origin_is_rejected(self):
        self.origin = "https://example.invalid/miniflux/v2.git"
        with self.assertRaisesRegex(ValueError, "unapproved source origin"):
            self.check_source()

    def test_auth_blob_drift_is_rejected(self):
        self.blobs["internal/api/middleware.go"] = "0" * 40
        with self.assertRaisesRegex(ValueError, "upstream blob drift"):
            self.check_source()

    def test_dirty_tree_is_rejected(self):
        for status in (" M internal/api/middleware.go", "?? internal/api/injected.go"):
            self.status = status
            with self.assertRaisesRegex(ValueError, "pristine"):
                self.check_source()

    def test_ignored_go_source_cannot_bypass_admission(self):
        subprocess.run(["git", "init", "--quiet", str(self.source)], check=True)
        (self.source / ".git/info/exclude").write_text("go.mod\nhidden.go\n")
        (self.source / "hidden.go").write_text("package main\n")
        hidden = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard"], cwd=self.source, text=True)
        self.assertEqual(hidden, "", "fixture must be invisible to the former check")
        self.others = subprocess.check_output(["git", "ls-files", "--others"], cwd=self.source, text=True).strip()
        self.assertIn("hidden.go", self.others)
        with self.assertRaisesRegex(ValueError, "including ignored build inputs"):
            self.check_source()

    def test_toolchain_requirement_drift_is_rejected(self):
        (self.source / "go.mod").write_text("module miniflux.app/v2\n\ngo 1.27.0\n")
        with self.assertRaisesRegex(ValueError, "Go requirement drift"):
            self.check_source()

    def test_source_symlink_is_rejected(self):
        link = self.source / "linked"
        link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlinked"):
            verify.verify_source(link)

    def test_exact_package_inventory_is_accepted(self):
        verify.verify_package()

    def test_patch_digest_drift_is_rejected(self):
        pins = copy.deepcopy(verify.PINS)
        pins["patch_sha256"] = "0" * 64
        with mock.patch.object(verify, "PINS", pins):
            with self.assertRaisesRegex(ValueError, "patch digest drift"):
                verify.verify_package()

    def test_missing_or_extra_overlay_is_rejected(self):
        pins = copy.deepcopy(verify.PINS)
        pins["test_sha256"]["internal/api/unexpected_test.go"] = "0" * 64
        with mock.patch.object(verify, "PINS", pins):
            with self.assertRaisesRegex(ValueError, "inventory drift"):
                verify.verify_package()

    def test_test_content_drift_is_rejected(self):
        pins = copy.deepcopy(verify.PINS)
        name = next(iter(pins["test_sha256"]))
        pins["test_sha256"][name] = "0" * 64
        with mock.patch.object(verify, "PINS", pins):
            with self.assertRaisesRegex(ValueError, "test digest drift"):
                verify.verify_package()


if __name__ == "__main__":
    unittest.main()
