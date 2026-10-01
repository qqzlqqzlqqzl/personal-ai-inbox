"""Experimental exact-transaction APT archive cache for Reader's hosted CI.

The installed Playwright resolver and APT remain the dependency solvers. The
manifest is regenerated from fresh signed indices, never restored from cache.
Only Ubuntu 24.04/amd64 + Playwright 1.63.0 is accepted by this experiment.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time

PLAYWRIGHT = "1.63.0"
SCHEMA = "v1"
ARCH = "amd64"
TOKEN = r"[a-z0-9][a-z0-9+.-]+(?::amd64)?"
VERSION = r"[0-9][A-Za-z0-9.+:~\-]*"
AUTH = ("-o", "APT::Get::AllowUnauthenticated=false",
        "-o", "Acquire::AllowInsecureRepositories=false",
        "-o", "Acquire::AllowDowngradeToInsecureRepositories=false")
APT = ("apt-get", *AUTH)
ENV = {**os.environ, "LC_ALL": "C", "LANG": "C", "DEBIAN_FRONTEND": "noninteractive"}


def command(*args, **kwargs):
    start = time.monotonic()
    result = subprocess.run(args, check=True, text=True, env=ENV, timeout=600, **kwargs)
    print(json.dumps({"command": list(args), "seconds": round(time.monotonic() - start, 3)}), flush=True)
    return result


def bounded(args, timeout=60, limit=65536, env=None, new_session=True):
    """Bound both output streams while the official CLI (and its children) run."""
    start = time.monotonic()
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=ENV if env is None else env, start_new_session=new_session)
    streams = {"stdout": bytearray(), "stderr": bytearray()}
    try:
        with selectors.DefaultSelector() as selector:
            for name in streams:
                selector.register(getattr(process, name), selectors.EVENT_READ, name)
            while selector.get_map():
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    raise ValueError("Official resolver timed out")
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        streams[key.data].extend(chunk)
                        if len(streams[key.data]) > limit:
                            raise ValueError("Official resolver output limit exceeded")
            remaining = timeout - (time.monotonic() - start)
            if remaining <= 0:
                raise ValueError("Official resolver timed out")
            code = process.wait(timeout=remaining)
    except BaseException:
        try:
            if new_session:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.wait()
        raise
    finally:
        process.stdout.close()
        process.stderr.close()
    return code, streams["stdout"].decode("utf-8"), streams["stderr"].decode("utf-8")


def parse_official(code, stdout, stderr):
    stdout, stderr = stdout.replace("\r\n", "\n"), stderr.replace("\r\n", "\n")
    if stderr or len(stdout.encode()) > 65536:
        raise ValueError("Official resolver emitted stderr or excess output")
    if code == 0 and stdout == "All system dependencies are installed.\n":
        return []
    lines = stdout.split("\n")
    header = re.fullmatch(r"Missing system dependencies \(([1-9][0-9]{0,2})\):", lines[0])
    if code != 1 or not header or lines[-1] != "":
        raise ValueError("Unrecognized official resolver result")
    count = int(header[1])
    names = [line[2:] for line in lines[1:-1]]
    if (count > 256 or len(names) != count or names != sorted(set(names))
            or any(not re.fullmatch("  " + TOKEN, line) for line in lines[1:-1])):
        raise ValueError("Malformed official missing-package list")
    return names


def capture_apt(path):
    """Transparent, one-invocation adapter for the pinned official dry-run.

    This runs only as the normal user. It never changes the official APT argv,
    and the absolute executable prevents the temporary PATH entry recursing.
    """
    args = sys.argv[1:]
    validate_capture_args(args)
    # Exclusive creation rejects a second invocation, even with identical argv.
    with open(path, "x", encoding="utf-8") as output:
        code, stdout, stderr = bounded(["/usr/bin/apt-get", *args], timeout=55, new_session=False)
        json.dump(dict(arguments=args, code=code, stdout=stdout, stderr=stderr), output)
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    raise SystemExit(code)


def validate_capture_args(args):
    if (args[:3] != ["install", "-s", "--no-install-recommends"] or not 1 <= len(args[3:]) <= 256
            or any(not re.fullmatch(TOKEN, token) for token in args[3:])):
        raise ValueError("Unexpected official APT invocation")


def captured_plan(result, captured):
    missing = parse_official(*result)
    if set(captured) != {"arguments", "code", "stdout", "stderr"}:
        raise ValueError("Invalid original APT capture")
    validate_capture_args(captured["arguments"])
    if (captured["code"] != 0 or captured["stderr"] or len(captured["stdout"].encode()) > 65536):
        raise ValueError("Original APT simulation failed or exceeded output limits")
    selected = parse_plan(captured["stdout"])
    if sorted(r["package"] for r in selected) != sorted(token_name(name) for name in missing):
        raise ValueError("Official report and original APT transaction disagree")
    return missing, selected


def official_plan():
    start = time.monotonic()
    # Capture the exact simulation Playwright actually invokes. A second
    # simulation of names alone cannot prove the original selected versions.
    with tempfile.TemporaryDirectory(prefix="reader-playwright-plan-") as temporary:
        folder = Path(temporary)
        capture = folder / "transaction.json"
        wrapper = folder / "apt-get"
        wrapper.write_text(f"#!{sys.executable}\nimport runpy\n"
                           f"runpy.run_path({str(Path(__file__).absolute())!r})['capture_apt']({str(capture)!r})\n",
                           encoding="utf-8")
        wrapper.chmod(0o700)
        result = bounded([sys.executable, "-m", "playwright", "install-deps", "chromium", "--dry-run"],
                         env={**ENV, "PATH": str(folder) + os.pathsep + ENV["PATH"]})
        missing, selected = captured_plan(result, json.loads(capture.read_text(encoding="utf-8")))
    print(json.dumps({"official_missing": missing, "original_apt_plan": selected,
                      "resolver_seconds": round(time.monotonic() - start, 3)}), flush=True)
    return missing, selected


def official():
    return official_plan()[0]


def supported_environment():
    release = platform.freedesktop_os_release()
    arch = command("dpkg", "--print-architecture", capture_output=True).stdout.strip()
    if (release.get("ID"), release.get("VERSION_ID"), arch, importlib.metadata.version("playwright")) != (
            "ubuntu", "24.04", ARCH, PLAYWRIGHT) or os.getuid() == 0:
        raise ValueError("Experiment requires an unprivileged Ubuntu 24.04/amd64 Playwright 1.63.0 runner")
    return {"distro": "ubuntu-24.04", "architecture": arch, "playwright": PLAYWRIGHT,
            "schema": SCHEMA}


def token_name(token):
    if not re.fullmatch(TOKEN, token):
        raise ValueError("Unsafe package token")
    return token.split(":")[0]


def parse_plan(text):
    """Read APT's selected Inst versions, never apt-cache candidate guesses.

    The numeric transaction summary and all Inst/Conf operations must agree.
    Human-readable dependency explanations are not interpreted as operations.
    """
    if any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise ValueError("Unexpected APT control characters")
    summaries = re.findall(r"^(\d+) upgraded, (\d+) newly installed, (\d+) to remove and (\d+) not upgraded\.$", text, re.M)
    if len(summaries) != 1 or int(summaries[0][2]):
        raise ValueError("APT transaction has removals or an unknown summary")
    installs, configured, upgrades = {}, {}, 0
    operation = re.compile(rf"(Inst|Conf) ({TOKEN}) (?:\[({VERSION})\] )?\(({VERSION}) .+ \[(amd64|all)\]\)")
    for line in text.splitlines():
        if line.startswith(("Remv ", "Purg ")):
            raise ValueError("APT removal rejected")
        if not re.match(r"^(Inst|Conf)\b", line):
            continue
        match = operation.fullmatch(line)
        if not match:
            raise ValueError(f"Unrecognized APT operation: {line!r}")
        action, token, previous, version, arch = match.groups()
        name = token_name(token)
        target = installs if action == "Inst" else configured
        if name in target or (action == "Conf" and previous):
            raise ValueError("Duplicate or invalid APT operation")
        target[name] = dict(package=name, version=version, architecture=arch)
        if previous:
            # APT's native version comparator handles epochs and Debian ordering.
            command("dpkg", "--compare-versions", version, "ge", previous)
            upgrades += 1
    if (installs != configured or upgrades != int(summaries[0][0])
            or len(installs) - upgrades != int(summaries[0][1])):
        raise ValueError("APT transaction operations and summary disagree")
    return sorted(installs.values(), key=lambda record: record["package"])


def simulate(arguments):
    result = command(*APT, "install", "--simulate", "--no-install-recommends", "--no-remove", *arguments,
                     capture_output=True)
    if result.stderr:
        raise ValueError("APT simulation emitted stderr")
    return parse_plan(result.stdout)


def exact_args(records):
    return [f"{r['package']}:{r['architecture']}={r['version']}" for r in records]


def metadata(selected):
    # One apt-cache process reads the selected records together rather than
    # reparsing the entire index for each package on every integrity recheck.
    single = isinstance(selected, dict)
    selections = [selected] if single else selected
    wanted = {r["package"]: r for r in selections}
    if not wanted or len(wanted) != len(selections):
        raise ValueError("Missing or duplicate metadata selection")
    output = command("apt-cache", "show", *exact_args(selections), capture_output=True)
    if output.stderr:
        raise ValueError("APT metadata emitted stderr")
    records = {}
    for stanza in output.stdout.strip().split("\n\n"):
        fields = {}
        for line in stanza.splitlines():
            if not line or line[0].isspace():
                continue
            key, sep, value = line.partition(": ")
            if not sep or key in fields:
                raise ValueError("Malformed or duplicate APT metadata field")
            fields[key] = value
        selected = wanted.get(fields.get("Package"))
        if selected is None or any(fields.get(k.title()) != selected[k] for k in ("package", "version", "architecture")):
            raise ValueError("APT metadata selection mismatch")
        size, digest, filename = (fields.get(k, "") for k in ("Size", "SHA256", "Filename"))
        parts = filename.split("/")
        expected = f"{selected['package']}_{selected['version'].split(':', 1)[-1]}_{selected['architecture']}.deb"
        if (not re.fullmatch(r"[1-9][0-9]*", size) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or len(parts) < 2 or any(p in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9.+~%:\-]+", p) for p in parts[:-1])
                or parts[-1] != expected):
            raise ValueError("APT metadata lacks valid size/SHA256/repository filename")
        record = dict(selected, size=int(size), sha256=digest, filename=filename)
        name = selected["package"]
        if name in records and records[name] != record:
            raise ValueError("Conflicting APT metadata")
        records[name] = record
    if set(records) != set(wanted):
        raise ValueError("Incomplete APT metadata")
    result = sorted(records.values(), key=lambda record: record["package"])
    return result[0] if single else result


def resolve(identity):
    missing, selected = official_plan()
    if not missing:
        return dict(identity, packages=[])
    held = command("apt-mark", "showhold", capture_output=True)
    if held.stderr or set(held.stdout.split()) & {record["package"] for record in selected}:
        raise ValueError("APT transaction affects held packages")
    if simulate(exact_args(selected)) != selected:
        raise ValueError("Pinned APT transaction differs from official resolution")
    return dict(identity, packages=metadata(selected))


def cache_key(manifest):
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"reader-playwright-debs-{SCHEMA}-{manifest['distro']}-{manifest['architecture']}-{manifest['playwright']}-{digest}"


def archive_name(record):
    if (not re.fullmatch(TOKEN, record["package"]) or ":" in record["package"]
            or not re.fullmatch(VERSION, record["version"]) or record["architecture"] not in (ARCH, "all")):
        raise ValueError("Unsafe archive identity")
    return f"{record['package']}_{record['version'].replace(':', '%3a')}_{record['architecture']}.deb"


def directory(path):
    """Open every path component relative to its descriptor, never follow links."""
    path = path.absolute()
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def verified_copies(folder, records, staging):
    dirfd = directory(folder)
    targetfd = None
    try:
        targetfd = directory(staging)
        expected = {archive_name(r): r for r in records}
        if len(expected) != len(records) or set(os.listdir(dirfd)) != set(expected):
            raise ValueError("Unexpected archive cache contents")
        for name, record in expected.items():
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
            with os.fdopen(fd, "rb") as source:
                outputfd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                   0o600, dir_fd=targetfd)
                target = os.fdopen(outputfd, "wb")
                try:
                    _copy_archive(source, target, record)
                finally:
                    target.close()
    finally:
        if targetfd is not None:
            os.close(targetfd)
        os.close(dirfd)


def _copy_archive(source, target, record):
    info = os.fstat(source.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != record["size"]:
        raise ValueError("Archive must be a single regular file of the expected size")
    digest, count = hashlib.sha256(), 0
    while chunk := source.read(1024 * 1024):
        count += len(chunk)
        digest.update(chunk)
        target.write(chunk)
    if count != record["size"] or digest.hexdigest() != record["sha256"]:
        raise ValueError("Archive SHA256/size mismatch")


def install(cache, manifest, identity):
    start = time.monotonic()
    fd = directory(cache)
    try:
        empty = not os.listdir(fd)
    finally:
        os.close(fd)
    records = manifest["packages"]
    timings = {}
    if empty and records:
        with tempfile.TemporaryDirectory(prefix="reader-playwright-download-") as download:
            phase = time.monotonic()
            command(*APT, "download", *exact_args(records), cwd=download)
            timings["apt_download_seconds"] = round(time.monotonic() - phase, 3)
            phase = time.monotonic()
            verified_copies(Path(download), records, cache)
            timings["download_verification_seconds"] = round(time.monotonic() - phase, 3)
    with tempfile.TemporaryDirectory(prefix="reader-playwright-staging-") as staging:
        # Verify the ENTIRE cache before the first privileged command.
        phase = time.monotonic()
        verified_copies(cache, records, Path(staging))
        timings["staging_verification_seconds"] = round(time.monotonic() - phase, 3)
        phase = time.monotonic()
        if resolve(identity) != manifest:
            raise ValueError("APT transaction/metadata changed after cache-key resolution")
        timings["pre_privilege_recheck_seconds"] = round(time.monotonic() - phase, 3)
        if records:
            phase = time.monotonic()
            # Separate root-owned APT archive store prevents preexisting, unrecorded
            # archives in the runner's default store from entering this transaction.
            destination = command("sudo", "mktemp", "-d", "/var/cache/apt/reader-playwright-XXXXXXXX",
                                  capture_output=True).stdout.strip()
            if not re.fullmatch(r"/var/cache/apt/reader-playwright-[A-Za-z0-9]{8}", destination):
                raise ValueError("Unexpected privileged staging directory")
            for record in records:
                name = archive_name(record)
                command("sudo", "install", "-m", "0644", "--", str(Path(staging) / name), f"{destination}/{name}")
            timings["privileged_archive_copy_seconds"] = round(time.monotonic() - phase, 3)
            # Copies take time. Recheck exact closure and fresh metadata again
            # immediately before APT may unpack anything, without changing key.
            phase = time.monotonic()
            selected = [{k: r[k] for k in ("package", "version", "architecture")} for r in records]
            if simulate(exact_args(records)) != selected or metadata(selected) != records:
                raise ValueError("APT transaction/metadata changed during privileged staging")
            timings["pre_install_recheck_seconds"] = round(time.monotonic() - phase, 3)
            phase = time.monotonic()
            command("sudo", *APT, "-o", f"Dir::Cache::archives={destination}", "install", "-y",
                    "--no-download", "--no-install-recommends", "--no-remove", *exact_args(records))
            timings["apt_install_seconds"] = round(time.monotonic() - phase, 3)
        phase = time.monotonic()
        if official():
            raise ValueError("Official resolver still reports missing system dependencies")
        timings["post_install_official_seconds"] = round(time.monotonic() - phase, 3)
    evidence = {"cache_key": cache_key(manifest), "manifest": manifest,
                "source": "apt-download" if empty and records else "verified-cache",
                "archive_bytes": sum(r["size"] for r in records),
                "timings": timings,
                "total_seconds": round(time.monotonic() - start, 3)}
    print(json.dumps(evidence, sort_keys=True), flush=True)
    return evidence


def evidence(name, data):
    output = Path("runtime/playwright-deps")
    output.mkdir(parents=True, exist_ok=True)
    (output / name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "install"])
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    runner = Path(os.environ["RUNNER_TEMP"]).absolute()
    cache, manifest_path = args.cache.absolute(), args.manifest.absolute()
    if (cache.parent != runner or cache.name != "reader-playwright-debs"
            or manifest_path.parent != runner or manifest_path.name != "reader-playwright-packages.json"):
        raise ValueError("Only dedicated runner-temporary archive and manifest paths are allowed")
    identity = supported_environment()
    if args.action == "prepare":
        start = time.monotonic()
        command("sudo", *APT, "-o", "APT::Update::Error-Mode=any", "update")
        index_done = time.monotonic()
        manifest = resolve(identity)
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        cache.mkdir(exist_ok=False)
        key = cache_key(manifest)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"key={key}\n")
        data = {"cache_key": key, "manifest": manifest, "image_os": os.environ.get("ImageOS"),
                "image_version": os.environ.get("ImageVersion"),
                "index_seconds": round(index_done - start, 3),
                "resolution_seconds": round(time.monotonic() - index_done, 3)}
        evidence("prepare.json", data)
        print(json.dumps(data, sort_keys=True), flush=True)
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        # Re-resolve BEFORE downloads too; any changed plan requires a new key.
        phase = time.monotonic()
        if resolve(identity) != manifest:
            raise ValueError("APT transaction/metadata changed since cache-key resolution")
        precheck = round(time.monotonic() - phase, 3)
        result = install(cache, manifest, identity)
        result["timings"]["pre_download_recheck_seconds"] = precheck
        evidence("install.json", result)


if __name__ == "__main__":
    main()
