"""Real headed Chromium acceptance of the fixture CLI; run under Xvfb after build.

timeout 600s xvfb-run -a python tests/dev_fixture_workspace_browser_acceptance.py

No browser fallback, installation, mocks, skip-to-green, or product/tool edits.
The original build stays unchanged. A separately hashed copy receives a local
synthetic DOM probe, so the derived page is NOT byte-identical to a release.
All test roots, evidence and failure screenshots are retained.
"""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler
import zlib

import dev_fixture_workspace as tool

ROOT = Path(__file__).resolve().parents[1]
TOOL = Path(__file__).with_name("dev_fixture_workspace.py")
TOOL_SHA = "068ba17339b5e40c82c0a87a719ea95b72b464ac75974abc2ff656c440c89ba4"
HARNESS_SHA = "0b16515d233b8009d1756f951717aab288b339df24c2b48674586f972d7aa7ca"
SRC = "a37afbb0984593aa75d2c4b3d2dcfd3f07f17d38"
KEY = "reader.fixture.browser.acceptance.v1"
NOTE_ID = 987654321
# The in-page probe has a 12s app deadline and bounded local requests. This
# retained-context wait is not a success check; captured assertions below are.
PROBE_SETTLE_SECONDS = 30


class NotRun(RuntimeError):
    """Required real browser/display is absent; never translate this to a pass."""


def require(value, message):
    if not value:
        raise AssertionError(message)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(*args):
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True, timeout=15).strip()


def output_directory():
    tool.checked_path(ROOT)
    require(ROOT != Path("/home/ubuntu/ai-news") and not (ROOT / ".private").exists(),
            "refuse deployment/private-config checkout")
    runtime = ROOT / "runtime"
    runtime.mkdir(exist_ok=True)
    tool.checked_path(runtime)
    parent = runtime / "dev-fixture-workspace-browser"
    parent.mkdir(exist_ok=True)
    tool.checked_path(parent)
    return Path(tempfile.mkdtemp(prefix="run-", dir=parent))


def pinned_browser():
    require(not os.environ.get("CHROMIUM_EXECUTABLE"), "browser executable override forbidden")
    require(metadata.version("playwright") == "1.63.0", "Playwright must match the locked version")
    import playwright
    from playwright.sync_api import sync_playwright
    registry = json.loads((Path(playwright.__file__).parent / "driver/package/browsers.json").read_text())
    rows = [x for x in registry["browsers"] if x["name"] in {"chromium", "chromium-headless-shell"}]
    require(len(rows) == 2 and all(x["revision"] == "1243" and x["browserVersion"] == "153.0.8010.12" for x in rows),
            "wrong pinned Chromium descriptors")
    # Ask the installed pinned driver for its one executable path. Do not scan
    # other caches, use system Chromium, or choose an alternate executable.
    with sync_playwright() as pw:
        executable = Path(pw.chromium.executable_path)
    require(executable.parent.parent.name == "chromium-1243", "unexpected pinned executable layout")
    if not executable.is_file():
        raise NotRun("pinned Chromium1243 executable missing: " + str(executable))
    tool.checked_path(executable, directory=False)
    display = os.environ.get("DISPLAY", "")
    if not re.fullmatch(r":\d+(?:\.\d+)?", display):
        raise NotRun("headed Xvfb requires an explicit local DISPLAY")
    if not Path("/tmp/.X11-unix/X" + display[1:].split(".")[0]).exists():
        raise NotRun("local Xvfb display socket missing")
    return executable.parent.parent.parent, display, executable


def process_identity(pid):
    directory = Path("/proc") / str(pid)
    require(directory.stat().st_uid == os.getuid(), "test process ancestry must belong to current UID")
    fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
    started = time.time() - time.clock_gettime(time.CLOCK_BOOTTIME) + int(fields[19]) / os.sysconf("SC_CLK_TCK")
    argv = (directory / "cmdline").read_bytes().split(b"\0")
    return int(fields[1]), started, [os.fsdecode(x) for x in argv if x]


def temporary_xvfb_authority(root, display):
    """Copy only this invocation's authenticated temporary display cookie.

    Never use a user's existing authority, log cookie bytes/digests, change X
    access controls, or export this private HOME in test artifacts.
    """
    value = os.environ.get("XAUTHORITY")
    if not value:
        raise NotRun("xvfb-run temporary XAUTHORITY was not supplied")
    source = tool.checked_path(value, directory=False)
    wrapper = Path("/usr/bin/xvfb-run")
    server = Path("/usr/bin/Xvfb")
    for executable in (wrapper, server):
        tool.checked_path(executable, directory=False)
        info = executable.stat()
        require(info.st_uid == 0 and not info.st_mode & 0o022, "untrusted system Xvfb executable")
    pid = os.getppid(); wrapper_pid = None; started = None
    for _ in range(16):
        parent, birth, argv = process_identity(pid)
        if str(wrapper) in argv:
            require(not any(x in {"-f", "--auth-file"} or x.startswith("--auth-file=") for x in argv),
                    "pre-existing/custom Xauthority input forbidden")
            wrapper_pid, started = pid, birth
            break
        if parent <= 1: break
        pid = parent
    require(wrapper_pid is not None, "cannot prove this process belongs to the current system xvfb-run")
    require(source.parent.name.startswith("xvfb-run.") and source.name == "Xauthority",
            "authority is not the current wrapper's temporary file")
    directory = source.parent.stat(); info = source.stat()
    require(directory.st_uid == os.getuid() and stat.S_IMODE(directory.st_mode) == 0o700,
            "temporary authority directory must be private and owned")
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1,
            "temporary authority file must be private, owned, and not hardlinked")
    require(directory.st_ctime >= started - 1 and info.st_mtime >= started - 1,
            "authority predates this xvfb-run invocation")
    children = (Path("/proc") / str(wrapper_pid) / "task" / str(wrapper_pid) / "children").read_text().split()
    matches = []
    display_number = display.split(".")[0]
    for child in children:
        parent, _, argv = process_identity(int(child))
        if parent != wrapper_pid or not argv or Path(argv[0]).name != "Xvfb": continue
        require((Path("/proc") / child / "exe").resolve() == server, "unexpected Xvfb executable")
        if display_number in argv and "-auth" in argv:
            at = argv.index("-auth")
            if at + 1 < len(argv) and argv[at + 1] == str(source): matches.append(int(child))
    require(len(matches) == 1, "current live Xvfb does not bind this exact temporary authority/display")
    with tool.directory_fd(source.parent) as parent:
        fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as stream:
            observed = os.fstat(stream.fileno())
            require((observed.st_dev, observed.st_ino) == (info.st_dev, info.st_ino), "authority changed during admission")
            content = stream.read(32769)
    require(0 < len(content) <= 32768, "invalid temporary authority size")
    # Xauthority binary fields are network-order uint16 length/value records.
    # Admit only this local display's MIT cookie, never an unrelated auth entry.
    offset = 0; records = 0
    while offset < len(content):
        require(offset + 2 <= len(content), "truncated authority family")
        family = struct.unpack_from(">H", content, offset)[0]; offset += 2
        values = []
        for _ in range(4):
            require(offset + 2 <= len(content), "truncated authority length")
            size = struct.unpack_from(">H", content, offset)[0]; offset += 2
            require(offset + size <= len(content), "truncated authority value")
            values.append(content[offset:offset + size]); offset += size
        address, number, protocol, cookie = values
        require(family == 256 and address == socket.gethostname().encode() and number == display_number[1:].encode()
                and protocol == b"MIT-MAGIC-COOKIE-1" and len(cookie) == 16, "authority includes an unrelated display or credential")
        records += 1
    require(1 <= records <= 4, "unexpected temporary authority record count")
    tool.owned_root(root)
    with tool.directory_fd(root / "home") as parent:
        fd = os.open(".Xauthority", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
    # Only non-secret provenance is returned. No hash of the cookie is emitted.
    return {"temporary_display_auth_verified": True, "wrapper_pid": wrapper_pid, "xvfb_pid": matches[0],
            "display": display_number, "same_uid_private_home": True, "exported": False}


def probe_script(sentinel):
    config = json.dumps({"key": KEY, "note": NOTE_ID, "sentinel": sentinel})
    return "const cfg = " + config + ";\n" + r"""
(() => {
  const result = {schema:1, nonce:crypto.randomUUID(), time_origin_ms:performance.timeOrigin,
    started_ms:Date.now(), origin:location.origin, user_agent:navigator.userAgent,
    previous_marker:sessionStorage.getItem(cfg.key), passed:false};
  const box = document.createElement('section');
  box.id = 'fixture-workspace-acceptance';
  box.setAttribute('role', 'status');
  Object.assign(box.style, {position:'fixed', top:'8px', left:'8px', zIndex:'2147483647',
    maxWidth:'95vw', padding:'12px', background:'#fff', color:'#111', border:'3px solid #b00020',
    font:'16px sans-serif', whiteSpace:'pre-wrap'});
  box.textContent = 'Controlled fixture browser acceptance: running';
  document.body.appendChild(box);
  const save = () => sessionStorage.setItem(cfg.key, JSON.stringify(result));
  const check = (ok, why) => { if (!ok) throw new Error(why); };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const bounded = (promise, ms) => Promise.race([promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error('probe deadline')), ms))]);
  const request = (path, options={}) => fetch(path, {...options, signal:AbortSignal.timeout(2000)});
  save();
  (async () => {
    const deadline = Date.now() + 12000;
    while (![...document.querySelectorAll('button')].some(e => e.getAttribute('aria-label') === 'AI 精选' && e.isConnected && e.offsetWidth > 0 && e.offsetHeight > 0)) {
      check(Date.now() < deadline, 'real Reader toolbar did not mount');
      await sleep(50);
    }
    result.reader_toolbar_mounted = true;
    check(!navigator.userAgent.includes('HeadlessChrome'), 'expected real headed Chromium');
    const me = await request('/mf/v1/me'); result.known_status = me.status;
    const owner = await me.json(); check(me.status === 200 && owner.id === 1, 'controlled known fixture API failed');
    const unknown = await request('/mf/v1/fixture-unsupported-browser-probe');
    result.unknown_status = unknown.status; result.unknown_body = await unknown.json();
    check(unknown.status === 501 && result.unknown_body.error === 'unsupported_fixture_api', 'unknown API was not explicit failure');
    const message = 'Unsupported fixture API: 501 unsupported_fixture_api';
    box.textContent = message;
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const rect = box.getBoundingClientRect(); const style = getComputedStyle(box);
    result.failure_visible = {text:box.textContent, rect:rect.toJSON(),
      visible:rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.bottom <= innerHeight &&
        style.display !== 'none' && style.visibility === 'visible'};
    check(result.failure_visible.visible && box.textContent === message, '501 failure not visible in the real DOM');
    const denied = await request('/mf/v1/ai/settings', {method:'DELETE'});
    result.denied_method_status = denied.status; check(denied.status === 405, 'unsupported method not denied');
    try {
      await request(cfg.sentinel + '/browser-must-not-arrive/' + result.nonce, {mode:'no-cors'});
      result.external_rejected = false;
    } catch (error) { result.external_rejected = error.name === 'TypeError'; }
    check(result.external_rejected, 'external HTTP was not blocked before delivery');
    check(!!navigator.serviceWorker, 'service-worker API unavailable in real Chromium');
    const registration = await bounded(navigator.serviceWorker.register('/inbox/_fixture_acceptance_sw.js',
      {scope:'/inbox/fixture-acceptance-only/'}), 2000);
    result.service_workers_blocked = registration === undefined && (await navigator.serviceWorker.getRegistrations()).length === 0;
    check(result.service_workers_blocked, 'service worker not blocked by the controlled context');
    const note = await request('/mf/v1/ai/notes/' + cfg.note, {method:'PUT',
      headers:{'content-type':'application/json'}, body:JSON.stringify({note:'synthetic-browser-probe:' + result.nonce})});
    check(note.status === 200, 'synthetic in-context note evidence failed');
    result.completed_ms = Date.now(); result.passed = true;
    box.textContent = message + '\nKnown API 200; external HTTP blocked; SW blocked; headed Reader active';
    box.style.borderColor = '#136f32';
    save();
  })().catch(error => { result.error = String(error); result.completed_ms = Date.now();
    box.textContent = 'FIXTURE ACCEPTANCE FAILED: ' + result.error; save(); });
})();
"""


def prepare_derived(build, output, sentinel, derived):
    original_sha, manifest = tool.tree_manifest(build)
    tool.write_new(output / "original-build.json", {"sha256": original_sha, "files": manifest})
    shutil.copytree(build, derived)
    index = derived / "index.html"
    before = index.read_bytes()
    require(before.count(b"</head>") == 1, "unexpected built HTML head")
    for name in ("_fixture_acceptance_probe.js", "_fixture_acceptance_sw.js"):
        require(not (derived / name).exists(), "probe path already exists in admitted build")
    index.write_bytes(before.replace(b"</head>", b'<script defer src="/inbox/_fixture_acceptance_probe.js"></script></head>'))
    (derived / "_fixture_acceptance_probe.js").write_text(probe_script(sentinel))
    # If service-worker blocking were broken, this valid script would hit the
    # separate local sentinel. It never contacts a provider or public internet.
    (derived / "_fixture_acceptance_sw.js").write_text(
        "self.addEventListener('install', e => e.waitUntil(fetch(" + json.dumps(sentinel + "/service-worker-must-not-arrive") + ")));\n")
    derived_sha, derived_manifest = tool.tree_manifest(derived)
    tool.write_new(output / "derived-build.json", {"sha256": derived_sha, "files": derived_manifest,
        "original_sha256": original_sha, "probe_only_copy": True, "byte_identical_to_release": False})
    return derived, derived_sha, original_sha


class Sentinel:
    def __init__(self):
        self.arrivals = []
        self.connections = []
        self.control_path = "/parent-control-" + str(time.time_ns())
        outer = self
        class Server(ThreadingHTTPServer):
            def get_request(self):
                connection, address = super().get_request()
                connection.settimeout(3)
                outer.connections.append({"at_ms": time.time_ns() // 1000000})
                return connection, address
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.arrivals.append({"at_ms": time.time_ns() // 1000000, "path": self.path})
                self.send_response(200); self.end_headers(); self.wfile.write(b"synthetic sentinel")
            def log_message(self, *args):
                pass
        self.server = Server(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        # A real parent-side positive control proves that this TCP/HTTP observer
        # is reachable. All subsequent browser/SW connections must remain zero.
        with build_opener(ProxyHandler({})).open(self.base + self.control_path, timeout=3) as response:
            require(response.status == 200 and response.read() == b"synthetic sentinel", "network sentinel positive control failed")
        self.require_no_browser_connections()

    def require_no_browser_connections(self):
        require(len(self.connections) == 1 and len(self.arrivals) == 1 and self.arrivals[0]["path"] == self.control_path,
                "browser traffic reached independent TCP/HTTP observer")

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)


def cli(output, label, args, *, timeout=45, expected=0):
    # Deliberately inject only fake forbidden variables. Never pass host secrets.
    env = {"PATH": "/usr/bin:/bin", "HOME": str(output), "TMPDIR": str(output),
           "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
           "OPENAI_API_KEY": "synthetic-not-a-credential", "HTTP_PROXY": "http://invalid.example.test:9",
           "AI_NEWS_ROOT": "/tmp/synthetic-production-root-must-not-be-inherited"}
    command = [sys.executable, "-I", str(TOOL), *args]
    started = time.time_ns() // 1000000
    timed_out = False
    try:
        run = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        stdout, stderr, code = run.stdout, run.stderr, run.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        decode = lambda value: value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        stdout, stderr, code = decode(exc.stdout), decode(exc.stderr), None
    finished = time.time_ns() // 1000000
    (output / (label + ".stdout.log")).write_text(stdout)
    (output / (label + ".stderr.log")).write_text(stderr)
    tool.write_new(output / (label + ".command.json"), {"argv": command, "timeout_seconds": timeout,
        "started_ms": started, "finished_ms": finished, "exit_code": code, "timed_out": timed_out,
        "injected_environment_names_only": ["OPENAI_API_KEY", "HTTP_PROXY", "AI_NEWS_ROOT"]})
    require(not timed_out and code == expected, f"{label}: expected exit {expected}, got {code}; retained logs")
    text = stdout if expected == 0 else stderr
    data = json.loads(text)
    return data, (started, finished)


def worker_boundary(root, output, label):
    owned, marker = tool.owned_root(root)
    state = tool.latest(owned)
    require(state["workspace"] == marker["workspace"] and state["headed"] is True, "wrong owned headed worker")
    pid = state["pid"]
    process = Path("/proc") / str(pid)
    require(process.stat().st_uid == os.getuid(), "worker PID not owned by this test user")
    keys = {v.split(b"=", 1)[0].decode() for v in (process / "environ").read_bytes().split(b"\0") if v}
    expected = set(tool.clean_env(owned, headed=True, display=":0", browser_cache=owned))
    require(keys == expected, "real worker inherited unexpected environment names")
    inodes = set()
    for fd in (process / "fd").iterdir():
        try:
            match = re.fullmatch(r"socket:\[(\d+)\]", os.readlink(fd))
        except FileNotFoundError:
            continue
        if match:
            inodes.add(match.group(1))
    listeners = []
    for row in (process / "net/tcp").read_text().splitlines()[1:]:
        fields = row.split()
        if fields[9] in inodes and fields[3] == "0A":
            host, port = fields[1].split(":")
            require(host == "0100007F", "fixture listener is not IPv4 loopback")
            listeners.append(int(port, 16))
    require(len(listeners) == 1, "expected exactly the real harness loopback static listener")
    base = f"http://127.0.0.1:{listeners[0]}"
    try:
        build_opener(ProxyHandler({})).open(base + "/mf/v1/me", timeout=3)
        raise AssertionError("bare localhost falsely exposed a fixture API")
    except HTTPError as exc:
        require(exc.code == 501, "bare localhost API must fail explicitly")
    result = {"workspace": marker["workspace"], "pid": pid, "environment_keys": sorted(keys),
              "listener": base, "raw_api_status": 501, "context_only": True}
    tool.write_new(output / (label + ".boundary.json"), result)
    return result


def png_proof(path):
    data = path.read_bytes()
    require(data.startswith(b"\x89PNG\r\n\x1a\n"), "missing actual browser screenshot")
    offset = 8; compressed = bytearray()
    while offset < len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        if data[offset + 4:offset + 8] == b"IDAT":
            compressed.extend(data[offset + 8:offset + 8 + size])
        offset += size + 12
    require(len(set(zlib.decompress(compressed))) > 8, "actual browser screenshot is blank")
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def captured(root, generation, reason, birth_window, sentinel):
    folder = root / "generations" / generation
    paths = list(folder.glob("*-" + reason + ".json"))
    require(len(paths) == 1, "expected one retained capture for " + reason)
    evidence = tool.read_json(paths[0])
    require(not evidence.get("screenshot_error") and not evidence.get("storage_error"), "capture failed")
    require(not evidence["errors"], "real browser page errors were captured")
    probe = json.loads(evidence["session_storage"][KEY])
    require(probe["passed"] is True and probe["previous_marker"] is None, "real browser probe did not pass in a fresh context")
    require(birth_window[0] <= probe["time_origin_ms"] <= birth_window[1], "context was silently replaced outside its start/reset window")
    require(probe["failure_visible"]["visible"] and "501 unsupported_fixture_api" in probe["failure_visible"]["text"], "unknown API failure was not visible")
    require(probe["external_rejected"] and probe["service_workers_blocked"], "real browser isolation assertion failed")
    require(any(x["kind"] == "external" for x in evidence["blocked"]), "no real external request reached the context guard")
    require(any(x["kind"] == "unsupported" for x in evidence["blocked"]), "unknown API did not reach the strict guard")
    sentinel.require_no_browser_connections()
    require(any(method == "PUT" and path == f"/mf/v1/ai/notes/{NOTE_ID}" and
                body == {"note": "synthetic-browser-probe:" + probe["nonce"]}
                for method, path, body in evidence["fixture_writes"]), "missing real in-context fixture write")
    screenshot = paths[0].with_suffix(".png")
    return {"generation": generation, "probe": probe, "capture_sha256": digest(paths[0]),
            "screenshot": png_proof(screenshot), "path": str(paths[0])}


def retain_root(root, output):
    # Copy only synthetic receipts/logs/captures, not profiles, caches or builds.
    dest = output / "retained-workspace"
    dest.mkdir(exist_ok=True)
    for name in ("owner.json", "build-manifest.json"):
        if (root / name).is_file() and not (root / name).is_symlink():
            shutil.copy2(root / name, dest / name)
    for source in root.glob("worker-*.log"):
        if not source.is_symlink(): shutil.copy2(source, dest / source.name)
    for category in ("receipts", "responses", "generations", "retained-generations", "retained-blockers"):
        directory = root / category
        if not directory.is_dir() or directory.is_symlink(): continue
        for source in directory.rglob("*"):
            if source.is_symlink(): continue
            if source.is_file():
                target = dest / source.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)


def main():
    output = output_directory()
    report = {"passed": False, "status": "FAILED", "output": str(output), "head": git("rev-parse", "HEAD"),
              "src": git("rev-parse", "HEAD:src"), "physical_device": False, "release_byte_identity": False}
    root = None; sentinel = None; displaced = None; original_build = None; original_sha = None; fixture_parent = None
    try:
        require(report["src"] == SRC, "unexpected approved src tree")
        require(digest(TOOL) == TOOL_SHA and digest(TOOL.with_name("review_reader_harness.py")) == HARNESS_SHA,
                "fixture tool or original harness differs from reviewed bytes")
        cache, display, executable = pinned_browser()
        report.update(playwright="1.63.0", chromium_revision="1243", chromium_version="153.0.8010.12",
                      headed=True, display=display, executable_sha256=digest(executable))
        value = os.environ.get("AI_NEWS_TEST_BUILD", "runtime/browser-build")
        original_build = tool.checked_path(Path(value) if Path(value).is_absolute() else ROOT / value)
        identity = json.loads((ROOT / "artifacts/ci-reader-identity.json").read_text())
        require(identity["head"] == report["head"] and identity["src_tree"] == SRC, "build/CI source identity mismatch")
        sentinel = Sentinel()
        # Keep Chromium's nested SingletonSocket below Linux AF_UNIX limits.
        fixture_parent = Path(tempfile.mkdtemp(prefix="rf-", dir="/tmp"))
        # Keep the derived static bytes outside the collected evidence folder.
        # The original and derived manifests remain available for exact review.
        derived, derived_sha, original_sha = prepare_derived(original_build, output, sentinel.base,
                                                            fixture_parent / "derived-build")
        report.update(original_build_sha256=original_sha, derived_build_sha256=derived_sha)
        # Use the real tool's owned-root admission before start, so only this
        # xvfb-run's temporary display cookie can enter its private HOME. The
        # tool's environment allowlist and API are not changed or bypassed.
        root = tool.create_root(derived, derived_sha, fixture_parent)
        report["display_auth"] = temporary_xvfb_authority(root, display)
        # The real public CLI start launches its own separate daemon, unchanged
        # harness and pinned headed Chromium. No test double or launch hook.
        first, first_birth = cli(output, "01-start", ["start", "--root", str(root),
            "--headed", "--display", display, "--browser-cache", str(cache)])
        root = Path(first["root"])
        require(first["live"] and first["headed"], "real start did not acknowledge headed context")
        status, _ = cli(output, "02-status", ["status", "--root", str(root)], timeout=25)
        require(status["generation"] == first["generation"] and status["instance"] == first["instance"], "status changed worker identity")
        report["first_boundary"] = worker_boundary(root, output, "first")
        time.sleep(PROBE_SETTLE_SECONDS)
        # A real filesystem fault makes the actual capture path unusable. Move
        # its directory aside and retain a regular blocking file; delete nothing.
        active = root / "generations" / first["generation"]
        saved_parent = root / "retained-generations"; saved_parent.mkdir()
        saved = saved_parent / first["generation"]
        active.rename(saved)
        displaced = (active, saved)
        tool.write_new(active, {"synthetic_capture_destination_fault": True})
        failed, _ = cli(output, "03-reset-capture-failure", ["reset", "--root", str(root)], timeout=25, expected=1)
        require("old context retained" in failed["error"], "real reset did not preserve on capture failure")
        kept, _ = cli(output, "04-status-retained", ["status", "--root", str(root)], timeout=25)
        require(kept["live"] and kept["generation"] == first["generation"] and kept["instance"] == first["instance"], "failed capture replaced the live context")
        require(kept["last_command_failure"]["retained_context"], "missing failed-command retention receipt")
        blockers = root / "retained-blockers"; blockers.mkdir()
        active.rename(blockers / (first["generation"] + ".json"))
        saved.rename(active); displaced = None
        second, second_birth = cli(output, "05-reset-retry", ["reset", "--root", str(root)], timeout=40)
        require(second["live"] and second["generation"] != first["generation"], "reset did not create a fresh context")
        report["first_capture"] = captured(root, first["generation"], "reset", first_birth, sentinel)
        worker_boundary(root, output, "second")
        time.sleep(PROBE_SETTLE_SECONDS)
        stopped, _ = cli(output, "06-stop", ["stop", "--root", str(root)], timeout=25)
        require(stopped["state"] == "stopped" and not stopped["live"], "real stop failed")
        report["second_capture"] = captured(root, second["generation"], "stop", second_birth, sentinel)
        stopped_status, _ = cli(output, "07-status-stopped", ["status", "--root", str(root)], timeout=25)
        require(not stopped_status["live"], "stopped receipt falsely claims liveness")
        before_restart = {p: digest(p) for p in (root / "generations").rglob("*") if p.is_file()}
        third, third_birth = cli(output, "08-restart", ["start", "--root", str(root), "--headed", "--display", display,
            "--browser-cache", str(cache)])
        require(third["instance"] != first["instance"] and third["generation"] != second["generation"], "restart reused stale identity")
        worker_boundary(root, output, "third")
        time.sleep(PROBE_SETTLE_SECONDS)
        cli(output, "09-stop-restarted", ["stop", "--root", str(root)], timeout=25)
        report["third_capture"] = captured(root, third["generation"], "stop", third_birth, sentinel)
        require(len({report[name]["probe"]["nonce"] for name in ("first_capture", "second_capture", "third_capture")}) == 3,
                "reset/restart did not create fresh document contexts")
        require(all(path.is_file() and digest(path) == value for path, value in before_restart.items()), "restart modified or deleted prior evidence")
        sentinel.require_no_browser_connections()
        report.update(passed=True, status="PASSED", root=str(root), outbound_network_arrivals=0,
                      same_context_retained_on_capture_failure=True, prior_evidence_preserved=True)
    except NotRun as exc:
        report.update(status="NOT_RUN", error=str(exc), error_type=type(exc).__name__)
    except Exception as exc:
        report.update(status="FAILED", error=str(exc), error_type=type(exc).__name__)
    finally:
        # Restore the intentional filesystem fault before asking the real tool
        # to save a failure screenshot and stop. All moved files are retained.
        if displaced:
            active, saved = displaced
            if active.exists(): active.rename(active.with_name(active.name + "-retained-blocker"))
            if saved.exists(): saved.rename(active)
        if root is None and fixture_parent:
            # start can fail after creating its root. This parent was freshly
            # created for this one invocation; retain the real worker error too.
            candidates = list(fixture_parent.glob("reader-fixture-*"))
            if len(candidates) == 1:
                try:
                    root = tool.owned_root(candidates[0])[0]
                except Exception as exc:
                    report["startup_root_error"] = type(exc).__name__
        if root:
            try:
                last = tool.latest(root)
                if last and last["state"] in {"running", "resetting"}:
                    cli(output, "failure-stop", ["stop", "--root", str(root)], timeout=30)
            except Exception as exc:
                report["cleanup_error"] = type(exc).__name__ + ": " + str(exc)
                report.update(passed=False, status="FAILED")
            try:
                retain_root(root, output)
            except Exception as exc:
                report["retention_error"] = type(exc).__name__ + ": " + str(exc)
                report.update(passed=False, status="FAILED")
        if sentinel:
            report["network_sentinel_arrivals"] = sentinel.arrivals
            report["network_sentinel_tcp_connections"] = sentinel.connections
            report["network_sentinel_parent_positive_controls"] = 1
            sentinel.close()
        if original_build and original_sha:
            try:
                report["original_build_unchanged"] = tool.tree_manifest(original_build)[0] == original_sha
                if not report["original_build_unchanged"]: report.update(passed=False, status="FAILED")
            except Exception as exc:
                report.update(passed=False, status="FAILED", original_build_error=type(exc).__name__)
        tool.write_new(output / "result.json", report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
