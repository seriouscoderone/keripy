"""Lambda bootstrap: ensure libsodium is loadable before any keri imports.

Two deployment shapes are supported and both must resolve libsodium:
  - container image: libsodium ships next to the code under /var/task/lib,
    entrypoint `bootstrap.handler` (this module wraps the handler).
  - zip + KeriRuntimeLayer: libsodium ships in the layer, extracted to
    /opt/lib (LD_LIBRARY_PATH=/opt/lib), entrypoint `witness_handler.handler`
    (which calls ensure_libsodium() itself before importing keri).

pysodium does `ctypes.cdll.LoadLibrary(ctypes.util.find_library('sodium'))`.
On the Amazon Linux Lambda image there is no gcc/ldconfig for find_library to
consult, so a SONAME-only lookup returns None and the load fails. We resolve
the absolute .so path ourselves and patch find_library to return it.
"""

import ctypes
import ctypes.util
import os

_patched = False


def ensure_libsodium():
    """Resolve libsodium's absolute path and patch ctypes.util.find_library so
    pysodium loads it. Idempotent; safe to call from multiple entrypoints.

    Search order: <code>/lib, <code>, every LD_LIBRARY_PATH dir, then the
    default Lambda layer mount /opt/lib. Returns the resolved path or None.
    """
    global _patched
    task_dir = os.path.dirname(os.path.abspath(__file__))
    lib_dirs = [os.path.join(task_dir, "lib"), task_dir]
    lib_dirs += [d for d in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep) if d]
    lib_dirs.append("/opt/lib")  # default Lambda layer lib mount
    sonames = ["libsodium.so.26", "libsodium.so"]
    candidates = [os.path.join(d, s) for d in lib_dirs for s in sonames]

    lib_path = next((p for p in candidates if os.path.exists(p)), None)
    if lib_path and not _patched:
        orig = ctypes.util.find_library

        def _patched_find_library(name):
            if name in ("sodium", "libsodium"):
                return lib_path
            return orig(name)

        ctypes.util.find_library = _patched_find_library
        _patched = True
    return lib_path


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
