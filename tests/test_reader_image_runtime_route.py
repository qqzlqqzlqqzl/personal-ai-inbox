import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReaderImageRuntimeRouteTests(unittest.TestCase):
    def test_polish_generates_one_narrow_cache_first_route(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node is required to evaluate the generated Workbox URL predicate")
        stage = ROOT / "src/polish_frontend.py"
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            root = Path(directory)
            web = root / "upstream/reactflux"
            paths = (
                "src/pages/Login.jsx", "src/store/settingsState.js", "index.html",
                "vite.config.js", "src/components/Sidebar/Sidebar.jsx",
            )
            (root / "runtime").mkdir()
            with tarfile.open(root / "runtime/reactflux.tar.gz", "w") as archive:
                for name in paths:
                    source = ROOT / "upstream/reactflux" / name
                    target = web / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
                    archive.add(source, arcname=f"reactflux/{name}", recursive=False)
            shutil.copytree(ROOT / "patches", root / "patches")
            (web / "src/components/Ai").mkdir()
            script = stage.read_text(encoding="utf-8").replace("/home/ubuntu/ai-news", root.as_posix())
            command = [sys.executable, "-X", "utf8", "-c", script]
            subprocess.run(command, check=True, cwd=root, capture_output=True)
            config_path = web / "vite.config.js"
            config = config_path.read_text(encoding="utf-8")
            subprocess.run(command, check=True, cwd=root, capture_output=True)
            self.assertEqual(config_path.read_text(encoding="utf-8"), config)

        self.assertEqual(config.count('cacheName: "reader-images-v1"'), 1)
        self.assertIn('cacheName: "runtime-build-assets"', config)
        self.assertIn('cacheName: "runtime-fonts"', config)
        route = config.split("runtimeCaching: [", 1)[1].split("\n          },", 1)[0] + "\n          }"
        js = """
const route = (ROUTE);
const origin = "https://reader.test";
const path = "/mf/proxy/" + "A".repeat(43) + "=/aHR0cHM6Ly9pbWcuZXhhbXBsZS9h";
const urls = [path, path + "?reader_width=480", path + "?reader_width=960",
  path + "?reader_width=1600", path + "?reader_width=12", path + "?x=1",
  path + "?reader_width=480&x=1", path + "?reader_width=480&reader_width=480",
  path + "/extra", path.replace("AAA", "ab"), "/inbox" + path, "/mf/v1/entries",
  "https://elsewhere.test" + path];
const matches = urls.map(value => {
  const url = new URL(value, origin);
  return route.urlPattern({ sameOrigin: url.origin === origin, url });
});
console.log(JSON.stringify([route.method, route.handler, route.options, matches]));
""".replace("ROUTE", route)
        output = subprocess.check_output([node, "-e", js], text=True, cwd=ROOT)
        self.assertEqual(json.loads(output), [
            "GET", "CacheFirst",
            {"cacheName": "reader-images-v1", "expiration": {"maxEntries": 300, "maxAgeSeconds": 604800},
             "cacheableResponse": {"statuses": [200]}},
            [True] * 4 + [False] * 9,
        ])


if __name__ == "__main__":
    unittest.main()
