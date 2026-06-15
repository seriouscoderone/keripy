from aws_cdk import Stack
from constructs import Construct
from aws_cdk import aws_dynamodb as ddb


class WatcherStack(Stack):
    """Seam for a future KEL-observing / duplicity-checking watcher. Phase C ships the
    construct API only — no handler. When built, the watcher pools its Baser onto the
    shared ``core_table`` under namespace ``<stack-name>:kel`` with the same
    LeadingKeys grant (``<stack-name>:*#*``) as the witness — see
    docs/superpowers/specs/2026-06-14-cdk-phase-c-design.md."""

    def __init__(self, scope: Construct, cid: str, *, name: str, domain_name: str,
                 hosted_zone_id: str, core_table: "ddb.ITable", witnesses=None, **kw):
        super().__init__(scope, cid, **kw)
        raise NotImplementedError(
            "WatcherStack is a seam: the watcher handler is a future build. It will pool "
            "onto core_table under namespace <stack-name>:kel.")
