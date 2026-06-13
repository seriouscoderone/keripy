"""Lambda bootstrap: load libsodium before keri imports, then expose handler."""
import ctypes
import ctypes.util
import os

_task_dir = os.path.dirname(os.path.abspath(__file__))
_candidates = [
    os.path.join(_task_dir, "lib", "libsodium.so.26"),
    os.path.join(_task_dir, "lib", "libsodium.so"),
    os.path.join(_task_dir, "libsodium.so.26"),
    os.path.join(_task_dir, "libsodium.so"),
]
_lib_path = next((p for p in _candidates if os.path.exists(p)), None)
if _lib_path:
    _orig = ctypes.util.find_library

    def _patched(name):
        return _lib_path if name in ("sodium", "libsodium") else _orig(name)

    ctypes.util.find_library = _patched

from keri_cdk.handlers.serviceaid.handler import handler  # noqa: E402,F401
