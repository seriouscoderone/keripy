"""Alias the relocated Lambda handlers under their flat module names.

The witness/mailbox handlers were relocated into the keri_cdk library
(keri_cdk/handlers/{witness,mailbox}/). The handler tests here still refer to
them by their flat module names — both as imports (`from mailbox_handler import
build_app`) and, crucially, as `unittest.mock.patch("mailbox_handler._hab")`
string targets, which must resolve to a top-level entry in `sys.modules`.

Importing the package module and registering it under the flat name keeps every
existing reference pointing at the SAME object as `keri_cdk.handlers.mailbox.
mailbox_handler` (and likewise for witness), so the tests exercise the real
relocated code without per-test edits.
"""
import sys

import pytest

from keri_cdk.handlers.mailbox import mailbox_handler as _mailbox_handler
from keri_cdk.handlers.mailbox import ws_handlers as _ws_handlers
from keri_cdk.handlers.witness import witness_handler as _witness_handler

sys.modules.setdefault("mailbox_handler", _mailbox_handler)
sys.modules.setdefault("ws_handlers", _ws_handlers)
sys.modules.setdefault("witness_handler", _witness_handler)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: requires a moto cold-start or real AWS (deselected by default)",
    )
