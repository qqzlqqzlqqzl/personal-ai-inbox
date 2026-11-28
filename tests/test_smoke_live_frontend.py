import gzip
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


SMOKE = Path(__file__).with_name("smoke_live_frontend.py")
BUNDLE = "assets/index-current.js"


class LiveFrontendSmokeTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.stale_worker = False
        self.missing_gzip_bundle = False
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                encoding = self.headers["Accept-Encoding"]
                fixture.requests.append((self.path, encoding, self.headers["User-Agent"]))
                if self.path == "/inbox/":
                    body = f'<script src="/inbox/{BUNDLE}"></script>'.encode()
                elif self.path == "/inbox/sw.js":
                    bundle = "assets/index-old.js" if encoding == "gzip" and fixture.stale_worker else BUNDLE
                    body = f'precache([{{url:"{bundle}"}}])'.encode()
                elif self.path == f"/inbox/{BUNDLE}" and not (encoding == "gzip" and fixture.missing_gzip_bundle):
                    body = b"console.log('reader')"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                if encoding == "gzip":
                    body = gzip.compress(body)
                    self.send_header("Content-Encoding", "gzip")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.base = f"http://127.0.0.1:{server.server_port}/inbox/"

    def run_smoke(self, *args):
        return subprocess.run([sys.executable, str(SMOKE), "--base", self.base, *args],
                              capture_output=True, text=True, timeout=10)

    def test_matching_build_with_gzip_and_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "index.html").write_text(f'<script src="/{BUNDLE}"></script>')
            result = self.run_smoke("--build", temp)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(BUNDLE, result.stdout)
        self.assertCountEqual(
            [(path, encoding) for path, encoding, _ in self.requests],
            [(path, encoding) for path in ("/inbox/", "/inbox/sw.js", f"/inbox/{BUNDLE}")
             for encoding in ("gzip", "identity")],
        )
        self.assertTrue(all(agent.startswith("personal-ai-inbox-frontend-smoke/")
                            for _, _, agent in self.requests))

    def test_stale_gzipped_worker_fails(self):
        self.stale_worker = True
        result = self.run_smoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn("sw.js (gzip): assets/index-old.js", result.stderr)

    def test_local_build_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "index.html").write_text('<script src="/assets/index-other.js"></script>')
            result = self.run_smoke("--build", temp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("assets/index-other.js", result.stderr)

    def test_unavailable_bundle_encoding_fails(self):
        self.missing_gzip_bundle = True
        result = self.run_smoke()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{BUNDLE} (gzip): HTTP Error 404", result.stderr)


if __name__ == "__main__":
    unittest.main()
