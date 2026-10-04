#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
source_dir=${1:?Supply a patched pinned source directory}
evidence=${2:?Supply an evidence directory outside the source tree}
mkdir -p "$evidence"
evidence=$(cd "$evidence" && pwd)
source_dir=$(cd "$source_dir" && pwd)
export GOTOOLCHAIN=local GOWORK=off GOFLAGS=-mod=readonly
expected=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["go_version"])' "$root/pins.json")
[[ $(go env GOVERSION) == "go$expected" ]] || { echo "Expected exact Go $expected" >&2; exit 1; }
python3 "$root/scripts/verify.py" patched "$source_dir" | tee "$evidence/source-verification.txt"
{
  go version
  uname -a
  printf 'Upstream commit: '; git -C "$source_dir" rev-parse HEAD
  printf 'Patch: '; sha256sum "$root/miniflux-2.3.3-entry-metadata.patch"
} > "$evidence/build-identity.txt"
cd "$source_dir"
go mod download
go mod verify | tee "$evidence/module-verification.txt"
# Formatting is recorded separately so it can be reviewed even on a failed build.
gofmt -d internal/api/entry_metadata*.go internal/storage/entry_metadata*.go > "$evidence/gofmt.diff"
[[ ! -s "$evidence/gofmt.diff" ]] || { echo "gofmt drift; see evidence" >&2; exit 1; }
go vet ./... 2>&1 | tee "$evidence/go-vet.txt"
ISSUE73_POSTGRES_URL= ISSUE73_REQUIRE_POSTGRES=0 go test -race -count=1 -timeout=8m ./... 2>&1 | tee "$evidence/go-test.txt"
# The explicit second run fails if PostgreSQL is not configured; a skipped test is not acceptance.
ISSUE73_REQUIRE_POSTGRES=1 go test -race -count=1 -v -timeout=2m ./internal/api -run '^TestMetadataPostgres$' 2>&1 | tee "$evidence/postgres-auth-tests.txt"
# Record the full, explicit protocol matrix, including injected storage failures.
ISSUE73_POSTGRES_URL= ISSUE73_REQUIRE_POSTGRES=0 go test -race -count=1 -v ./internal/api ./internal/storage -run '^TestMetadata' 2>&1 | tee "$evidence/metadata-tests.txt"
CGO_ENABLED=0 go build -trimpath -buildvcs=false -ldflags='-s -w -X miniflux.app/v2/internal/version.Version=2.3.3 -X miniflux.app/v2/internal/version.Commit=c4d54f87a81b30aa173fddf05d7ff83ae7da5796' -o "$evidence/miniflux-issue73-linux-amd64" .
# Preserve upstream notices with the candidate binary, including optional NOTICE.
cp LICENSE "$evidence/LICENSE"
if [[ -f NOTICE ]]; then
  cp NOTICE "$evidence/NOTICE"
fi
sha256sum "$evidence/miniflux-issue73-linux-amd64" > "$evidence/miniflux-issue73-linux-amd64.sha256"
go version -m "$evidence/miniflux-issue73-linux-amd64" > "$evidence/binary-build-info.txt"
python3 "$root/scripts/verify.py" patched "$source_dir" | tee "$evidence/final-source-verification.txt"
