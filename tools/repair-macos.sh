#!/usr/bin/env bash
# cibuildwheel macOS wheel repair.
#
# delocate-wheel rewrites the bundled dylibs' install-names, which invalidates
# their ad-hoc code signatures. On Apple Silicon the dynamic loader SIGKILLs a
# dylib whose signature no longer matches its contents, so runswmm dies (rc=-9)
# the instant it loads libswmm5/libomp. delocate re-signs the main binaries but
# leaves the from-source OpenMP libs (libomp + libiomp5/libgomp aliases) and
# libswmm-output with stale signatures. Re-sign every bundled dylib and the
# runswmm executable ad-hoc after delocate, then repack so the wheel RECORD
# stays consistent with the re-signed files.
set -euo pipefail

delocate_archs="$1"
dest_dir="$2"
wheel="$3"

delocate-wheel --require-archs "$delocate_archs" -w "$dest_dir" -v "$wheel"

python -m pip install --quiet --upgrade wheel
for repaired in "$dest_dir"/*.whl; do
  work="$(mktemp -d)"
  python -m wheel unpack --dest "$work" "$repaired"
  # -type f skips the alias symlinks (their real target is signed via *.dylib).
  find "$work" \( -name '*.dylib' -o -name runswmm \) -type f -print0 |
    xargs -0 codesign --force --sign -
  rm -f "$repaired"
  python -m wheel pack --dest-dir "$dest_dir" "$work"/*/
  rm -rf "$work"
done
