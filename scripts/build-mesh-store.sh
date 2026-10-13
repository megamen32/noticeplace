#!/usr/bin/env bash
set -euo pipefail
source_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
build_root="$source_root/.tmp/mesh-build"
mkdir -p "$build_root" "$build_root/prefix" "$build_root/lib"
archive="$build_root/dqlite-1.17.1.tar.gz"
if [[ ! -f "$archive" ]]; then
    curl --fail --location --max-time 30 https://codeload.github.com/canonical/dqlite/tar.gz/refs/tags/v1.17.1 --output "$archive"
fi
printf '%s  %s\n' c9468a335aac9755b70f8f92726d84e93b486459767979288aafc43e66b5ff24 "$archive" | sha256sum --check --status
if [[ ! -f "$build_root/prefix/lib/libdqlite.so.0.0.1" ]]; then
    tar -xzf "$archive" -C "$build_root"
    (
        cd "$build_root/dqlite-1.17.1"
        export CFLAGS='-O2 -g0'
        autoreconf -i
        ./configure --prefix="$build_root/prefix" --disable-static
        make -j2
        make install
    )
fi
cp "$build_root/prefix/lib/libdqlite.so.0.0.1" "$build_root/lib/libdqlite.so.0"
cp "$build_root/dqlite-1.17.1/LICENSE" "$build_root/lib/DQLITE-LICENSE"
(
    cd "$source_root/mesh-store"
    export GOMAXPROCS=2
    export CGO_CFLAGS="-I$build_root/prefix/include"
    export CGO_LDFLAGS="-L$build_root/prefix/lib -lsqlite3 -Wl,-rpath,\$ORIGIN/lib"
    go mod verify
    go build -p 2 -tags nosqlite3 -o "$build_root/noticeplace-mesh-store" .
)
sha256sum "$build_root/noticeplace-mesh-store" "$build_root/lib/libdqlite.so.0"
