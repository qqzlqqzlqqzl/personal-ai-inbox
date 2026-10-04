"""Cache only an APT-index-verified font .deb, never installed system files.

Run prepare after Playwright install-deps refreshes the signed APT indices.
The manifest is outside the cache. Cache contents are untrusted until checked;
APT's handling of an existing local archive is NOT our checksum validation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import tempfile
import time

PACKAGE = "fonts-noto-cjk"
CACHED_FILE = PACKAGE + ".deb"


def command(*args, **kwargs):
    # The official job container already runs as root and does not ship sudo.
    # Preserve the same command after all archive validation; non-root stays unchanged.
    if args and args[0] == "sudo" and os.geteuid() == 0:
        args = args[1:]
    return subprocess.run(args, check=True, text=True, env={**os.environ, "LC_ALL": "C"}, **kwargs)


def metadata():
    policy = command("apt-cache", "policy", PACKAGE, capture_output=True).stdout
    candidates = re.findall(r"^\s*Candidate: (\S+)$", policy, re.MULTILINE)
    if len(candidates) != 1 or not re.fullmatch(r"[0-9][A-Za-z0-9.+:~\-]*", candidates[0]):
        raise ValueError("APT has no unambiguous font candidate")
    version = candidates[0]
    output = command("apt-cache", "show", f"{PACKAGE}={version}", capture_output=True).stdout
    records = []
    for stanza in output.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in stanza.splitlines()
                      if line and not line[0].isspace() and ": " in line)
        if fields.get("Package") != PACKAGE or fields.get("Version") != version:
            raise ValueError("Unexpected APT package/version")
        arch, size, digest = (fields.get(k, "") for k in ("Architecture", "Size", "SHA256"))
        filename = fields.get("Filename", "").rsplit("/", 1)[-1]
        if (arch != "all" or not size.isdigit() or int(size) <= 0
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or not re.fullmatch(r"fonts-noto-cjk_[A-Za-z0-9.+:~%\-]+_all\.deb", filename)):
            raise ValueError("APT font record lacks valid architecture/size/SHA256/filename")
        records.append(dict(package=PACKAGE, version=version, architecture=arch,
                            size=int(size), sha256=digest, filename=filename))
    if not records or any(record != records[0] for record in records):
        raise ValueError("Conflicting APT font records")
    return records[0]


def cache_key(record, release, architecture):
    # Include version even if a future package reuses byte-identical content.
    identity = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    distro = "-".join(release[k] for k in ("ID", "VERSION_ID"))
    if not re.fullmatch(r"[a-zA-Z0-9.\-]+", distro + "-" + architecture):
        raise ValueError("Invalid runner distribution/architecture")
    return (f"reader-cjk-deb-v2-{distro}-{architecture}-{record['sha256']}-"
            f"{hashlib.sha256(identity).hexdigest()[:16]}")


def directory(path):
    # Never let a restored directory redirect reads through a symlink.
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError(f"Symlink directory rejected: {item.name}")
    if not path.is_dir():
        raise ValueError("Font cache must be a directory")
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def verified_copy(folder, name, record, destination):
    """Reject all unexpected entries; copy/hash the same non-symlink descriptor."""
    dirfd = directory(folder)
    try:
        if os.listdir(dirfd) != [name]:
            raise ValueError("Unexpected font cache contents")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dirfd)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != record["size"]:
                raise ValueError("Font archive is not a single regular file of the expected size")
            digest, count = hashlib.sha256(), 0
            while chunk := source.read(1024 * 1024):
                count += len(chunk)
                digest.update(chunk)
                destination.write(chunk)
            if count != record["size"] or digest.hexdigest() != record["sha256"]:
                raise ValueError("Font archive SHA256/size mismatch")
    finally:
        os.close(dirfd)


def install(cache, record):
    start = time.monotonic()
    fd = directory(cache)
    try:
        empty = not os.listdir(fd)
    finally:
        os.close(fd)
    if empty:
        # Download as the normal user from configured signed APT repositories.
        # The freshly resolved candidate is pinned; any failure stays a failure.
        with tempfile.TemporaryDirectory(prefix="reader-cjk-download-") as download:
            command("apt-get", "download", f"{PACKAGE}={record['version']}", cwd=download)
            files = list(Path(download).iterdir())
            # apt-get may percent-encode a version epoch in its download name.
            if len(files) != 1 or not re.fullmatch(r"fonts-noto-cjk_[A-Za-z0-9.+:~%\-]+_all\.deb", files[0].name):
                raise ValueError("Unexpected APT download contents")
            with (cache / CACHED_FILE).open("xb") as cached:
                verified_copy(Path(download), files[0].name, record, cached)
    # Install a separate, freshly created copy, not a cache-controlled path.
    # No sudo call occurs until both size and SHA256 checks have passed.
    with tempfile.TemporaryDirectory(prefix="reader-cjk-install-") as staging:
        archive = Path(staging) / CACHED_FILE
        with archive.open("xb") as target:
            verified_copy(cache, CACHED_FILE, record, target)
        print(json.dumps({"font_package": record, "source": "apt-download" if empty else "verified-cache",
                          "prepare_seconds": round(time.monotonic() - start, 3)}), flush=True)
        # APT can prefer a repository even when given a local .deb of the same
        # version. Seed its standard archive name AFTER verification, then
        # forbid downloads during installation. APT archive reuse only checks
        # size in some versions; our explicit SHA256 check above is essential.
        apt_name = f"{PACKAGE}_{record['version'].replace(':', '%3a')}_all.deb"
        command("sudo", "install", "-m", "0644", "--", str(archive), f"/var/cache/apt/archives/{apt_name}")
        command("sudo", "apt-get", "install", "-y", "--no-download", f"{PACKAGE}={record['version']}")
    print(f"CJK font download/verify/install: {time.monotonic() - start:.3f}s", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "install"])
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    cache = args.cache.absolute()
    if args.action == "prepare":
        record = metadata()
        # Fresh workspace, outside the restored archive. Never cache this manifest.
        args.manifest.write_text(json.dumps(record), encoding="utf-8")
        cache.mkdir(exist_ok=False)
        release = platform.freedesktop_os_release()
        arch = command("dpkg", "--print-architecture", capture_output=True).stdout.strip()
        key = cache_key(record, release, arch)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"key={key}\n")
        print(json.dumps({"font_package": record, "cache_key": key}), flush=True)
    else:
        record = json.loads(args.manifest.read_text(encoding="utf-8"))
        if record != metadata():
            raise ValueError("APT font candidate changed since cache-key resolution")
        install(cache, record)


if __name__ == "__main__":
    main()
