"""Synthetic contract tests. All roots and failures are intentionally retained."""
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest
from urllib.request import urlopen
from urllib.error import HTTPError
from unittest.mock import patch
import uuid

import dev_fixture_workspace as w


class FakeFixture:
    def __init__(self, root, generation, *, headed=False):
        self.out = root / "generations" / generation
        self.out.mkdir()
        w.write_new(self.out / "opened.json", {"headed": headed, "env_keys": sorted(os.environ)})
    def pump(self):
        time.sleep(0.01)
    def capture(self, reason):
        w.write_new(self.out / "captured.json", {"reason": reason, "failed_evidence": "synthetic preserved"})
    def close(self):
        assert (self.out / "captured.json").is_file(), "capture must precede close"
        w.write_new(self.out / "closed.json", {"closed": True})


class Contract(unittest.TestCase):
    def setUp(self):
        self.parent = Path(tempfile.mkdtemp(prefix="reader-fixture-contract-", dir="/tmp"))
        self.build = self.parent / "static"
        self.build.mkdir()
        (self.build / "index.html").write_text("<html><body>Synthetic fixture</body></html>")
        self.digest = w.tree_manifest(self.build)[0]
        self.root = w.create_root(self.build, self.digest, self.parent)
        self.marker = w.owned_root(self.root)[1]
        self.instance = uuid.uuid4().hex
        print("RETAINED_ROOT", self.root, flush=True)

    def request(self, action="status", **changes):
        return {"workspace": self.marker["workspace"], "instance": self.instance,
                "id": uuid.uuid4().hex, "command": action, **changes}

    def launch_synthetic(self):
        self.instance = uuid.uuid4().hex
        w.write_new(self.root / f"launch-{self.instance}.json", {
            "workspace": self.marker["workspace"], "headed": False})
        process = multiprocessing.get_context("fork").Process(
            target=w.serve, args=(self.root, self.instance), kwargs={"fixture_factory": FakeFixture})
        process.start()
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            last = w.latest(self.root)
            if last and last["instance"] == self.instance and last["state"] == "running":
                return process
            if not process.is_alive():
                self.fail("synthetic worker failed to start")
            time.sleep(0.02)
        process.terminate(); process.join(timeout=2)
        self.fail("synthetic worker startup timeout")

    def test_cross_process_status_reset_stop_restart_and_retained_receipts(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-not-a-real-key", "HTTP_PROXY": "synthetic"}):
            process = self.launch_synthetic()
        try:
            first = w.command(self.root, "status", timeout=3)
            self.assertTrue(first["live"])
            first_dir = self.root / "generations" / first["generation"]
            keys = w.read_json(first_dir / "opened.json")["env_keys"]
            self.assertNotIn("OPENAI_API_KEY", keys)
            self.assertNotIn("HTTP_PROXY", keys)
            after = w.command(self.root, "reset", timeout=3)
            self.assertNotEqual(first["generation"], after["generation"])
            self.assertEqual(w.read_json(first_dir / "captured.json")["reason"], "reset")
            self.assertTrue((first_dir / "closed.json").is_file())
            self.assertEqual(w.command(self.root, "stop", timeout=3)["state"], "stopped")
            process.join(timeout=3)
            self.assertEqual(process.exitcode, 0)
            self.assertFalse(w.command(self.root, "status")["live"])
            old_receipts = {p: p.read_bytes() for p in (self.root / "receipts").glob("*.json")}
            restarted = self.launch_synthetic()
            try:
                again = w.command(self.root, "status", timeout=3)
                self.assertNotEqual(again["instance"], first["instance"])
                self.assertEqual(w.command(self.root, "stop", timeout=3)["state"], "stopped")
                restarted.join(timeout=3)
                self.assertEqual(restarted.exitcode, 0)
            finally:
                if restarted.is_alive(): restarted.terminate(); restarted.join(timeout=3)
            for path, data in old_receipts.items(): self.assertEqual(path.read_bytes(), data)
        finally:
            if process.is_alive(): process.terminate(); process.join(timeout=3)

    def test_unknown_cross_instance_commands_do_not_mutate_session(self):
        session = w.Session(self.root, self.marker, self.instance, FakeFixture)
        session.start()
        for change in ({"command": "delete"}, {"command": []}, {"workspace": "other"}, {"instance": "other"},
                       {"id": "../../config"}, {"id": None}, {"root": "/production"}):
            with self.subTest(change=change), self.assertRaises(w.Rejected):
                session.handle(self.request(**change))
        self.assertIsNotNone(session.fixture)
        session.handle(self.request("stop"))

    def test_stale_running_receipt_never_proves_liveness(self):
        (self.root / "commands" / self.instance).mkdir()
        w.receipt(self.root, self.marker, self.instance, "running", pid=os.getpid())
        with self.assertRaisesRegex(w.Rejected, "stale receipts"):
            w.command(self.root, "status", timeout=0.06)

    def test_new_root_and_build_pin_fail_closed(self):
        with self.assertRaisesRegex(w.Rejected, "SHA256"):
            w.create_root(self.build, "0" * 64, self.parent)
        session = w.Session(self.root, self.marker, self.instance, FakeFixture)
        (self.root / "build" / "index.html").write_text("changed")
        with self.assertRaisesRegex(w.Rejected, "pinned build"):
            session.start()

    def test_reset_preserves_old_evidence_before_new_build_failure(self):
        session = w.Session(self.root, self.marker, self.instance, FakeFixture)
        session.start()
        old = self.root / "generations" / session.generation
        (self.root / "build" / "index.html").write_text("changed")
        with self.assertRaises(w.Rejected): session.handle(self.request("reset"))
        self.assertTrue((old / "captured.json").is_file())
        self.assertTrue((old / "closed.json").is_file())
        self.assertEqual(w.latest(self.root)["state"], "failed")

    def test_symlink_root_and_control_member_rejected(self):
        link = self.parent / "reader-fixture-link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(w.Rejected): w.owned_root(link)
        (self.root / "responses" / "foreign.json").symlink_to(self.parent / "missing")
        with self.assertRaises(w.Rejected): w.owned_root(self.root)

    def test_symlink_build_and_credentials_file_rejected(self):
        (self.build / "link").symlink_to(self.build / "index.html")
        with self.assertRaises(w.Rejected): w.tree_manifest(self.build)
        other = self.parent / "other-build"; other.mkdir()
        (other / "index.html").write_text("x")
        (other / ".env").write_text("SYNTHETIC_FIXTURE_ONLY=1")
        with self.assertRaises(w.Rejected): w.tree_manifest(other)

    def test_production_config_relative_traversal_and_unowned_roots_rejected(self):
        for path in ("/home/ubuntu/ai-news/dist", "/tmp/production", "/tmp/config", "/tmp/.private",
                     "/etc", "relative", str(self.parent / ".." / "other")):
            with self.subTest(path=path), self.assertRaises(w.Rejected): w.checked_path(path, temp=True)
        with self.assertRaises(w.Rejected): w.owned_root(self.parent)
        self.root.chmod(0o755)
        with self.assertRaises(w.Rejected): w.owned_root(self.root)

    def test_root_receipt_cannot_be_reused_in_other_root(self):
        copied = self.parent / "reader-fixture-copied"; copied.mkdir(mode=0o700)
        w.write_new(copied / "owner.json", self.marker)
        with self.assertRaisesRegex(w.Rejected, "identity"):
            w.owned_root(copied)

    def test_json_rejects_duplicate_nonfinite_oversize_and_symlink(self):
        for n, raw in enumerate((b'{"x":1,"x":2}', b'{"x":NaN}', b'"not object"', b' ' * (w.LIMIT + 1))):
            path = self.parent / f"bad-{n}.json"; path.write_bytes(raw)
            with self.subTest(n=n), self.assertRaises(w.Rejected): w.read_json(path)
        path = self.parent / "link.json"; path.symlink_to(self.root / "owner.json")
        with self.assertRaises(OSError): w.read_json(path)

    def test_receipt_write_is_exclusive_and_preserves_original(self):
        path = self.parent / "receipt.json"
        w.write_new(path, {"x": 1})
        before = path.read_bytes()
        with self.assertRaises(FileExistsError): w.write_new(path, {"x": 2})
        self.assertEqual(path.read_bytes(), before)

    def test_environment_allowlist_and_headed_validation(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic", "AI_NEWS_ROOT": "/production", "DISPLAY": ":9"}):
            env = w.clean_env(self.root)
        self.assertEqual(set(env), {"PATH", "HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
                                   "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "LANG", "AI_NEWS_TEST_BUILD"})
        for value in (None, "host:0", "tcp/host:0"):
            with self.assertRaises(w.Rejected): w.clean_env(self.root, headed=True, display=value)
        self.assertEqual(w.clean_env(self.root, headed=True, display=":0")["DISPLAY"], ":0")

    def test_route_allowlist_default_deny_and_external_http(self):
        base = "http://127.0.0.1:12345"
        for url in ("https://example.test", "http://localhost:12345/mf/v1/me", "http://127.0.0.1:12346/mf/v1/me"):
            self.assertEqual(w.route_kind(url, "GET", base), "external")
        for path in ("/mf/v1/no-such-api", "/mf/v1/ai/settings/extra", "/mf/v1/%61i/settings", "/api/test", "/v1/test"):
            self.assertEqual(w.route_kind(base + path, "GET", base), "unsupported")
        self.assertEqual(w.route_kind(base + "/mf/v1/ai/settings", "DELETE", base), "method")
        self.assertEqual(w.route_kind(base + "/mf/v1/ai/settings", "PUT", base), "fixture")
        self.assertEqual(w.route_kind(base + "/inbox/assets/main.js", "GET", base), "static")
        self.assertEqual(w.route_kind(base + "/inbox/assets/main.js", "POST", base), "method")

    def test_unknown_api_is_explicit_error_not_empty_success(self):
        class Request:
            url = "http://127.0.0.1:12345/mf/v1/unknown"; method = "GET"
        class Route:
            request = Request()
            def fulfill(self, **kwargs): self.result = kwargs
            def abort(self, *args): self.aborted = True
        fixture = w.ControlledFixture.__new__(w.ControlledFixture)
        fixture.blocked = []
        fixture.h = type("Fake", (), {"base": "http://127.0.0.1:12345"})()
        route = Route(); fixture.route(route)
        self.assertEqual(route.result, {"status": 501, "json": {"error": "unsupported_fixture_api"}})
        route.request.url = "https://example.test/unknown"; fixture.route(route)
        self.assertTrue(route.aborted)

    def test_fifo_control_input_rejected_without_blocking(self):
        fifo = self.root / "commands" / "fifo"
        os.mkfifo(fifo)
        with self.assertRaises(w.Rejected): w.owned_root(self.root)
        with self.assertRaises(w.Rejected): w.read_json(fifo)

    def test_capture_failure_prevents_reset_or_close(self):
        class FailedCapture(FakeFixture):
            def capture(self, reason): raise OSError("synthetic evidence write failure")
        session = w.Session(self.root, self.marker, self.instance, FailedCapture)
        session.start()
        generation = session.generation
        with self.assertRaises(OSError): session.handle(self.request("reset"))
        self.assertEqual(session.generation, generation)
        self.assertIsNotNone(session.fixture)
        self.assertFalse((self.root / "generations" / generation / "closed.json").exists())

    def test_worker_rejects_unknown_command_and_keeps_usable_session(self):
        process = self.launch_synthetic()
        try:
            request = self.request("delete")
            w.write_new(self.root / "commands" / self.instance / (request["id"] + ".json"), request)
            path = self.root / "responses" / (request["id"] + ".json")
            deadline = time.monotonic() + 3
            while not path.exists() and time.monotonic() < deadline: time.sleep(0.02)
            self.assertFalse(w.read_json(path)["ok"])
            self.assertTrue(w.command(self.root, "status", timeout=3)["live"])
            w.command(self.root, "stop", timeout=3)
            process.join(timeout=3)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive(): process.terminate(); process.join(timeout=3)

    def test_active_worker_excludes_second_worker(self):
        process = self.launch_synthetic()
        try:
            another = uuid.uuid4().hex
            w.write_new(self.root / f"launch-{another}.json", {
                "workspace": self.marker["workspace"], "headed": False})
            # In-process probe preserves the caller's environment and signal handler.
            import signal
            handler = signal.getsignal(signal.SIGTERM)
            try:
                with patch.dict(os.environ, os.environ.copy()), self.assertRaisesRegex(w.Rejected, "active worker"):
                    w.serve(self.root, another, fixture_factory=FakeFixture)
            finally:
                signal.signal(signal.SIGTERM, handler)
            self.assertTrue(w.command(self.root, "status", timeout=3)["live"])
            w.command(self.root, "stop", timeout=3)
            process.join(timeout=3)
        finally:
            if process.is_alive(): process.terminate(); process.join(timeout=3)

    def test_real_harness_adapter_routes_headed_and_static_loopback_with_fake_browser(self):
        import review_reader_harness as harness
        class Page:
            def set_default_timeout(self, *a): pass
            def on(self, *a): pass
            def goto(self, *a, **kw): pass
            def wait_for_timeout(self, *a): pass
            def screenshot(self, path, **kw): Path(path).write_bytes(b"synthetic screenshot")
            def evaluate(self, *a): return {"synthetic": "session"}
        class Context:
            def __init__(self): self.options = {}; self.routes = []
            def add_init_script(self, script): self.script = script
            def route(self, *a): self.routes.append(a)
            def unroute_all(self, **kw): self.routes.clear()
            def route_web_socket(self, *a): self.ws = a
            def new_page(self): return Page()
            def storage_state(self): return {"synthetic": True}
        class Browser:
            def new_context(self, **options):
                self.ctx = Context(); self.ctx.options = options; return self.ctx
            def close(self): self.closed = True
        class Chromium:
            def launch(self, **kw): self.options = kw; self.browser = Browser(); return self.browser
        class Playwright:
            def __init__(self): self.chromium = Chromium()
            def stop(self): self.stopped = True
        class Starter:
            def __init__(self): self.pw = Playwright()
            def start(self): return self.pw
        starter = Starter()
        with patch.dict(os.environ, w.clean_env(self.root), clear=True), patch.object(harness, "sync_playwright", lambda: starter), patch.object(harness, "ROOT", self.root):
            fixture = w.ControlledFixture(self.root, "adapter-test", headed=True)
            try:
                self.assertFalse(starter.pw.chromium.options["headless"])
                self.assertEqual(fixture.h.ctx.options["service_workers"], "block")
                self.assertFalse(fixture.h.ctx.options["accept_downloads"])
                self.assertEqual(fixture.h.ctx.routes, [("**/*", fixture.route)])
                self.assertEqual(fixture.h.server.server_address[0], "127.0.0.1")
                with urlopen(fixture.h.base + "/inbox/", timeout=2) as response:
                    self.assertIn(b"Synthetic fixture", response.read())
                with self.assertRaises(HTTPError) as raised:
                    urlopen(fixture.h.base + "/mf/v1/me", timeout=2)
                self.assertEqual(raised.exception.code, 501)
                fixture.capture("reset")
                snapshots = list(fixture.out.glob("*-reset.json"))
                self.assertEqual(len(snapshots), 1)
                self.assertEqual(w.read_json(snapshots[0])["session_storage"], {"synthetic": "session"})
            finally:
                fixture.close()
            self.assertTrue(starter.pw.chromium.browser.closed)
            self.assertTrue(starter.pw.stopped)


if __name__ == "__main__":
    unittest.main(verbosity=2)
