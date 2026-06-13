"""Lambda bootstrap: ensure libsodium is loadable before any keri imports.

Two deployment shapes are supported and both must resolve libsodium:
  - container image: libsodium ships next to the code under /var/task/lib,
    entrypoint `bootstrap.handler` (this module wraps the handler).
  - zip + KeriRuntimeLayer: libsodium ships in the layer, extracted to
    /opt/lib (LD_LIBRARY_PATH=/opt/lib), entrypoint `witness_handler.handler`
    (which calls ensure_libsodium() itself before importing keri).

The actual libsodium shim now lives in the shared module
``keri_cdk/handlers/_libsodium.py`` (so the witness and mailbox handlers use
identical resolution logic). This module re-exports ``ensure_libsodium`` from
there and preserves the container-image ``bootstrap.handler`` entrypoint.
"""

import os
import sys

# ``_libsodium`` lives one level up (keri_cdk/handlers/_libsodium.py). Each
# handler dir is its own Lambda asset, so make the parent ``handlers/`` dir
# importable both in the host/test env and (after Task 9 vendoring) at runtime.
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

try:
    from _libsodium import ensure_libsodium  # zip/container: parent dir on sys.path
except ImportError:  # pragma: no cover - resolved as a package in the host env
    from keri_cdk.handlers._libsodium import ensure_libsodium  # noqa: F401

ensure_libsodium()

# Expose `handler` for the container-image entrypoint `bootstrap.handler`.
# Guarded against the circular case: when witness_handler is imported first
# (zip+layer entrypoint witness_handler.handler) it calls ensure_libsodium()
# via `from bootstrap import ...`, which re-enters this module while
# witness_handler is only partially initialized — `handler` isn't bound yet,
# so the import would raise. We swallow that; the zip path doesn't use
# bootstrap.handler anyway.
try:
    from witness_handler import handler  # noqa: E402,F401
except ImportError:  # pragma: no cover - circular re-entry on the zip path
    pass
