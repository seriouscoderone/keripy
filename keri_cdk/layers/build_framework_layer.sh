#!/usr/bin/env bash
# Build the prebuilt arm64 ServiceAidFrameworkLayer asset.
#
# Lambda extracts a layer zip to /opt. We lay the asset out as:
#   serviceaid_framework/python/  -> /opt/python  (on sys.path: keri_serviceaid)
#
# We build INSIDE the AL arm64 Lambda base image (python3.14) so any compiled
# deps match the Lambda runtime, mirroring build_layer.sh. keri_serviceaid is a
# pure-Python package, so this is effectively a copy of the package tree plus a
# no-deps pip install (keri itself ships in KeriRuntimeLayer — do NOT bundle it
# here, the two layers compose at /opt/python).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/serviceaid_framework"
ROOT="$(git rev-parse --show-toplevel)"

rm -rf "$OUT"
mkdir -p "$OUT/python"

docker run --rm --platform linux/arm64 \
  --entrypoint /bin/sh \
  -v "$ROOT":/work -w /work \
  public.ecr.aws/lambda/python:3.14-arm64 -c '
    set -e
    # Install ONLY keri_serviceaid into python/ with no deps (keri + native libs
    # ride in KeriRuntimeLayer; bundling them again would bloat + shadow).
    # keri_serviceaid currently has NO package metadata (no pyproject/setup), so
    # the pip install fails and the cp -R is the LIVE install path today; the pip
    # form is kept so a future packaged keri_serviceaid installs cleanly.
    pip install --no-cache-dir --no-deps \
      ./keri_serviceaid -t /work/keri_cdk/layers/serviceaid_framework/python \
      2>/dev/null || \
    cp -R /work/keri_serviceaid /work/keri_cdk/layers/serviceaid_framework/python/keri_serviceaid

    PY=/work/keri_cdk/layers/serviceaid_framework/python
    find "$PY" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
    find "$PY" -type d -name "tests" -prune -exec rm -rf {} + 2>/dev/null || true
    find "$PY" -type d -name "*.dist-info" -prune -exec rm -rf {} + 2>/dev/null || true
  '

echo "---- keri_serviceaid present? ----"
ls -d "$OUT/python/keri_serviceaid" >/dev/null 2>&1 \
  && echo "OK: python/keri_serviceaid exists" || echo "MISSING: python/keri_serviceaid"
echo "---- unzipped layer size ----"
du -sh "$OUT"
