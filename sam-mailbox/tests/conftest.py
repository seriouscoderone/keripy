"""Put the relocated mailbox_handler on sys.path for flat `import mailbox_handler`.

mailbox_handler.py moved into the keri_cdk library
(keri_cdk/handlers/mailbox/); these tests still import it as a flat module, so
add that directory to sys.path. sam-mailbox/tests -> sam-mailbox -> repo root,
then into keri_cdk/handlers/mailbox.
"""

import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(os.path.dirname(_here))
_mailbox_handler_dir = os.path.join(_repo_root, "keri_cdk", "handlers", "mailbox")
if _mailbox_handler_dir not in sys.path:
    sys.path.insert(0, _mailbox_handler_dir)
