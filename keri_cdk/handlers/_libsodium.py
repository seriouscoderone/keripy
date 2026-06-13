"""Shared libsodium shim for all KERI Lambda handlers (witness + mailbox).

pysodium does ``ctypes.cdll.LoadLibrary(ctypes.util.find_library('sodium'))``.
On the Amazon Linux Lambda image there is no gcc/ldconfig for find_library to
consult, so a SONAME-only lookup returns None and the load fails. We resolve
the absolute .so path ourselves and patch ``ctypes.util.find_library`` to
return it. ``ensure_libsodium()`` must be called BEFORE any ``keri`` import in
every handler entrypoint (both the witness handler and the mailbox handler).

Deployment shapes — both must resolve libsodium:
  - zip + KeriRuntimeLayer: libsodium ships in the layer, extracted to
    ``/opt/lib`` (``LD_LIBRARY_PATH=/opt/lib``). This is the shape both the
    witness and the mailbox stacks deploy.
  - container image (legacy witness shape): libsodium ships next to the code
    under ``/var/task/lib``.

Packaging note: each handler dir is shipped as its own Lambda asset
(``Code.from_asset("keri_cdk/handlers/<name>")``), so this module — which lives
one level up at ``keri_cdk/handlers/_libsodium.py`` — is NOT automatically
inside a handler's ``/var/task``. Handler entrypoints import it via a
sys.path-tolerant shim (they add the parent ``handlers/`` dir to sys.path),
which works in the host/test env today; the Task 9 deploy step vendors this
file into each handler asset so the import resolves at runtime too.
"""

import ctypes
import ctypes.util
import os

_patched = False


def ensure_libsodium():
    """Resolve libsodium's absolute path and patch ctypes.util.find_library so
    pysodium loads it. Idempotent; safe to call from multiple entrypoints.

    Search order: ``<code>/lib``, ``<code>``, every ``LD_LIBRARY_PATH`` dir,
    then the default Lambda layer mount ``/opt/lib``. Returns the resolved path
    or ``None`` (a no-op when libsodium loads normally, e.g. a host venv).

    NOTE: ``<code>`` is resolved relative to whatever entrypoint module's
    directory is the first ``LD_LIBRARY_PATH`` / cwd candidate; we additionally
    probe this module's own directory and its parent so the search still finds a
    co-located ``lib/`` in the container shape regardless of where the shared
    module is vendored.
    """
    global _patched
    here = os.path.dirname(os.path.abspath(__file__))
    lib_dirs = [
        os.path.join(here, "lib"),
        here,
        os.path.join(os.path.dirname(here), "lib"),
        os.path.dirname(here),
        os.getcwd(),
        os.path.join(os.getcwd(), "lib"),
    ]
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
