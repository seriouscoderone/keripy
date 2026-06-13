"""Legacy container-image entrypoint: bootstrap libsodium, then boot uvicorn
serving the Falcon ASGI app on port 8080 (the AWS_LWA_PORT default).

The zip + KeriRuntimeLayer + LWA shape (what MailboxStack deploys) does NOT use
this module — it runs ``run.sh`` -> ``uvicorn mailbox_handler:app`` and the
libsodium shim is applied at the top of ``mailbox_handler``. This module is
retained for the container-image shape, where Docker CMD ``python bootstrap.py``
invokes it directly. It delegates libsodium resolution to the shared shim
``keri_cdk/handlers/_libsodium.py`` so witness + mailbox stay identical.
"""

import os
import sys

# Shared shim lives one level up (keri_cdk/handlers/_libsodium.py).
_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)
try:
    from _libsodium import ensure_libsodium
except ImportError:  # pragma: no cover - resolved as a package in the host env
    from keri_cdk.handlers._libsodium import ensure_libsodium

ensure_libsodium()

import uvicorn  # noqa: E402
from mailbox_handler import build_app  # noqa: E402

uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("AWS_LWA_PORT", "8080")), log_level="info")
