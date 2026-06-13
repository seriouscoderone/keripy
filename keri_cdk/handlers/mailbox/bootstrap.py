"""Lambda Web Adapter entrypoint: bootstrap libsodium, then boot uvicorn
serving the Falcon ASGI app on port 8080 (the AWS_LWA_PORT default).

Docker CMD: `python bootstrap.py` invokes this directly as __main__, so no
`if __name__ == "__main__":` guard is needed. AWS Lambda Web Adapter (loaded
as an extension by the container) intercepts inbound Lambda invocations,
forwards them as HTTP to localhost:8080, and streams the responses back
through the Lambda Runtime API with response-streaming framing.
"""

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

import uvicorn  # noqa: E402
from mailbox_handler import build_app  # noqa: E402

uvicorn.run(build_app(), host="0.0.0.0", port=8080, log_level="info")
