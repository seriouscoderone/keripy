"""Lambda bootstrap: ensure libsodium is loadable before any keri imports."""

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

_lib_path = None
for _p in _candidates:
    if os.path.exists(_p):
        _lib_path = _p
        break

if _lib_path:
    _orig_find_library = ctypes.util.find_library

    def _patched_find_library(name):
        if name in ("sodium", "libsodium"):
            return _lib_path
        return _orig_find_library(name)

    ctypes.util.find_library = _patched_find_library

from witness_handler import handler  # noqa: E402
