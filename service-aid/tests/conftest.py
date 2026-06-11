"""Shared fixtures: temp Habery, a saidified ACDC schema, recipient AID."""
import os
import tempfile

import pytest

os.environ.setdefault("HOME", tempfile.mkdtemp(prefix="serviceaid-test-"))

from keri.app.habbing import Habery
from keri.core.signing import Salter
from keri.core import scheming
from keri.kering import Kinds

from _schema import RATING_SCHEMA_SAD


@pytest.fixture
def issuer_hby():
    hby = Habery(name="svc", temp=True, salt=Salter(raw=b'0123456789abcdef').qb64)
    yield hby
    hby.close()


@pytest.fixture
def rating_schema(issuer_hby):
    """Saidify the schema, register it in the issuer's db, return (said, sad)."""
    schemer = scheming.Schemer(sed=dict(RATING_SCHEMA_SAD), kind=Kinds.json)
    issuer_hby.db.schema.pin(keys=(schemer.said,), val=schemer)
    return schemer.said, schemer.sed


@pytest.fixture
def recipient_pre():
    """A deterministic recipient AID prefix for tests."""
    rcp_hby = Habery(name="rcp", temp=True, salt=Salter(raw=b'fedcba9876543210').qb64)
    hab = rcp_hby.makeHab(name="rcp", transferable=True)
    pre = hab.pre
    rcp_hby.close()
    return pre
