"""Owned, restartable synthetic Reader workspace; never a production server.

Use the CLI help and docs/dev-fixture-workspace.md. Control uses private local
files, not HTTP. Only the Playwright context has fixture APIs/authentication.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import unquote, urlsplit

SCHEMA = "reader-dev-fixture-v1"
PLAYWRIGHT_VERSION = "1.63.0"
FORBIDDEN = {"production", "prod", "config", ".config", ".private", ".ssh", ".aws", ".git", "secrets"}
COMMANDS = {"status", "reset", "stop"}
LIMIT = 64 * 1024


class Rejected(RuntimeError):
    """An explicit local-fixture admission failure."""


class CaptureFailed(Rejected):
    """A command failed before closing anything; the old context is retained."""


def checked_path(value, *, directory=True, temp=False):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or FORBIDDEN.intersection(path.parts):
        raise Rejected("absolute non-production/non-config path required")
    if str(path).startswith("/home/ubuntu/ai-news"):
        raise Rejected("production root rejected")
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise Rejected("symlink path rejected")
    if temp and not any(path.is_relative_to(p) for p in (Path("/tmp"), Path("/var/tmp"))):
        raise Rejected("workspace parent must be under /tmp or /var/tmp")
    if directory and not path.is_dir():
        raise Rejected("directory required")
    if not directory and not path.is_file():
        raise Rejected("regular file required")
    return path


def tree_manifest(path):
    path = checked_path(path)
    records = []
    for item in sorted(path.rglob("*")):
        rel = item.relative_to(path)
        if item.is_symlink() or FORBIDDEN.intersection(rel.parts):
            raise Rejected("unsafe build tree")
        mode = item.stat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode) or item.name == ".env" or item.name.startswith(".env."):
            raise Rejected("only regular static build files allowed")
        records.append({"path": rel.as_posix(), "mode": stat.S_IMODE(mode),
                        "bytes": item.stat().st_size,
                        "sha256": hashlib.sha256(item.read_bytes()).hexdigest()})
    if not any(x["path"] == "index.html" for x in records):
        raise Rejected("build index.html required")
    digest = hashlib.sha256(json.dumps(records, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return digest, records


def write_new(path, data):
    """Never replace or delete evidence. Parent must already be owned."""
    raw = json.dumps(data, ensure_ascii=True, sort_keys=True, indent=2).encode() + b"\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    path = Path(path)
    # Publish complete JSON atomically. Preserve the staging link as evidence;
    # link() also refuses an existing final path instead of overwriting it.
    staged = path.with_name(f".{path.name}.pending-{uuid.uuid4().hex}")
    with os.fdopen(os.open(staged, flags, 0o600), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(staged, path, follow_symlinks=False)


def read_json(path):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise Rejected("regular JSON file required")
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise Rejected("oversize control JSON")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Rejected("duplicate control JSON key")
            result[key] = value
        return result
    def constant(value):
        raise Rejected("non-finite control JSON")
    data = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    if not isinstance(data, dict):
        raise Rejected("control object required")
    return data


def owned_root(value):
    root = checked_path(value, temp=True)
    if not root.name.startswith("reader-fixture-") or root.stat().st_uid != os.getuid():
        raise Rejected("not an owned fixture root")
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise Rejected("fixture root must be private mode 0700")
    # Browser-owned scratch can contain Chromium's internal symlinks. We never
    # traverse/read that scratch. Managed control/evidence/build trees may not.
    members = list(root.iterdir())
    for name in ("commands", "responses", "receipts", "generations", "build"):
        if (root / name).is_dir() and not (root / name).is_symlink():
            members.extend((root / name).rglob("*"))
    for p in members:
        mode = p.lstat().st_mode
        if (not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))
                or p.stat().st_uid != os.getuid()):
            raise Rejected("unsafe owned-root member")
    try:
        marker = read_json(root / "owner.json")
    except (OSError, ValueError) as exc:
        raise Rejected("missing or invalid owned-root receipt") from exc
    if (set(marker) != {"schema", "root", "uid", "workspace"}
            or marker["schema"] != SCHEMA or marker["root"] != str(root)
            or marker["uid"] != os.getuid()
            or not isinstance(marker["workspace"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", marker["workspace"])):
        raise Rejected("root receipt identity mismatch")
    return root, marker


def create_root(build, digest, parent):
    parent = checked_path(parent, temp=True)
    if parent not in (Path("/tmp"), Path("/var/tmp")) and parent.stat().st_uid != os.getuid():
        raise Rejected("unowned workspace parent")
    build = checked_path(build)
    actual, manifest = tree_manifest(build)
    if digest != actual:
        raise Rejected("build SHA256 mismatch")
    root = Path(tempfile.mkdtemp(prefix="reader-fixture-", dir=parent))
    marker = {"schema": SCHEMA, "root": str(root), "uid": os.getuid(), "workspace": uuid.uuid4().hex}
    write_new(root / "owner.json", marker)
    for name in ("commands", "responses", "receipts", "generations", "home", "tmp", "cache"):
        (root / name).mkdir(mode=0o700)
    write_new(root / "build-manifest.json", {"sha256": digest, "files": manifest})
    shutil.copytree(build, root / "build")
    if tree_manifest(root / "build")[0] != digest:
        raise Rejected("copied build SHA256 mismatch; evidence retained")
    return root


def clean_env(root, *, headed=False, display=None, browser_cache=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
           "XDG_CACHE_HOME": str(root / "cache"), "XDG_CONFIG_HOME": str(root / "home"),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "LANG": "C.UTF-8",
           "AI_NEWS_TEST_BUILD": str(root / "build")}
    if headed:
        if not display or not re.fullmatch(r":\d+(?:\.\d+)?", display):
            raise Rejected("headed mode requires an explicit local DISPLAY, for example :0")
        env["DISPLAY"] = display
    if browser_cache:
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(checked_path(browser_cache))
    return env


def receipt(root, marker, instance, state, **extra):
    data = {"schema": SCHEMA, "workspace": marker["workspace"], "instance": instance,
            "state": state, "at_ns": time.time_ns(), **extra}
    write_new(root / "receipts" / f"{data['at_ns']:020d}-{uuid.uuid4().hex}.json", data)
    return data


def latest(root):
    paths = sorted((root / "receipts").glob("*.json"))
    return read_json(paths[-1]) if paths else None


def command(root_value, action, *, timeout=20):
    if action not in COMMANDS:
        raise Rejected("unknown workspace command")
    root, marker = owned_root(root_value)
    last = latest(root)
    if not last or last["workspace"] != marker["workspace"]:
        raise Rejected("missing workspace receipt")
    if last["state"] not in {"running", "resetting"}:
        if action == "status":
            return {**last, "live": False}
        raise Rejected("workspace is not running")
    instance = last["instance"]
    if not isinstance(instance, str) or not re.fullmatch(r"[0-9a-f]{32}", instance):
        raise Rejected("invalid instance receipt")
    ident = uuid.uuid4().hex
    request = {"workspace": marker["workspace"], "instance": instance, "id": ident, "command": action}
    folder = root / "commands" / instance
    if not folder.is_dir():
        raise Rejected("missing current worker mailbox")
    write_new(folder / f"{ident}.json", request)
    response = root / "responses" / f"{ident}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if response.exists():
            data = read_json(response)
            if any(data.get(k) != v for k, v in request.items() if k != "command"):
                raise Rejected("cross-instance response rejected")
            if not data.get("ok"):
                raise Rejected(data.get("error", "worker rejected command"))
            return data
        current = latest(root)
        if current and current["instance"] == instance and current["state"] == "failed":
            raise Rejected("worker failed; inspect retained failure receipt")
        time.sleep(0.05)
    raise Rejected("worker did not acknowledge; stale receipts do not prove liveness")


def route_kind(url, method, base):
    target, origin = urlsplit(url), urlsplit(base)
    if (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
        return "external"
    path = unquote(target.path)
    if path != target.path or ".." in path.split("/"):
        return "unsupported"
    rules = {
        "/mf/v1/me": {"GET"}, "/mf/version": {"GET"}, "/mf/v1/version": {"GET"},
        "/mf/v1/categories": {"GET", "POST"}, "/mf/v1/feeds": {"GET"},
        "/mf/v1/feeds/counters": {"GET"}, "/mf/v1/entries": {"GET"},
        "/mf/v1/ai/settings": {"GET", "PUT"}, "/mf/v1/ai/status": {"GET"},
        "/mf/v1/ai/catalog": {"GET"}, "/mf/v1/ai/x/roster": {"GET"},
        "/mf/v1/ai/subscribe": {"POST"},
    }
    if path in rules:
        return "fixture" if method in rules[path] else "method"
    if re.fullmatch(r"/mf/v1/ai/notes/[1-9][0-9]*", path):
        return "fixture" if method in {"GET", "PUT"} else "method"
    if re.fullmatch(r"/mf/v1/entries/[1-9][0-9]*", path):
        return "fixture" if method == "GET" else "method"
    if path.startswith(("/mf", "/v1", "/api")):
        return "unsupported"
    return "static" if method in {"GET", "HEAD"} else "method"


class ControlledFixture:
    """Adapt the existing harness without copying its fixture data or changing it."""
    def __init__(self, root, generation, *, headed=False):
        if importlib.metadata.version("playwright") != PLAYWRIGHT_VERSION:
            raise Rejected("exact locked Playwright 1.63.0 required")
        import review_reader_harness as harness
        from http.server import SimpleHTTPRequestHandler
        import functools

        self.out = root / "generations" / generation
        self.out.mkdir(mode=0o700)
        self.h = harness.Harness.__new__(harness.Harness)
        self.blocked = []
        # All harness output belongs to this generation, never the checkout.
        harness.ROOT = self.out
        original = harness.sync_playwright

        class LaunchProxy:
            def __init__(self, pw):
                self.pw = pw
            @property
            def chromium(self):
                outer = self
                class Chromium:
                    def launch(self, **kwargs):
                        kwargs["headless"] = not headed
                        return outer.pw.chromium.launch(**kwargs)
                return Chromium()
            def stop(self):
                return self.pw.stop()

        class Starter:
            def start(self):
                return LaunchProxy(original().start())

        harness.sync_playwright = Starter
        try:
            harness.Harness.__init__(self.h, "controlled", service_workers="block", accept_downloads=False)
            h = self.h
            if h.server.server_address[0] != "127.0.0.1":
                raise Rejected("harness server must bind loopback only")
            build = h.build
            class StaticOnly(SimpleHTTPRequestHandler):
                def do_GET(self):
                    path = unquote(urlsplit(self.path).path)
                    if path.startswith(("/mf", "/api", "/v1")):
                        self.send_error(501, "Fixture APIs exist only in the controlled context")
                        return
                    if ".." in path.split("/"):
                        self.send_error(400)
                        return
                    relative = path.removeprefix("/inbox/").lstrip("/")
                    self.path = "/" + relative if (build / relative).is_file() else "/index.html"
                    super().do_GET()
                def log_message(self, *args):
                    pass
            h.server.RequestHandlerClass = functools.partial(StaticOnly, directory=str(build))
            h.ctx.unroute_all(behavior="wait")
            h.ctx.route("**/*", self.route)
            h.ctx.route_web_socket("**/*", lambda ws: ws.close())
            h.page.goto(h.base + "/inbox/today", wait_until="domcontentloaded", timeout=15000)
            h.checks["controlled_context_started"] = True
        except BaseException:
            self.close_partial()
            raise
        finally:
            harness.sync_playwright = original

    def route(self, route):
        h, req = self.h, route.request
        kind = route_kind(req.url, req.method, h.base)
        if kind == "external":
            self.blocked.append({"kind": "external"})
            route.abort("blockedbyclient")
        elif kind == "static":
            route.continue_()
        elif kind != "fixture":
            self.blocked.append({"kind": kind})
            route.fulfill(status=405 if kind == "method" else 501, json={"error": "unsupported_fixture_api"})
        else:
            path = urlsplit(req.url).path
            if re.fullmatch(r"/mf/v1/entries/[1-9][0-9]*", path) and not any(str(e["id"]) == path.rsplit("/", 1)[-1] for e in h.entries):
                route.fulfill(status=404, json={"error": "fixture_entry_missing"})
                return
            try:
                h.route(route)
            except (ValueError, KeyError, TypeError, StopIteration):
                self.blocked.append({"kind": "invalid_fixture_payload"})
                route.fulfill(status=400, json={"error": "invalid_fixture_payload"})

    def pump(self):
        self.h.page.wait_for_timeout(50)

    def capture(self, reason):
        # Evidence first: a failed screenshot is itself recorded, not discarded.
        stamp = f"{time.time_ns()}-{reason}"
        result = {"reason": reason, "errors": self.h.errors, "blocked": self.blocked,
                  "calls": self.h.calls, "fixture_writes": self.h.writes,
                  "context_only": True, "production_acceptance": False}
        try:
            self.h.page.screenshot(path=str(self.out / f"{stamp}.png"), timeout=5000)
        except Exception as exc:
            result["screenshot_error"] = type(exc).__name__
        try:
            result["storage"] = self.h.ctx.storage_state()
            result["session_storage"] = self.h.page.evaluate("Object.fromEntries(Object.entries(sessionStorage))")
        except Exception as exc:
            result["storage_error"] = type(exc).__name__
        write_new(self.out / f"{stamp}.json", result)

    def close_partial(self):
        for obj, method in (("browser", "close"), ("pw", "stop"), ("server", "shutdown"), ("server", "server_close")):
            if hasattr(self.h, obj):
                with contextlib.suppress(Exception):
                    getattr(getattr(self.h, obj), method)()

    def close(self):
        self.close_partial()


class Session:
    def __init__(self, root, marker, instance, factory=ControlledFixture, *, headed=False):
        self.root, self.marker, self.instance, self.factory = root, marker, instance, factory
        self.headed = headed
        self.fixture = None
        self.generation = None
        self.tool_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self.last_command_failure = None

    def start(self):
        expected = read_json(self.root / "build-manifest.json")["sha256"]
        if tree_manifest(self.root / "build")[0] != expected:
            raise Rejected("pinned build changed")
        self.generation = f"{time.time_ns()}-{uuid.uuid4().hex}"
        self.fixture = self.factory(self.root, self.generation, headed=self.headed)
        self.last_command_failure = None
        return receipt(self.root, self.marker, self.instance, "running", generation=self.generation,
                       pid=os.getpid(), headed=self.headed, context_only=True,
                       build_sha256=expected, tool_sha256=self.tool_sha256)

    def handle(self, data):
        if (set(data) != {"workspace", "instance", "id", "command"}
                or data["workspace"] != self.marker["workspace"] or data["instance"] != self.instance
                or not isinstance(data["id"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", data["id"])
                or not isinstance(data["command"], str)
                or data["command"] not in COMMANDS):
            raise Rejected("unknown or cross-instance command")
        action = data["command"]
        if action == "status":
            self.fixture.pump()
            return {"state": "running", "generation": self.generation, "live": True,
                    "build_sha256": read_json(self.root / "build-manifest.json")["sha256"],
                    "tool_sha256": self.tool_sha256, "headed": self.headed, "context_only": True,
                    "last_command_failure": self.last_command_failure}
        try:
            self.fixture.capture(action)  # Must complete before anything is closed/reset.
        except Exception as exc:
            self.last_command_failure = {"command": action, "outcome": "failed",
                                         "error_type": type(exc).__name__, "retained_context": True}
            raise CaptureFailed("evidence capture failed; old context retained") from exc
        self.fixture.close()
        self.fixture = None
        if action == "stop":
            receipt(self.root, self.marker, self.instance, "stopped", generation=self.generation)
            return {"state": "stopped", "live": False}
        receipt(self.root, self.marker, self.instance, "resetting", generation=self.generation)
        try:
            self.start()
        except Exception as exc:
            receipt(self.root, self.marker, self.instance, "failed", error_type=type(exc).__name__)
            raise
        return {"state": "running", "generation": self.generation, "live": True}


def serve(root_value, instance, headed=False, *, fixture_factory=ControlledFixture):
    root, marker = owned_root(root_value)
    if not re.fullmatch(r"[0-9a-f]{32}", instance):
        raise Rejected("invalid worker instance")
    launch = read_json(root / f"launch-{instance}.json")
    if launch.get("workspace") != marker["workspace"] or launch.get("headed") != headed:
        raise Rejected("worker launch identity mismatch")
    env = clean_env(root, headed=headed, display=launch.get("display"),
                    browser_cache=launch.get("browser_cache"))
    os.environ.clear()
    os.environ.update(env)
    def terminate(signum, frame):
        raise InterruptedError("fixture worker interrupted; evidence retained")
    signal.signal(signal.SIGTERM, terminate)
    fd = os.open(root / "worker.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Rejected("workspace already has an active worker") from None
        session = Session(root, marker, instance, factory=fixture_factory, headed=headed)
        folder = root / "commands" / instance
        folder.mkdir(mode=0o700)
        done = set()
        try:
            session.start()
            while session.fixture is not None:
                for path in sorted(folder.glob("*.json")):
                    if path.name in done:
                        continue
                    done.add(path.name)
                    # Validate the root every command; never follow a replaced mailbox.
                    owned_root(root)
                    data = read_json(path)
                    ident = path.stem
                    if not re.fullmatch(r"[0-9a-f]{32}", ident):
                        raise Rejected("invalid command filename")
                    response = {"workspace": marker["workspace"], "instance": instance, "id": ident}
                    try:
                        if data.get("id") != ident:
                            raise Rejected("command filename identity mismatch")
                        response.update(session.handle(data), ok=True)
                    except CaptureFailed as exc:
                        response.update(ok=False, error=str(exc), retained_context=True)
                        # Failure of this evidence destination must not turn a
                        # rejected reset/stop into destruction of the old context.
                        with contextlib.suppress(OSError):
                            receipt(root, marker, instance, "running", generation=session.generation,
                                    **session.last_command_failure)
                    except Rejected as exc:
                        if session.fixture is None:
                            raise  # A failed new generation is not a live rejected command.
                        response.update(ok=False, error=str(exc))
                    try:
                        write_new(root / "responses" / f"{ident}.json", response)
                    except OSError:
                        if not response.get("retained_context"):
                            raise
                        # If the whole volume is unavailable, the caller times
                        # out rather than receiving a false success. Keep live.
                    if session.fixture is None:
                        break
                if session.fixture is not None:
                    session.fixture.pump()
        except Exception as exc:
            receipt(root, marker, instance, "failed", error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            if session.fixture is not None:
                with contextlib.suppress(Exception):
                    session.fixture.capture("worker-failure")
                session.fixture.close()


def start(root, *, headed=False, display=None, browser_cache=None, timeout=30):
    root, marker = owned_root(root)
    env = clean_env(root, headed=headed, display=display, browser_cache=browser_cache)
    instance = uuid.uuid4().hex
    write_new(root / f"launch-{instance}.json", {"workspace": marker["workspace"], "headed": headed,
                                               "display": display, "browser_cache": browser_cache})
    log = root / f"worker-{instance}.log"
    argv = [sys.executable, "-I", str(Path(__file__).resolve()), "_serve", "--root", str(root), "--instance", instance]
    if headed:
        argv.append("--headed")
    with log.open("xb") as stream:
        process = subprocess.Popen(argv, env=env, cwd=root, stdin=subprocess.DEVNULL, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        last = latest(root)
        if last and last["instance"] == instance and last["state"] == "running":
            return {"root": str(root), **command(root, "status", timeout=5)}
        if process.poll() is not None:
            raise Rejected(f"worker startup failed; retained log: {log}")
        time.sleep(0.05)
    # A worker that did not reach readiness cannot be left as an orphan service.
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    receipt(root, marker, instance, "failed", error="startup timeout", log=str(log))
    raise Rejected(f"worker readiness timed out; retained log: {log}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("start")
    p.add_argument("--root")
    p.add_argument("--build")
    p.add_argument("--build-sha256")
    p.add_argument("--parent", default="/tmp")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--display")
    p.add_argument("--browser-cache")
    for name in sorted(COMMANDS):
        sub.add_parser(name).add_argument("--root", required=True)
    p = sub.add_parser("fingerprint")
    p.add_argument("--build", required=True)
    p = sub.add_parser("_serve")
    p.add_argument("--root", required=True)
    p.add_argument("--instance", required=True)
    p.add_argument("--headed", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "fingerprint":
            result = {"build_sha256": tree_manifest(args.build)[0]}
        elif args.action == "start":
            if args.root:
                if args.build or args.build_sha256:
                    raise Rejected("restart uses the existing pinned build; omit build arguments")
                root = args.root
            else:
                if not args.build or not args.build_sha256:
                    raise Rejected("new workspace requires --build and --build-sha256")
                root = create_root(args.build, args.build_sha256, args.parent)
            result = start(root, headed=args.headed, display=args.display,
                           browser_cache=args.browser_cache)
        elif args.action == "_serve":
            # Isolated mode omits the script directory from sys.path.
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            serve(args.root, args.instance, args.headed)
            return 0
        else:
            result = command(args.root, args.action)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Rejected, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "type": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
