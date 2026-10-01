#!/usr/bin/env python3
"""Offline negative tests for fail-closed source/package admission."""
import copy
import hashlib
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
        if args in (("git", "ls-files", "-v", "-z"), ("git", "ls-tree", "-r", "-z", "HEAD")):
            return ""
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


class TrackedWorkingBytesTests(unittest.TestCase):
    """Real Git index fixtures, rather than mocked status/diff assertions."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".verify-index-", dir=verify.ROOT)
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name)
        self.git("init", "--quiet")
        self.file = self.source / "internal/api/middleware.go"
        self.file.parent.mkdir(parents=True)
        self.file.write_text("package api\n// pinned authentication fixture\n")
        self.executable = self.source / "build.sh"
        self.executable.write_text("#!/bin/sh\nexit 0\n")
        self.executable.chmod(0o755)
        (self.source / "notice-link").symlink_to("build.sh")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "--quiet", "-m", "Synthetic pinned tree")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.source, text=True).strip()

    def test_exact_tracked_files_modes_and_symlink_are_accepted(self):
        verify.verify_tracked_bytes(self.source)

    def test_assume_unchanged_and_skip_worktree_are_rejected(self):
        for flag in ("assume-unchanged", "skip-worktree"):
            with self.subTest(flag=flag):
                self.git("update-index", "--" + flag, "internal/api/middleware.go")
                self.file.write_text(self.file.read_text() + "// invisible edit\n")
                self.assertEqual(self.git("diff", "--name-only"), "")
                for patched in (False, True):
                    with self.assertRaisesRegex(ValueError, "masked or abnormal index"):
                        verify.verify_tracked_bytes(self.source, patched=patched)
                self.git("update-index", "--no-" + flag, "internal/api/middleware.go")
                self.git("checkout", "--", "internal/api/middleware.go")

    def test_index_mask_is_rejected_even_without_dirty_bytes(self):
        self.git("update-index", "--assume-unchanged", "internal/api/middleware.go")
        with self.assertRaisesRegex(ValueError, "masked or abnormal index"):
            verify.verify_tracked_bytes(self.source)

    def test_actual_bytes_checked_independently_of_index_tags(self):
        original = verify.run
        self.git("update-index", "--assume-unchanged", "internal/api/middleware.go")
        self.file.write_text(self.file.read_text() + "// hidden edit\n")
        def normal_tags(*args, **kw):
            result = original(*args, **kw)
            if args == ("git", "ls-files", "-v", "-z"):
                return "\0".join("H " + row[2:] if row else row for row in result.split("\0"))
            return result
        with mock.patch.object(verify, "run", side_effect=normal_tags):
            with self.assertRaisesRegex(ValueError, "tracked working bytes drift"):
                verify.verify_tracked_bytes(self.source, patched=True)

    def test_non_security_tracked_file_bytes_are_checked(self):
        self.executable.write_text("#!/bin/sh\nexit 1\n")
        with self.assertRaisesRegex(ValueError, "tracked working bytes drift: build.sh"):
            verify.verify_tracked_bytes(self.source)

    def test_executable_mode_is_checked_despite_git_filemode_false(self):
        self.git("config", "core.filemode", "false")
        self.executable.chmod(0o644)
        self.assertEqual(self.git("diff", "--name-only"), "")
        with self.assertRaisesRegex(ValueError, "executable mode drift"):
            verify.verify_tracked_bytes(self.source)

    def test_tracked_file_replaced_by_symlink_is_rejected(self):
        self.file.unlink()
        self.file.symlink_to(self.executable)
        with self.assertRaisesRegex(ValueError, "tracked file type drift"):
            verify.verify_tracked_bytes(self.source)

    def test_tracked_symlink_target_bytes_are_checked(self):
        link = self.source / "notice-link"
        link.unlink()
        link.symlink_to("internal/api/middleware.go")
        with self.assertRaisesRegex(ValueError, "tracked working bytes drift: notice-link"):
            verify.verify_tracked_bytes(self.source)

    def test_patched_allowlist_requires_exact_bytes(self):
        pins = copy.deepcopy(verify.PINS)
        self.file.write_text("package api\n// approved patch fixture\n")
        pins["patched_sha256"]["internal/api/middleware.go"] = hashlib.sha256(self.file.read_bytes()).hexdigest()
        with mock.patch.object(verify, "PINS", pins):
            verify.verify_tracked_bytes(self.source, patched=True)
            with self.assertRaisesRegex(ValueError, "tracked working bytes drift"):
                verify.verify_tracked_bytes(self.source, patched=False)
            self.file.write_text(self.file.read_text() + "// extra edit\n")
            with self.assertRaisesRegex(ValueError, "patched tracked bytes drift"):
                verify.verify_tracked_bytes(self.source, patched=True)


if __name__ == "__main__":
    unittest.main()
