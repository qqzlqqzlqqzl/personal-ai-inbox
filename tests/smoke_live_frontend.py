"""Check that both encodings can load the same live reader build."""

import argparse
import gzip
import http.client
import re
import sys
import zlib
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

BASE = "https://lylme.cn/inbox/"
ENCODINGS = ("gzip", "identity")
ENTRY_BUNDLE = re.compile(rb"(?<![\w-])assets/index-[A-Za-z0-9_-]+\.js(?![\w.-])")


def fetch(url, encoding):
    request = Request(url, headers={
        "Accept-Encoding": encoding,
        "User-Agent": "personal-ai-inbox-frontend-smoke/1.0",
    })
    try:
        with urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
            body = response.read()
            if response.headers.get("Content-Encoding", "").lower() == "gzip":
                body = gzip.decompress(body)
            return body
    except (OSError, EOFError, http.client.HTTPException, ValueError, zlib.error) as error:
        raise ValueError(f"{url} ({encoding}): {error}") from error


def bundle_references(body):
    return {path.decode("ascii") for path in ENTRY_BUNDLE.findall(body)}


def verify(base, build):
    references = {
        f"{name} ({encoding})": bundle_references(fetch(urljoin(base, path), encoding))
        for name, path in (("sw.js", "sw.js"), ("index.html", ""))
        for encoding in ENCODINGS
    }
    if build is not None:
        index = build / "index.html"
        references[str(index)] = bundle_references(index.read_bytes())
    expected = references["index.html (identity)"]
    if not expected or any(found != expected for found in references.values()):
        report = "\n".join(f"  {name}: {', '.join(sorted(found)) or 'no index bundle'}"
                           for name, found in references.items())
        raise ValueError(f"Index bundle references differ or are missing:\n{report}")
    for bundle in sorted(expected):
        for encoding in ENCODINGS:
            fetch(urljoin(base, bundle), encoding)
    return sorted(expected)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--build", type=Path, help="Compare against this build's index.html")
    args = parser.parse_args(argv)
    base = args.base.rstrip("/") + "/"
    try:
        bundles = verify(base, args.build)
    except (OSError, ValueError) as error:
        print(f"Frontend smoke failed: {error}", file=sys.stderr)
        return 1
    print(f"Frontend smoke passed for {base}: {', '.join(bundles)} (gzip and identity)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
