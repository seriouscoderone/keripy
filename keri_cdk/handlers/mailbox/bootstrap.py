"""Mailbox libsodium shim + legacy container-image entrypoint.

The libsodium shim is defined HERE (not in a shared parent module) so the
mailbox Lambda asset is fully self-contained: ``Code.from_asset(
"keri_cdk/handlers/mailbox")`` ships this dir as ``/var/task``, and the handler
imports ``from bootstrap import ensure_libsodium`` locally. (Witness has its own
copy in keri_cdk/handlers/witness/bootstrap.py — duplicating a ~20-line platform
shim is correct: each Lambda asset is independent and a shared parent module
would not be in the deployed zip.)

Two deployment shapes are supported and both must resolve libsodium:
  - zip + KeriRuntimeLayer + LWA (what MailboxStack deploys): run.sh runs
    ``uvicorn mailbox_handler:app`` and the handler calls ensure_libsodium()
    before any keri import; libsodium ships in the layer at /opt/lib.
  - container image (legacy): Docker CMD ``python bootstrap.py`` runs this
    module's __main__ block, which boots uvicorn directly.

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


# Legacy container-image entrypoint (Docker CMD: `python bootstrap.py`). The
# zip + LWA shape does NOT use this; it runs run.sh -> uvicorn mailbox_handler:app
# and the shim is applied at the top of mailbox_handler. Guarded so importing
# this module (`from bootstrap import ensure_libsodium`) is side-effect-free.
if __name__ == "__main__":
    ensure_libsodium()

    import uvicorn
    from mailbox_handler import build_app

    uvicorn.run(
        build_app(),
        host="0.0.0.0",
        port=int(os.environ.get("AWS_LWA_PORT", "8080")),
        log_level="info",
    )
