"""Service-AID libsodium shim, shipped in ServiceAidFrameworkLayer.

pysodium does ctypes.cdll.LoadLibrary(ctypes.util.find_library('sodium')). On the
Amazon Linux Lambda image find_library returns None (no gcc/ldconfig), so we
resolve the absolute .so path (from KeriRuntimeLayer at /opt/lib) and patch
find_library to return it. Idempotent; a no-op if the .so isn't found."""
import ctypes
import ctypes.util
import os

_patched = False


def ensure_libsodium():
    global _patched
    task_dir = os.path.dirname(os.path.abspath(__file__))
    lib_dirs = [os.path.join(task_dir, "lib"), task_dir]
    lib_dirs += [d for d in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep) if d]
    lib_dirs.append("/opt/lib")
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
