#!/usr/bin/env bash
# Exact official Go distribution, verified before extraction/execution.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
dest=${1:?Supply a new local toolchain directory}
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || { echo 'This bootstrap supports Linux amd64 only' >&2; exit 1; }
[[ ! -e "$dest" ]] || { echo 'Toolchain destination already exists' >&2; exit 1; }
version=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["go_version"])' "$root/pins.json")
expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["go_linux_amd64_sha256"])' "$root/pins.json")
mkdir -p "$dest"
archive="$dest/toolchain.tar.gz"
curl --fail --connect-timeout 30 --max-time 300 --location --proto '=https' --tlsv1.2 --retry 3 "https://go.dev/dl/go${version}.linux-amd64.tar.gz" -o "$archive"
printf '%s  %s\n' "$expected" "$archive" | sha256sum --check --strict
tar -xzf "$archive" -C "$dest"
GOTOOLCHAIN=local "$dest/go/bin/go" version
