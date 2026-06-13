#!/usr/bin/env bash
# Build the prebuilt arm64 KeriRuntimeLayer asset.
#
# Lambda extracts a layer zip to /opt. So we lay the asset out as:
#   keri_runtime/python/  -> /opt/python  (on sys.path: keri + pip deps)
#   keri_runtime/lib/     -> /opt/lib      (libsodium .so; LD_LIBRARY_PATH=/opt/lib)
#
# keripy's setup.py pins python_requires='>=3.14.0', so we build on the
# python3.14 Lambda base image (python3.14 is GA in Lambda since Nov 2025).
# We build INSIDE the AL arm64 Lambda base image so the wheels (lmdb, blake3,
# cryptography, ...) and the libsodium .so are the exact arch/ABI the Lambda
# runtime loads. This mirrors how sam-witness/Dockerfile assembled keripy +
# libsodium (it apt-installed libsodium23 -> libsodium.so.26 and copied it into
# lib/, with LD_LIBRARY_PATH=/var/task/lib). Here the AL image is dnf-based, so
# we dnf-install libsodium and copy whatever libsodium.so* it ships into lib/.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/keri_runtime"
ROOT="$(git rev-parse --show-toplevel)"

rm -rf "$OUT"
mkdir -p "$OUT/python" "$OUT/lib"

docker run --rm --platform linux/arm64 \
  --entrypoint /bin/sh \
  -v "$ROOT":/work -w /work \
  public.ecr.aws/lambda/python:3.14-arm64 -c '
    set -e
    # libsodium runtime .so (pysodium dlopen-loads it via ctypes find_library).
    # The AL2023 Lambda base image is dnf/microdnf-based.
    (dnf install -y libsodium || microdnf install -y libsodium || yum install -y libsodium) >/dev/null 2>&1 || true

    # keripy + all pip deps into python/ (-> /opt/python on sys.path).
    # uvicorn (+ h11, click) is the ASGI HTTP server run.sh launches for the
    # mailbox Falcon app; keripy itself does not depend on it, so add it here.
    # SSE works over h11 — no need for uvicorn[standard].
    pip install --no-cache-dir . uvicorn -t /work/keri_cdk/layers/keri_runtime/python

    # Copy the libsodium shared object(s) into lib/ (-> /opt/lib).
    # Search the whole image because the package path varies by distro.
    found=0
    for d in /usr/lib64 /usr/lib /lib64 /lib; do
      for f in "$d"/libsodium.so*; do
        [ -e "$f" ] || continue
        cp -P "$f" /work/keri_cdk/layers/keri_runtime/lib/
        found=1
      done
    done
    if [ "$found" = 0 ]; then
      # Last resort: hunt the whole filesystem.
      find / -name "libsodium.so*" 2>/dev/null -exec cp -P {} /work/keri_cdk/layers/keri_runtime/lib/ \; || true
    fi

    # Trim weight: tests, bytecode caches, dist-info that the runtime never needs.
    PY=/work/keri_cdk/layers/keri_runtime/python
    find "$PY" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
    find "$PY" -type d -name "tests" -prune -exec rm -rf {} + 2>/dev/null || true
    find "$PY" -type d -name "*.dist-info" -prune -exec rm -rf {} + 2>/dev/null || true
  '

echo "---- libsodium .so placed under lib/ ----"
ls -l "$OUT/lib" || true
echo "---- keri package present? ----"
ls -d "$OUT/python/keri" >/dev/null 2>&1 && echo "OK: python/keri exists" || echo "MISSING: python/keri"
echo "---- unzipped layer size ----"
du -sh "$OUT"
