"""Offline trust-boundary fixtures; hosted Ubuntu CI supplies integration evidence."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("ci_playwright_deps", Path(__file__).with_name("ci_playwright_deps.py"))
deps = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deps)
IDENTITY = dict(schema="v1", distro="ubuntu-24.04", architecture="amd64", playwright="1.63.0")
SELECTED = dict(package="fonts-example", version="1:2.3-4", architecture="all")
PREAMBLE = ("NOTE: This is only a simulation!\n"
            "      apt-get needs root privileges for real execution.\n"
            "Reading package lists...\nBuilding dependency tree...\nReading state information...\n")
PLAN = (PREAMBLE + "0 upgraded, 1 newly installed, 0 to remove and 20 not upgraded.\n"
        "Inst fonts-example (1:2.3-4 Ubuntu:24.04/noble [all])\n"
        "Conf fonts-example (1:2.3-4 Ubuntu:24.04/noble [all])\n")
MISSING = "Missing system dependencies (2):\n  fonts-example\n  libfoo:amd64\n"


class OfficialResolverTests(unittest.TestCase):
    def test_exact_forms_and_crlf(self):
        self.assertEqual(deps.parse_official(0, "All system dependencies are installed.\n", ""), [])
        for text in [MISSING, MISSING.replace("\n", "\r\n")]:
            self.assertEqual(deps.parse_official(1, text, ""), ["fonts-example", "libfoo:amd64"])

    def test_invalid_results(self):
        cases = [(1, "Failed to install browser dependencies\nError: apt exited with code 100\n", ""),
                 (0, "", ""), (1, "", ""), (0, MISSING, ""), (2, MISSING, ""), (-9, MISSING, ""),
                 (1, MISSING, "warning\n"), (0, "All system dependencies are installed.\n", "fallback\n")]
        for code, text, stderr in cases:
            with self.subTest(code=code, text=text, stderr=stderr), self.assertRaises(ValueError):
                deps.parse_official(code, text, stderr)

    def test_counts_spacing_and_tokens(self):
        bad = [MISSING.replace("(2)", count) for count in ["(0)", "(-1)", "(02)", "(257)", "(3)"]]
        bad += [MISSING.rstrip("\n"), MISSING + "\n", MISSING + "trailer\n", "prefix\n" + MISSING,
                MISSING.replace("  fonts", " fonts"), MISSING.replace("  fonts", "   fonts"),
                MISSING.replace("libfoo:amd64", "fonts-example"),
                "Missing system dependencies (2):\n  libfoo\n  fonts-example\n",
                MISSING.replace("fonts", "\x1b[31mfonts"), MISSING.replace("\n", "\r")]
        bad += [f"Missing system dependencies (1):\n  {token}\n" for token in
                ["-evil", "a", "../bad", "pkg;id", "$(id)", 'pkg"', "pkg foo", "Pkg", "pkg:arm64",
                 "pkg:amd64:amd64", "pkg:all", "pkg/dir", "pkg\x00", "pkg\t"]]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError):
                deps.parse_official(1, text, "")

    def test_timeout_and_stdout_stderr_overflow(self):
        for script in ["import time; time.sleep(1)", "print('x' * 10000)",
                       "import sys; sys.stderr.write('x' * 10000)"]:
            with self.subTest(script=script), self.assertRaises(ValueError):
                deps.bounded([sys.executable, "-c", script], timeout=0.1, limit=1000)

    def test_normal_capture(self):
        self.assertEqual(deps.bounded([sys.executable, "-c", "print('ok')"]), (0, "ok\n", ""))

    def test_environment_guard(self):
        completed = subprocess.CompletedProcess([], 0, "amd64\n")
        for release, version, uid in [({"ID": "debian", "VERSION_ID": "13"}, "1.63.0", 1000),
                                      ({"ID": "ubuntu", "VERSION_ID": "24.04"}, "1.62.0", 1000),
                                      ({"ID": "ubuntu", "VERSION_ID": "24.04"}, "1.63.0", 0)]:
            with patch.object(deps.platform, "freedesktop_os_release", return_value=release), \
                    patch.object(deps.importlib.metadata, "version", return_value=version), \
                    patch.object(deps.os, "getuid", return_value=uid), \
                    patch.object(deps, "command", return_value=completed), self.assertRaises(ValueError):
                deps.supported_environment()


class ResolutionTests(unittest.TestCase):
    def test_simulation_includes_preamble_and_exact_operations(self):
        self.assertEqual(deps.parse_plan(PLAN), [SELECTED])

    def test_upgrade_uses_debian_comparator(self):
        upgraded = PLAN.replace("0 upgraded, 1 newly", "1 upgraded, 0 newly").replace(
            "Inst fonts-example (", "Inst fonts-example [1:2.2-9] (")
        with patch.object(deps, "command") as command:
            self.assertEqual(deps.parse_plan(upgraded), [SELECTED])
            command.assert_called_once_with("dpkg", "--compare-versions", "1:2.3-4", "ge", "1:2.2-9")
        with patch.object(deps, "command", side_effect=subprocess.CalledProcessError(1, "dpkg")), \
                self.assertRaises(subprocess.CalledProcessError):
            deps.parse_plan(upgraded)

    def test_transaction_malformed_or_incomplete(self):
        bad = [PLAN.replace("0 to remove", "1 to remove"), PLAN + "Remv libfoo [1]\n",
               PLAN.replace("1 newly", "2 newly"), PLAN.replace("0 upgraded", "1 upgraded"),
               PLAN.replace("[all]", "[arm64]"), PLAN + "Conf libother (1 Ubuntu:24.04/noble [all])\n",
               PLAN + "Inst fonts-example (1:2.3-4 Ubuntu:24.04/noble [all])\n",
               PLAN.replace("Conf fonts-example (1:2.3-4", "Conf fonts-example (1:2.3-5"),
               PLAN.replace("Inst fonts-example", "Inst --evil"), PLAN + "Inst weird\n",
               PLAN.replace("Inst fonts-example (1:2.3-4 Ubuntu:24.04/noble [all])\n", "").replace("1 newly", "0 newly"),
               PLAN.replace("Conf fonts-example (1:2.3-4 Ubuntu:24.04/noble [all])\n", ""),
               PLAN.replace("0 upgraded", "\x1b0 upgraded")]
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError):
                deps.parse_plan(text)

    def test_official_apt_and_pinned_plan_drift(self):
        for plans in [[[]], [[SELECTED], []]]:
            with patch.object(deps, "official", return_value=["fonts-example"]), \
                    patch.object(deps, "simulate", side_effect=plans), \
                    patch.object(deps, "command", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                    self.assertRaises(ValueError):
                deps.resolve(IDENTITY)

    def test_held_package(self):
        with patch.object(deps, "official", return_value=["fonts-example"]), \
                patch.object(deps, "simulate", return_value=[SELECTED]), \
                patch.object(deps, "command", return_value=subprocess.CompletedProcess([], 0, "fonts-example\n", "")), \
                self.assertRaises(ValueError):
            deps.resolve(IDENTITY)

    def test_simulation_failure_and_stderr_are_fatal(self):
        with patch.object(deps, "command", side_effect=subprocess.CalledProcessError(100, "apt-get")), \
                self.assertRaises(subprocess.CalledProcessError):
            deps.simulate(["fonts-example"])
        with patch.object(deps, "command", return_value=subprocess.CompletedProcess([], 0, PLAN, "warning")), \
                self.assertRaises(ValueError):
            deps.simulate(["fonts-example"])

    def test_empty_official_result_still_checks_pending_apt_work(self):
        with patch.object(deps, "official", return_value=[]), patch.object(deps, "simulate", return_value=[]) as simulate:
            self.assertEqual(deps.resolve(IDENTITY), dict(IDENTITY, packages=[]))
            simulate.assert_called_once_with([])
        with patch.object(deps, "official", return_value=[]), patch.object(deps, "simulate", return_value=[SELECTED]), self.assertRaises(ValueError):
            deps.resolve(IDENTITY)

    def test_metadata_contract(self):
        digest = "a" * 64
        text = ("Package: fonts-example\nVersion: 1:2.3-4\nArchitecture: all\nSize: 123\n"
                f"SHA256: {digest}\nFilename: pool/main/f/fonts-example/fonts-example_2.3-4_all.deb\n")
        expected = dict(SELECTED, size=123, sha256=digest, filename="pool/main/f/fonts-example/fonts-example_2.3-4_all.deb")
        for valid in [text, text + "\n" + text]:
            with patch.object(deps, "command", return_value=subprocess.CompletedProcess([], 0, valid, "")):
                self.assertEqual(deps.metadata(SELECTED), expected)
        bad = [text.replace("SHA256:", "MD5sum:"), text.replace("Size: 123", "Size: 0"),
               text.replace("Architecture: all", "Architecture: amd64"), text.replace("Version: 1:2.3-4", "Version: 2.3-4"),
               text.replace("pool/main", "../main"), text.replace("Filename: pool", "Filename: /pool"),
               text + f"SHA256: {digest}\n", text + "\n" + text.replace(digest, "b" * 64),
               "Package: fonts-example\nVersion: 1:2.3-4\nArchitecture: all\nStatus: install ok installed\n"]
        for value in bad:
            with self.subTest(value=value), patch.object(deps, "command", return_value=subprocess.CompletedProcess([], 0, value, "")), \
                    self.assertRaises(ValueError):
                deps.metadata(SELECTED)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self.stage = self.root / "stage"
        self.stage.mkdir()
        self.data = b"synthetic archive"
        self.record = dict(SELECTED, size=len(self.data), sha256=hashlib.sha256(self.data).hexdigest(),
                           filename="pool/main/f/fonts-example/fonts-example_2.3-4_all.deb")
        self.manifest = dict(IDENTITY, packages=[self.record])
        self.archive = self.cache / deps.archive_name(self.record)
        self.archive.write_bytes(self.data)

    def reject_before_privilege(self):
        with patch.object(deps, "command") as command, patch.object(deps, "resolve", return_value=self.manifest):
            with self.assertRaises((OSError, ValueError)):
                deps.install(self.cache, self.manifest, IDENTITY)
            command.assert_not_called()

    def test_exact_key_changes_with_every_identity_field(self):
        key = deps.cache_key(self.manifest)
        cases = []
        for field, value in [("schema", "v2"), ("distro", "ubuntu-26.04"), ("architecture", "arm64"), ("playwright", "1.64.0")]:
            cases.append(dict(self.manifest, **{field: value}))
        for field, value in [("package", "fonts-other"), ("version", "2:2.3-4"), ("architecture", "amd64"),
                             ("size", 999), ("sha256", "f" * 64), ("filename", "pool/other.deb")]:
            cases.append(dict(self.manifest, packages=[dict(self.record, **{field: value})]))
        cases.append(dict(self.manifest, packages=[]))
        for manifest in cases:
            self.assertNotEqual(key, deps.cache_key(manifest))

    def test_epoch_archive_name(self):
        self.assertEqual(self.archive.name, "fonts-example_1%3a2.3-4_all.deb")
        for field, value in [("package", "../bad"), ("version", "2/3"), ("architecture", "arm64")]:
            with self.assertRaises(ValueError):
                deps.archive_name(dict(self.record, **{field: value}))

    def test_verified_descriptor_not_reopened(self):
        original = os.fdopen
        def substitute(fd, *args, **kwargs):
            # Simulate path replacement after the non-following open. The held
            # descriptor must still supply the original verified bytes.
            if args == ("rb",):
                old = self.root / "old"
                self.archive.rename(old)
                self.archive.write_bytes(b"X" * len(self.data))
            return original(fd, *args, **kwargs)
        with patch.object(deps.os, "fdopen", side_effect=substitute):
            deps.verified_copies(self.cache, [self.record], self.stage)
        self.assertEqual((self.stage / self.archive.name).read_bytes(), self.data)

    def test_tamper_and_truncation(self):
        for value in [b"X" * len(self.data), self.data[:-1], self.data + b"X"]:
            self.archive.write_bytes(value)
            self.reject_before_privilege()

    def test_links(self):
        original = self.root / "original"
        self.archive.rename(original)
        self.archive.symlink_to(original)
        self.reject_before_privilege()
        self.archive.unlink()
        os.link(original, self.archive)
        self.reject_before_privilege()

    def test_directory_or_ancestor_symlink(self):
        for path in [self.root / "link", self.root / "parent-link" / "cache"]:
            if path.name == "link":
                path.symlink_to(self.cache, target_is_directory=True)
            else:
                path.parent.symlink_to(self.root, target_is_directory=True)
            with self.subTest(path=path), self.assertRaises(OSError):
                deps.verified_copies(path, [self.record], self.stage)

    def test_fifo_directory_missing_and_extra(self):
        self.archive.unlink()
        os.mkfifo(self.archive)
        self.reject_before_privilege()
        self.archive.unlink()
        self.archive.mkdir()
        self.reject_before_privilege()
        self.archive.rmdir()
        (self.cache / "unexpected").write_bytes(b"x")
        self.reject_before_privilege()
        self.archive.write_bytes(self.data)
        self.reject_before_privilege()

    def test_no_download_warm_and_independent_staging(self):
        calls = []
        def execute(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("sudo", "mktemp"):
                return subprocess.CompletedProcess([], 0, "/var/cache/apt/reader-playwright-abcdefgh\n")
            if args[:2] == ("sudo", "install"):
                staged = Path(args[5])
                self.assertNotEqual(staged, self.archive)
                self.assertEqual(staged.read_bytes(), self.data)
                self.archive.write_bytes(b"X" * len(self.data))
                self.assertEqual(staged.read_bytes(), self.data)
            if args[:2] == ("sudo", "apt-get"):
                for flag in ("--no-download", "--no-remove", "--no-install-recommends"):
                    self.assertIn(flag, args)
                self.assertIn("fonts-example:all=1:2.3-4", args)
                self.assertIn("Dir::Cache::archives=/var/cache/apt/reader-playwright-abcdefgh", args)
        with patch.object(deps, "command", side_effect=execute), patch.object(deps, "resolve", return_value=self.manifest), \
                patch.object(deps, "official", return_value=[]), patch.object(deps, "simulate", return_value=[SELECTED]), \
                patch.object(deps, "metadata", return_value=[self.record]):
            report = deps.install(self.cache, self.manifest, IDENTITY)
        self.assertEqual(report["source"], "verified-cache")
        self.assertEqual(len(calls), 3)

    def test_cold_destination_swap_cannot_write_outside_cache(self):
        self.archive.unlink()
        outside = self.root / "outside"
        outside.mkdir()
        def execute(*args, **kwargs):
            self.cache.rename(self.root / "old-cache")
            self.cache.symlink_to(outside, target_is_directory=True)
            (Path(kwargs["cwd"]) / self.archive.name).write_bytes(self.data)
        with patch.object(deps, "command", side_effect=execute) as command, self.assertRaises(OSError):
            deps.install(self.cache, self.manifest, IDENTITY)
        self.assertEqual(command.call_count, 1)
        self.assertEqual(list(outside.iterdir()), [])

    def test_plan_drift_after_privileged_copy_blocks_install(self):
        calls = []
        def execute(*args, **kwargs):
            calls.append(args)
            if args[:2] == ("sudo", "mktemp"):
                return subprocess.CompletedProcess([], 0, "/var/cache/apt/reader-playwright-abcdefgh\n")
        with patch.object(deps, "command", side_effect=execute), patch.object(deps, "resolve", return_value=self.manifest), \
                patch.object(deps, "simulate", return_value=[]), self.assertRaises(ValueError):
            deps.install(self.cache, self.manifest, IDENTITY)
        self.assertEqual([args[1] for args in calls], ["mktemp", "install"])

    def test_cold_download_once_and_verify(self):
        self.archive.unlink()
        calls = []
        def execute(*args, **kwargs):
            calls.append(args)
            if args[0] == "apt-get":
                self.assertIn("download", args)
                self.assertIn("fonts-example:all=1:2.3-4", args)
                (Path(kwargs["cwd"]) / self.archive.name).write_bytes(self.data)
            if args[:2] == ("sudo", "mktemp"):
                return subprocess.CompletedProcess([], 0, "/var/cache/apt/reader-playwright-abcdefgh\n")
        with patch.object(deps, "command", side_effect=execute), patch.object(deps, "resolve", return_value=self.manifest), \
                patch.object(deps, "official", return_value=[]), patch.object(deps, "simulate", return_value=[SELECTED]), \
                patch.object(deps, "metadata", return_value=[self.record]):
            report = deps.install(self.cache, self.manifest, IDENTITY)
        self.assertEqual(report["source"], "apt-download")
        self.assertEqual(sum(args[0] == "apt-get" for args in calls), 1)
        self.assertEqual(self.archive.read_bytes(), self.data)

    def test_download_failure_stays_failure(self):
        self.archive.unlink()
        with patch.object(deps, "command", side_effect=subprocess.CalledProcessError(100, "apt-get")) as command:
            with self.assertRaises(subprocess.CalledProcessError):
                deps.install(self.cache, self.manifest, IDENTITY)
            self.assertEqual(command.call_count, 1)
            self.assertEqual(command.call_args.args[0], "apt-get")

    def test_changed_plan_before_privilege(self):
        with patch.object(deps, "resolve", return_value=dict(self.manifest, packages=[])), \
                patch.object(deps, "command") as command, self.assertRaises(ValueError):
            deps.install(self.cache, self.manifest, IDENTITY)
        command.assert_not_called()

    def test_remaining_official_requirements_fail(self):
        def execute(*args, **kwargs):
            return subprocess.CompletedProcess([], 0, "/var/cache/apt/reader-playwright-abcdefgh\n")
        with patch.object(deps, "command", side_effect=execute), patch.object(deps, "resolve", return_value=self.manifest), \
                patch.object(deps, "official", return_value=["libfoo"]), patch.object(deps, "simulate", return_value=[SELECTED]), \
                patch.object(deps, "metadata", return_value=[self.record]), self.assertRaises(ValueError):
            deps.install(self.cache, self.manifest, IDENTITY)

    def test_all_archives_verified_before_any_privilege(self):
        second = dict(self.record, package="fonts-other", sha256="f" * 64)
        (self.cache / deps.archive_name(second)).write_bytes(self.data)
        self.manifest["packages"].append(second)
        self.reject_before_privilege()


class WorkflowTests(unittest.TestCase):
    def test_workflow_keeps_safety_and_all_browser_groups(self):
        workflow = (Path(__file__).parents[1] / ".github/workflows/reader-regression.yml").read_text()
        self.assertNotIn("restore-keys", workflow)
        self.assertNotIn("continue-on-error", workflow)
        for entry in ["timeout-minutes: 15", "contents: read", "persist-credentials: false",
                      "ci_cjk_font.py prepare", "ci_cjk_font.py install", "cjk_font_browser_acceptance.py",
                      "history_browser_acceptance.py", "review_console_browser.py", "review_reading_browser.py",
                      "ci_playwright_deps.py prepare", "ci_playwright_deps.py install"]:
            self.assertIn(entry, workflow)
        self.assertGreater(workflow.index("Save verified Playwright dependency archives"), workflow.index("run: python tests/review_reading_browser.py"))

    def test_fresh_update_failure_stops_before_resolution(self):
        with tempfile.TemporaryDirectory() as root:
            args = ["helper", "prepare", "--cache", root + "/reader-playwright-debs", "--manifest", root + "/reader-playwright-packages.json"]
            with patch.object(sys, "argv", args), patch.dict(os.environ, RUNNER_TEMP=root), \
                    patch.object(deps, "supported_environment", return_value=IDENTITY), \
                    patch.object(deps, "command", side_effect=subprocess.CalledProcessError(100, "apt-get")) as command, \
                    patch.object(deps, "resolve") as resolve, self.assertRaises(subprocess.CalledProcessError):
                deps.main()
            resolve.assert_not_called()
            self.assertIn("APT::Update::Error-Mode=any", command.call_args.args)
            self.assertEqual(command.call_count, 1)

    def test_reject_system_or_cached_manifest_paths(self):
        for cache, manifest in [("/usr", "/tmp/reader-playwright-packages.json"),
                                ("/tmp/reader-playwright-debs", "/tmp/reader-playwright-debs/manifest.json")]:
            with patch.object(sys, "argv", ["helper", "prepare", "--cache", cache, "--manifest", manifest]), \
                    patch.dict(os.environ, RUNNER_TEMP="/tmp"), self.assertRaises(ValueError):
                deps.main()


if __name__ == "__main__":
    unittest.main()
