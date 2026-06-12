# -*- encoding: utf-8 -*-
"""
keri.db.secretkeeper module

A secret-backed, in-memory Keeper: the entire (small) keystore lives in one
KMS-encrypted secret per stack. Pure storage substitution for the LMDB/DynamoDB
keeper — keripy's Keeper/Manager/aeid surface is unchanged.
"""
from __future__ import annotations

import base64
import contextlib
import json
import zlib

_BLOB_VERSION = 1


def dumpKeeper(data: dict[str, dict[str, bytes]]) -> str:
    """Serialize the keeper dict to a compressed, base64-ascii blob.

    data: {subdb_name: {hex_key: value_bytes}}. Values are bytes (CESR);
    base64-encoded for JSON transport, then the whole doc is zlib-compressed.
    """
    payload = {"v": _BLOB_VERSION,
               "d": {sub: {k: base64.b64encode(v).decode("ascii")
                           for k, v in items.items()}
                     for sub, items in data.items()}}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(zlib.compress(raw)).decode("ascii")


def loadKeeper(blob: str | None) -> dict[str, dict[str, bytes]]:
    """Inverse of dumpKeeper. None/empty -> {} (fresh keeper)."""
    if not blob:
        return {}
    raw = zlib.decompress(base64.b64decode(blob))
    payload = json.loads(raw)
    if payload.get("v") != _BLOB_VERSION:
        raise ValueError(f"unsupported keeper blob version: {payload.get('v')}")
    return {sub: {k: base64.b64decode(v) for k, v in items.items()}
            for sub, items in payload["d"].items()}


class SecretStore:
    """Thin pluggable secret-store client. Secrets Manager by default; SSM
    Parameter Store SecureString selectable via kind='ssm' (interface-ready)."""

    def __init__(self, *, region: str = "us-east-1", endpoint_url: str | None = None,
                 kind: str = "secretsmanager", session=None):
        import boto3
        self.kind = kind
        kwa = {"region_name": region}
        if endpoint_url:
            kwa["endpoint_url"] = endpoint_url
        src = session if session is not None else boto3
        if kind == "secretsmanager":
            self._c = src.client("secretsmanager", **kwa)
        elif kind == "ssm":
            self._c = src.client("ssm", **kwa)
        else:
            raise ValueError(f"unknown secret store kind: {kind!r}")

    def get(self, name: str) -> str | None:
        """Return the secret string, or None if it does not exist."""
        if self.kind == "secretsmanager":
            try:
                return self._c.get_secret_value(SecretId=name)["SecretString"]
            except self._c.exceptions.ResourceNotFoundException:
                return None
        else:  # ssm
            try:
                return self._c.get_parameter(Name=name, WithDecryption=True
                                             )["Parameter"]["Value"]
            except self._c.exceptions.ParameterNotFound:
                return None

    def put(self, name: str, value: str) -> None:
        """Create-or-update the secret value (overwrites); do NOT use for
        get-or-create — use _create_only."""
        if self.kind == "secretsmanager":
            try:
                self._c.put_secret_value(SecretId=name, SecretString=value)
            except self._c.exceptions.ResourceNotFoundException:
                self._c.create_secret(Name=name, SecretString=value)
        else:  # ssm
            self._c.put_parameter(Name=name, Value=value, Type="SecureString",
                                  Overwrite=True)

    def _create_only(self, name: str, value: str) -> bool:
        """Create the secret only if absent; never overwrite. True if created."""
        if self.kind == "secretsmanager":
            try:
                self._c.create_secret(Name=name, SecretString=value)
                return True
            except self._c.exceptions.ResourceExistsException:
                return False
        else:  # ssm
            try:
                self._c.put_parameter(Name=name, Value=value,
                                      Type="SecureString", Overwrite=False)
                return True
            except self._c.exceptions.ParameterAlreadyExists:
                return False

    def get_or_create(self, name: str, mint) -> tuple[bool, str]:
        """Return (created, value). If absent, create (atomically, never
        overwriting); else return existing. Race-safe: a caller that loses the
        create race re-reads the winner's value."""
        existing = self.get(name)
        if existing is not None:
            return False, existing
        value = mint()
        if self._create_only(name, value):
            return True, value
        return False, self.get(name)   # lost the race — winner's value wins


def _hexk(key) -> str:
    """Normalize a db key to a hex string for the in-memory dict.

    Mirrors dynamodbing._hex: bytes/bytearray/memoryview/str all map to the
    same hex-string keyspace so the blob round-trips losslessly (Task 2).
    """
    if isinstance(key, memoryview):
        key = bytes(key)
    if isinstance(key, (bytes, bytearray)):
        return bytes(key).hex()
    return str(key).encode("utf-8").hex()


class SecretSubDb:
    """A declared keeper sub-database handle (mirrors dynamodbing.DynamoSubDb).

    Carries only the attributes the subing/koming wrappers touch on a subdb:
    ``.name`` (used to address the per-subdb dict) and ``.flags()``.
    """

    def __init__(self, name: str):
        self.name = name
        self.dupsort = False
        self.opened = True

    def flags(self) -> dict:
        return {"dupsort": self.dupsort}


class SecretEnv:
    """Named sub-db opener used by subing/koming wrappers (mirrors DynamoEnv).

    Unlike DynamoEnv, stores are not pre-declared: the keeper's subdb set is
    fixed by setup_keeper, and lazily materializing a handle on first open is
    harmless and keeps SecretKeeper free of a separate declaration step.
    """

    def __init__(self, owner: "SecretKeeper"):
        self.owner = owner

    def open_db(self, key, dupsort: bool = False) -> SecretSubDb:
        name = key.decode("utf-8") if isinstance(key, bytes) else key
        if name not in self.owner._subdbs:
            self.owner._subdbs[name] = SecretSubDb(name)
            self.owner._data.setdefault(name, {})
        return self.owner._subdbs[name]


class SecretKeeper:
    """In-memory keeper persisted as one KMS-encrypted secret.

    Implements the single-value subset of the LMDBer/DynamoDBer interface that
    the keeper Subers actually call — putVal/setVal/getVal/remVal,
    getTopItemIter, and the cnt/remTop family — backed by a plain nested dict
    ``{subdb_name: {hex_key: val_bytes}}``. Every mutation auto-flushes the
    whole secret doc, preserving ``salt``/``bran`` alongside the keeper blob.

    Method NAMES, signatures, key/value types (bytes in, bytes out) and the
    ``getTopItemIter`` yield-shape ``(key_bytes, val_bytes)`` mirror
    dynamodbing.DynamoDBer exactly so setup_keeper + Manager work unchanged.

    IoSet/IoDup/ordinal (On*) methods — only reached by group-multisig keeper
    Subers (smids/rmids CatCesrIoSetSuber), unused in single-sig — raise
    NotImplementedError rather than silently mis-behaving.
    """

    def __init__(self, *, store, secret_name: str, salt, bran,
                 keeper_blob: str | None = None, no_store: bool = False):
        self.store = store
        self.secret_name = secret_name
        self.name = "keeper"
        self.salt = salt
        self.bran = bran
        self._no_store = no_store
        self._defer_depth = 0
        self._data = loadKeeper(keeper_blob)
        self._subdbs = {n: SecretSubDb(n) for n in self._data}
        self.env = SecretEnv(self)
        self.opened = True
        self.temp = False
        self.readonly = False
        self.path = f"secret://{secret_name}"
        self._version = None

    @classmethod
    def open(cls, *, store, secret_name: str) -> "SecretKeeper":
        """Load an existing keeper secret (or start fresh if absent)."""
        raw = store.get(secret_name)
        doc = (json.loads(raw) if raw
               else {"v": 1, "salt": None, "bran": None, "keeper": None})
        return cls(store=store, secret_name=secret_name, salt=doc.get("salt"),
                   bran=doc.get("bran"), keeper_blob=doc.get("keeper"))

    # ---- Persistence ----

    def _flush(self):
        """Serialize the whole keeper + salt/bran and overwrite the secret.

        No-op while a deferflush block is active (._defer_depth > 0): the
        block flushes once atomically on its outermost exit.
        """
        if self._no_store or self.store is None or self._defer_depth > 0:
            return
        doc = {"v": 1, "salt": self.salt, "bran": self.bran,
               "keeper": dumpKeeper(self._data)}
        self.store.put(self.secret_name,
                       json.dumps(doc, separators=(",", ":")))

    @contextlib.contextmanager
    def deferflush(self):
        """Suppress per-mutation flush within the block; flush once atomically
        on CLEAN exit. On an exception, do NOT flush — leaving the prior secret
        intact rather than persisting a half-written keeper.

        Use around establishment ceremonies (incept/rotate) so a crash
        mid-ceremony leaves the prior secret intact, not a half-written keeper.
        Re-entrant: nested deferflush blocks only flush at the outermost exit.
        """
        self._defer_depth += 1
        try:
            yield self
        except BaseException:
            self._defer_depth -= 1
            raise                       # abort: no partial flush
        else:
            self._defer_depth -= 1
            if self._defer_depth == 0:
                self._flush()

    # ---- Single-value CRUD (the keeper's real surface) ----

    def putVal(self, db, key, val) -> bool:
        """Insert val at key without overwriting. True if inserted, else False."""
        if not key:
            raise KeyError(f"Key: `{key}` is empty, too big, or wrong DUPFIXED size.")
        items = self._data.setdefault(db.name, {})
        hk = _hexk(key)
        if hk in items:
            return False
        items[hk] = bytes(val)
        self._flush()
        return True

    def setVal(self, db, key, val) -> bool:
        """Insert or overwrite val at key. Always True."""
        if not key:
            raise KeyError(f"Key: `{key}` is empty, too big, or wrong DUPFIXED size.")
        self._data.setdefault(db.name, {})[_hexk(key)] = bytes(val)
        self._flush()
        return True

    def getVal(self, db, key):
        """Return stored bytes at key, or None when missing."""
        if not key:
            raise KeyError(f"Key: `{key}` is empty, too big, or wrong DUPFIXED size.")
        val = self._data.get(db.name, {}).get(_hexk(key))
        return bytes(val) if val is not None else None

    def remVal(self, db, key) -> bool:
        """Remove entry at key. True if it existed, else False."""
        items = self._data.get(db.name, {})
        hk = _hexk(key)
        if hk in items:
            del items[hk]
            self._flush()
            return True
        return False

    delVal = remVal  # backwards compat alias (mirrors DynamoDBer)

    # ---- Top-level iteration / management ----

    def getTopItemIter(self, db, top: bytes = b""):
        """Yield (key_bytes, val_bytes) for keys whose original bytes start
        with top. Empty top yields the whole subdb. Mirrors DynamoDBer."""
        prefix = _hexk(top) if top else ""
        for hk, v in list(self._data.get(db.name, {}).items()):
            if hk.startswith(prefix):
                yield (bytes.fromhex(hk), bytes(v))

    def remTop(self, db, top: bytes = b"") -> bool:
        """Remove all entries whose keys start with top. True if any removed."""
        items = self._data.get(db.name, {})
        prefix = _hexk(top) if top else ""
        doomed = [hk for hk in items if hk.startswith(prefix)]
        if not doomed:
            return False
        for hk in doomed:
            del items[hk]
        self._flush()
        return True

    delTop = remTop  # backwards compat alias

    def cntTop(self, db, top: bytes = b"") -> int:
        """Count all entries whose keys start with top."""
        prefix = _hexk(top) if top else ""
        return sum(1 for hk in self._data.get(db.name, {}) if hk.startswith(prefix))

    def cntAll(self, db) -> int:
        """Count all values stored in db."""
        return len(self._data.get(db.name, {}))

    # ---- Lifecycle ----

    def flush(self) -> int:
        """Force a flush of the secret doc. Returns 0 (parity with DynamoDBer)."""
        self._flush()
        return 0

    def close(self, clear: bool = False):
        """Close the keeper, optionally clearing all data first."""
        if clear:
            self._data = {}
            self._subdbs = {}
            self._flush()
        self.opened = False

    # ---- Unsupported surface (group-multisig / ordinal — unused by keeper) ----

    def _unsupported(self, *a, **k):
        raise NotImplementedError(
            "SecretKeeper implements only the single-value keeper surface; "
            "IoSet/IoDup/ordinal (On*) methods are unused by the keeper "
            "(group-multisig smids/rmids only).")

    # IoSet (CatCesrIoSetSuber for smids/rmids — group multisig)
    putIoSetVals = pinIoSetVals = addIoSetVal = _unsupported
    getIoSetItemIter = getIoSetLastItem = remIoSet = remIoSetVal = _unsupported
    cntIoSet = getTopIoSetItemIter = _unsupported
    getIoSetLastItemIterAll = getIoSetLastIterAll = _unsupported
    # Ordinal (On*) — not part of the keeper schema at all
    putOnVal = pinOnVal = appendOnVal = getOnItem = getOnVal = _unsupported
    remOn = remOnAll = cntOnAll = getOnTopItemIter = getOnAllItemIter = _unsupported
