# -*- encoding: utf-8 -*-
"""
keri.db.dynamodbing module

DynamoDB-backed DBer implementing the same interface as LMDBer and WebDBer.
Enables keripy to run in serverless environments (AWS Lambda) by replacing
local LMDB storage with DynamoDB.

Requires boto3: install with `pip install keri[dynamodb]`
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Union

import boto3
from boto3.dynamodb.conditions import Key, Attr
from botocore.exceptions import ClientError

# Replicate key-composition utilities here to avoid importing dbing
# (which pulls in lmdb). Same approach as webdbing.py.

MaxON = int("f" * 32, 16)  # max ordinal number


def onKey(top, on, *, sep=b'.'):
    """Returns onkey (bytes): top + sep + on as 32-hex-padded int."""
    if hasattr(top, "encode"):
        top = top.encode("utf-8")
    return (b'%s%s%032x' % (top, sep, on))


def splitKey(key, sep=b'.'):
    """Right-split key at sep into (pre, suffix) pair."""
    if isinstance(key, memoryview):
        key = bytes(key)
    if hasattr(key, "encode"):
        if hasattr(sep, 'decode'):
            sep = sep.decode("utf-8")
    else:
        if hasattr(sep, 'encode'):
            sep = sep.encode("utf-8")
    splits = key.rsplit(sep, 1)
    if len(splits) != 2:
        raise ValueError(f"Unsplittable key: {key!r}")
    return tuple(splits)


def splitOnKey(key, *, sep=b'.'):
    """Right-split key and convert trailing ordinal to int."""
    top, on = splitKey(key, sep=sep)
    on = int(on, 16)
    return (top, on)


def suffix(key, ion, *, sep=b'.'):
    """Append ion as 32-hex suffix: key + sep + ion."""
    if hasattr(key, "encode"):
        key = key.encode("utf-8")
    if hasattr(sep, "encode"):
        sep = sep.encode("utf-8")
    return sep.join((key, b"%032x" % ion))


def unsuffix(iokey, *, sep=b'.'):
    """Strip 32-hex suffix: iokey -> (key, ion_int)."""
    if isinstance(iokey, memoryview):
        iokey = bytes(iokey)
    if hasattr(iokey, "encode"):
        if hasattr(sep, "decode"):
            sep = sep.decode("utf-8")
    else:
        if hasattr(sep, "encode"):
            sep = sep.encode("utf-8")
    key, ion = iokey.rsplit(sep, maxsplit=1)
    ion = int(ion, 16)
    return (key, ion)


# ---- Hex encoding helpers for DynamoDB string keys ----

def _hex(val: bytes) -> str:
    """Encode bytes to hex string for use as DynamoDB key attribute."""
    if isinstance(val, memoryview):
        val = bytes(val)
    if isinstance(val, str):
        val = val.encode("utf-8")
    return val.hex()


def _unhex(val: str) -> bytes:
    """Decode hex string back to bytes."""
    return bytes.fromhex(val)


def _bsep(sep):
    """Ensure separator is bytes."""
    if isinstance(sep, str):
        return sep.encode("utf-8")
    return sep


def _bcat(key: bytes, sep) -> bytes:
    """Concatenate key + sep, ensuring both are bytes."""
    if isinstance(key, str):
        key = key.encode("utf-8")
    if isinstance(sep, str):
        sep = sep.encode("utf-8")
    return key + sep


# ---- DynamoDB sort key prefixes by access pattern ----

_SK_SINGLE = "V"           # Single value per key
_SK_ON_PREFIX = "ON#"      # Ordinal-keyed
_SK_IO_PREFIX = "IO#"      # Insertion-ordered set
_SK_ONIO_PREFIX = "ONIO#"  # On + IoSet combined
_SK_META = "META"          # Metadata entries


# ---- Table/index names ----

_GSI_NAME = "subdb-index"
_GSI_PK = "gsi_pk"   # subdb name only
_GSI_SK = "gsi_sk"   # hex(full_original_key)

_APPEND_MAX_RETRY = 64   # ordinal-collision retry ceiling; exceeding it signals a real
                         # anomaly (hot-key storm / bug), not normal contention

# Stores that must NEVER be routed into a shared namespace via `shared_stores`,
# regardless of caller. These hold CONFIDENTIAL data — ACDC credential bodies
# (creds./cmse./ccrd.) and TEL events (tvts./tels.) — that lives only in a Reger.
# The shared-KEL oracle pools the PUBLIC key-event/key-state stores; pooling any
# of these would leak private data across the trust domain. `__init__` rejects a
# `shared_stores` naming one of them so a future Reger open that mis-copies the
# witness/Service-AID sharing args fails loudly instead of silently leaking.
# (keri.app.lambding.SHARED_KEL_STORES is asserted disjoint from this set.)
NEVER_SHARE_STORES = frozenset({"creds.", "cmse.", "ccrd.", "tvts.", "tels."})


@dataclass
class DynamoSubDb:
    """
    One declared DynamoDB-backed subdb.

    Mirrors webdbing.SubDb but without in-memory items or PyScript handle.
    """
    name: str
    table_name: str
    dupsort: bool = False
    flags_persisted: bool = False
    opened: bool = False

    def flags(self) -> dict[str, bool]:
        """Return subdb flags used by upstream wrapper tests."""
        return {"dupsort": self.dupsort}


class DynamoEnv:
    """Minimal named-subdb opener used by upstream wrappers (subing.py)."""

    def __init__(self, owner: "DynamoDBer"):
        self.owner = owner

    def open_db(self, key: bytes | str, dupsort: bool = False) -> DynamoSubDb:
        """
        Open a preconfigured named subdb handle.

        Parameters:
            key: Subdb name as bytes or UTF-8 text.
            dupsort: Requested duplicate flag.

        Returns:
            The DynamoSubDb handle for the requested store.

        Raises:
            KeyError: If the store was not declared when the DBer was opened.
        """
        name = self.owner._storify(key)
        if name not in self.owner._stores:
            raise KeyError(f"Store not configured in DynamoDBer: {name}")
        subdb = self.owner._stores[name]
        if not subdb.opened:
            if not subdb.flags_persisted:
                subdb.dupsort = bool(dupsort)
                subdb.flags_persisted = True
                # Persist dupsort metadata to DynamoDB
                self.owner._put_meta(subdb, {"dupsort": subdb.dupsort})
            subdb.opened = True
        return subdb


class DynamoDBer:
    """
    DynamoDB-backed DBer.

    Implements the same method interface as LMDBer and WebDBer, enabling
    subing.py / koming.py / basing.py wrappers to work unchanged.

    All reads use ConsistentRead=True. All writes are immediate (no flush).
    """

    def __init__(self, *, name: str, stores: dict[str, DynamoSubDb],
                 table_name: str, client, table, namespace: str | None = None,
                 shared_namespace: str | None = None,
                 shared_stores=None):
        self.name = name
        # Every key is namespaced — there is no bare/legacy format. When no
        # explicit namespace is given, the instance name IS the namespace, so a
        # single-tenant store-set (witness, mailbox, lambding) is self-isolated
        # with zero ceremony. Pooling DISTINCT store-sets into one physical
        # table (e.g. a Service AID's baser vs reger, which share a `stts.`
        # store) REQUIRES distinct explicit namespaces — the same name/namespace
        # would collide. Old single-tenant tables written before namespacing use
        # the bare `{subdb}#{key}` format and are NOT readable here: redeploy
        # against fresh tables (destroy-and-replace, not in-place migration).
        namespace = namespace if namespace else name
        if "#" in namespace:
            raise ValueError(f"namespace may not contain '#': {namespace!r}")
        self.namespace = namespace
        # Per-store routing: stores in `shared_stores` are keyed under
        # `shared_namespace` instead of `namespace`, so the public KEL/receipt/
        # key-state stores pool into one shared namespace (the key-state oracle)
        # while node-private stores stay per-service. Both off ⇒ unchanged.
        if shared_namespace and "#" in shared_namespace:
            raise ValueError(f"shared_namespace may not contain '#': {shared_namespace!r}")
        self._shared_namespace = shared_namespace
        self._shared_stores = frozenset(shared_stores or ())
        # Defense-in-depth: confidential stores (credential bodies / TEL events)
        # must never be pooled into a shared namespace. Fail loudly if a caller
        # mis-applies shared_stores to one (e.g. a future Reger open).
        _leaked = self._shared_stores & NEVER_SHARE_STORES
        if _leaked:
            raise ValueError(
                f"shared_stores may not include confidential store(s) {sorted(_leaked)} "
                "— credential bodies / TEL events must never be pooled into a shared namespace")
        self.env = DynamoEnv(self)
        self._stores = stores
        self.stores = list(stores)
        self._version = None
        self._table_name = table_name
        self._client = client   # boto3 low-level client (for batch ops)
        self._table = table     # boto3 Table resource (for simple ops)
        self.opened = True
        self.temp = False
        self.readonly = False
        self.path = f"dynamodb://{table_name}"

    @classmethod
    def open(
        cls,
        name: str,
        stores: list[str],
        *,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        table_name: str | None = None,
        clear: bool = False,
        session: "boto3.Session | None" = None,
        namespace: str | None = None,
        shared_namespace: str | None = None,
        shared_stores=None,
    ) -> "DynamoDBer":
        """
        Open a DynamoDB-backed DynamoDBer instance.

        Parameters:
            name: Base namespace for this database instance.
            stores: Declared subdb names available through env.open_db.
            region: AWS region for DynamoDB.
            endpoint_url: Override endpoint (for DynamoDB Local / moto).
            table_name: DynamoDB table name. Defaults to 'keri-{name}'.
            clear: When True, delete all items in the table for these stores.
            session: Optional boto3.Session for explicit credential control.

        Returns:
            A DynamoDBer ready for sync CRUD operations.
        """
        if table_name is None:
            table_name = f"keri-{name}"

        # Create boto3 resources
        kwargs = {"region_name": region}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url

        src = session if session is not None else boto3
        client = src.client("dynamodb", **kwargs)
        resource = src.resource("dynamodb", **kwargs)

        # Ensure table exists
        cls._ensure_table(client, table_name)
        table = resource.Table(table_name)

        # Build store handles
        opened: dict[str, DynamoSubDb] = {}
        all_store_names = [cls._storify(store) for store in stores]
        meta_store = "__meta__"
        if meta_store not in all_store_names:
            all_store_names.append(meta_store)

        for store_name in all_store_names:
            opened[store_name] = DynamoSubDb(
                name=store_name,
                table_name=table_name,
            )

        dber = cls(name=name, stores=opened, table_name=table_name,
                   client=client, table=table, namespace=namespace,
                   shared_namespace=shared_namespace, shared_stores=shared_stores)

        if clear:
            for store_name in all_store_names:
                dber._clear_store(store_name)

        # Load metadata (dupsort flags) for each store
        for store_name, subdb in opened.items():
            meta = dber._get_meta(subdb)
            if meta and "dupsort" in meta:
                subdb.dupsort = bool(meta["dupsort"])
                subdb.flags_persisted = True

        return dber

    @classmethod
    def _ensure_table(cls, client, table_name: str):
        """Create the DynamoDB table if it doesn't exist."""
        try:
            client.describe_table(TableName=table_name)
            return  # Already exists
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":
                raise

        client.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": _GSI_PK, "AttributeType": "S"},
                {"AttributeName": _GSI_SK, "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": _GSI_NAME,
                    "KeySchema": [
                        {"AttributeName": _GSI_PK, "KeyType": "HASH"},
                        {"AttributeName": _GSI_SK, "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Wait for table to become active
        waiter = client.get_waiter("table_exists")
        waiter.wait(TableName=table_name)

    @staticmethod
    def _storify(key: bytes | str) -> str:
        if isinstance(key, str):
            return key
        if isinstance(key, bytes):
            return key.decode("utf-8")
        raise TypeError(f"Unsupported store handle type: {type(key)}")

    # ---- Internal DynamoDB helpers ----

    def _nskey(self, name: str) -> str:
        """Prefix a store/meta name with its namespace. Stores listed in
        shared_stores route to shared_namespace (the pooled key-state oracle);
        all others use this instance's per-service namespace."""
        ns = (self._shared_namespace
              if self._shared_namespace and name in self._shared_stores
              else self.namespace)
        return f"{ns}#{name}"

    def _pk(self, db: DynamoSubDb, key: bytes) -> str:
        """Form the partition key: namespace#subdb_name#hex(key)."""
        return f"{self._nskey(db.name)}#{_hex(key)}"

    def _gsi_pk(self, db: DynamoSubDb) -> str:
        """GSI partition key is namespace#subdb_name."""
        return self._nskey(db.name)

    def _gsi_sk(self, key: bytes) -> str:
        """GSI sort key is the hex-encoded full key."""
        return _hex(key)

    def _put_item(self, db: DynamoSubDb, key: bytes, sk: str, val: bytes,
                  *, condition: str | None = None,
                  gsi_sk: str | None = None):
        """Low-level put_item with optional condition expression."""
        item = {
            "PK": self._pk(db, key),
            "SK": sk,
            "val": val,
            _GSI_PK: self._gsi_pk(db),
            _GSI_SK: gsi_sk if gsi_sk is not None else _hex(key),
        }
        kwargs = {"Item": item}
        if condition:
            kwargs["ConditionExpression"] = condition
        try:
            self._table.put_item(**kwargs)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def _get_item(self, db: DynamoSubDb, key: bytes, sk: str) -> bytes | None:
        """Low-level get_item with consistent read."""
        resp = self._table.get_item(
            Key={"PK": self._pk(db, key), "SK": sk},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        if item is None:
            return None
        val = item.get("val")
        if val is None:
            return None
        if isinstance(val, memoryview):
            return bytes(val)
        return bytes(val)

    def _delete_item(self, db: DynamoSubDb, key: bytes, sk: str) -> bool:
        """Low-level delete_item. Returns True if item existed."""
        # Check existence first since moto's ReturnValues behavior can differ
        if self._get_item(db, key, sk) is None:
            return False
        self._table.delete_item(
            Key={"PK": self._pk(db, key), "SK": sk},
        )
        return True

    def _query_pk(self, db: DynamoSubDb, key: bytes, *,
                  sk_prefix: str | None = None,
                  sk_begins: str | None = None,
                  sk_gte: str | None = None,
                  forward: bool = True,
                  limit: int | None = None) -> list[dict]:
        """Query items under a partition key with optional SK conditions."""
        pk = self._pk(db, key)
        kce = Key("PK").eq(pk)
        if sk_begins is not None:
            kce = kce & Key("SK").begins_with(sk_begins)
        elif sk_gte is not None:
            kce = kce & Key("SK").gte(sk_gte)

        kwargs = {
            "KeyConditionExpression": kce,
            "ConsistentRead": True,
            "ScanIndexForward": forward,
        }
        if limit:
            kwargs["Limit"] = limit

        items = []
        while True:
            resp = self._table.query(**kwargs)
            items.extend(resp.get("Items", []))
            if limit and len(items) >= limit:
                items = items[:limit]
                break
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek

        return items

    def _query_gsi(self, db: DynamoSubDb, *,
                   sk_prefix: str | None = None,
                   forward: bool = True) -> list[dict]:
        """Query the GSI for cross-key prefix iteration."""
        kce = Key(_GSI_PK).eq(self._gsi_pk(db))
        if sk_prefix:
            kce = kce & Key(_GSI_SK).begins_with(sk_prefix)

        kwargs = {
            "IndexName": _GSI_NAME,
            "KeyConditionExpression": kce,
            "ScanIndexForward": forward,
        }

        items = []
        while True:
            resp = self._table.query(**kwargs)
            items.extend(resp.get("Items", []))
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek

        return items

    def _batch_delete(self, keys: list[dict]):
        """Batch delete items by their PK/SK pairs.
        Uses Table resource batch_writer for automatic batching."""
        with self._table.batch_writer() as batch:
            for key in keys:
                batch.delete_item(Key=key)

    def _put_meta(self, db: DynamoSubDb, meta: dict):
        """Store metadata for a subdb."""
        import json
        self._table.put_item(Item={
            "PK": f"__meta__#{self._nskey(db.name)}",
            "SK": _SK_META,
            "val": json.dumps(meta).encode("utf-8"),
            _GSI_PK: "__meta__",
            _GSI_SK: self._nskey(db.name),
        })

    def _get_meta(self, db: DynamoSubDb) -> dict | None:
        """Read metadata for a subdb."""
        import json
        resp = self._table.get_item(
            Key={"PK": f"__meta__#{self._nskey(db.name)}", "SK": _SK_META},
            ConsistentRead=True,
        )
        item = resp.get("Item")
        if not item or "val" not in item:
            return None
        raw = item["val"]
        if isinstance(raw, (bytes, memoryview)):
            raw = bytes(raw).decode("utf-8")
        elif not isinstance(raw, str):
            raw = bytes(raw).decode("utf-8")  # handles Binary wrapper
        return json.loads(raw)

    def _clear_store(self, store_name: str):
        """Delete all items belonging to a store (within this namespace)."""
        # Query GSI for all items in this store
        kce = Key(_GSI_PK).eq(self._nskey(store_name))
        kwargs = {
            "IndexName": _GSI_NAME,
            "KeyConditionExpression": kce,
        }
        keys_to_delete = []
        while True:
            resp = self._table.query(**kwargs)
            for item in resp.get("Items", []):
                keys_to_delete.append({"PK": item["PK"], "SK": item["SK"]})
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek

        # Also delete the meta entry (deduplicate in case GSI already included it)
        meta_key = {"PK": f"__meta__#{self._nskey(store_name)}", "SK": _SK_META}
        seen = {(k["PK"], k["SK"]) for k in keys_to_delete}
        if (meta_key["PK"], meta_key["SK"]) not in seen:
            keys_to_delete.append(meta_key)

        if keys_to_delete:
            self._batch_delete(keys_to_delete)

    # ---- Lifecycle ----

    def flush(self) -> int:
        """No-op for DynamoDB -- writes are immediate. Returns 0."""
        return 0

    @property
    def version(self):
        """Return the database version string, or None if not set."""
        if self._version is None:
            self._version = self.getVer()
        return self._version

    @version.setter
    def version(self, val):
        """Set the database version string."""
        if hasattr(val, "decode"):
            val = val.decode("utf-8")
        self._version = val
        self.setVer(self._version)

    def getVer(self):
        """Read the version string from the __meta__ store."""
        store_name = self._storify("__meta__")
        if store_name not in self._stores:
            return None
        meta = self._get_meta(self._stores[store_name])
        if meta and "version" in meta:
            return meta["version"]
        return None

    def setVer(self, val):
        """Write the version string to the __meta__ store."""
        if hasattr(val, "encode"):
            pass  # keep as str
        else:
            val = str(val)
        store_name = self._storify("__meta__")
        if store_name not in self._stores:
            return
        subdb = self._stores[store_name]
        meta = self._get_meta(subdb) or {}
        meta["version"] = val
        self._put_meta(subdb, meta)

    def close(self, clear=False):
        """Close the database, optionally clearing all store data."""
        if clear:
            for store_name in list(self._stores.keys()):
                self._clear_store(store_name)
        self._stores = {}
        self.stores = []
        self.opened = False

    # ---- Single-value CRUD ----

    def putVal(self, db: DynamoSubDb, key: bytes, val: bytes) -> bool:
        """
        Insert val at key without overwriting an existing value.

        Returns:
            True when inserted. False when key already exists.
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        return self._put_item(db, key, _SK_SINGLE, val,
                              condition="attribute_not_exists(PK)",
                              gsi_sk=_hex(key))

    def setVal(self, db: DynamoSubDb, key: bytes, val: bytes) -> bool:
        """
        Insert or overwrite val at key.

        Returns:
            True after write succeeds.
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        return self._put_item(db, key, _SK_SINGLE, val,
                              gsi_sk=_hex(key))

    def getVal(self, db: DynamoSubDb, key: bytes) -> bytes | None:
        """
        Return stored value at key, or None when missing.
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        return self._get_item(db, key, _SK_SINGLE)

    def remVal(self, db: DynamoSubDb, key: bytes) -> bool:
        """
        Remove the exact entry at key.

        Returns:
            True when removed. False when key is empty or missing.
        """
        if not key:
            return False
        return self._delete_item(db, key, _SK_SINGLE)

    delVal = remVal  # backwards compat alias

    # ---- Ordinal-keyed (On) operations ----

    def putOnVal(self, db: DynamoSubDb, key: bytes, on: int = 0,
                 val: bytes | None = None, *, sep: bytes = b".") -> bool:
        """Write val at onkey = key + sep + on. Does not overwrite."""
        if val is None:
            return False
        return self.putVal(db=db, key=onKey(key, on, sep=sep), val=val)

    def pinOnVal(self, db: DynamoSubDb, key: bytes, on: int = 0,
                 val: bytes | None = None, *, sep: bytes = b".") -> bool:
        """Replace value at onkey = key + sep + on."""
        if val is None or not key:
            return False
        return self.setVal(db=db, key=onKey(key, on, sep=sep), val=val)

    def appendOnVal(self, db: DynamoSubDb, key: bytes, val: bytes,
                    *, sep: bytes = b".") -> int:
        """
        Append val after the last onkey for key. Returns the new ordinal.
        """
        if not key or val is None:
            raise ValueError(f"Bad append parameter: {key=} or {val=}")

        # Find the max existing ordinal for this key by querying GSI
        # Keys in GSI are hex-encoded onKeys: hex(key + sep + on)
        # We need to find all entries whose original key starts with key+sep
        prefix_bytes = _bcat(key, sep)
        gsi_prefix = _hex(prefix_bytes)

        items = self._query_gsi(db, sk_prefix=gsi_prefix, forward=False)

        on = 0
        if items:
            # The first item (reverse order) has the largest key
            gsi_sk_val = items[0].get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                ckey, cn = splitOnKey(full_key, sep=sep)
                if ckey == key:
                    if cn >= MaxON:
                        raise ValueError(
                            f"Number part {cn=} for key part {key=} exceeds maximum size."
                        )
                    on = cn + 1
            except (ValueError, IndexError):
                on = 0

        # `on` is the starting estimate from the (eventually-consistent) GSI max. Land at
        # the first genuinely-free ordinal via strongly-consistent conditional puts,
        # advancing locally on collision — robust to GSI staleness and concurrent writers
        # (no append dropped or overwritten; arrival-order best-effort).
        for _ in range(_APPEND_MAX_RETRY):
            if on >= MaxON:
                raise ValueError(
                    f"Number part {on=} for key part {key=} exceeds maximum size.")
            if self.putVal(db=db, key=onKey(key, on, sep=sep), val=val):
                return on
            on += 1
        raise ValueError(
            f"Failed appending {val=} at {key=} after {_APPEND_MAX_RETRY} attempts "
            "(excessive contention).")

    def getOnItem(self, db: DynamoSubDb, key: bytes, on: int = 0,
                  *, sep: bytes = b".") -> tuple[bytes, int, bytes] | None:
        """Get item (key, on, val) at onkey."""
        if not key:
            return None
        if (val := self.getVal(db=db, key=onKey(key, on, sep=sep))) is None:
            return None
        return key, on, val

    def getOnVal(self, db: DynamoSubDb, key: bytes, on: int = 0,
                 *, sep: bytes = b".") -> bytes | None:
        """Get value at onkey = key + sep + on."""
        if not key:
            return None
        return self.getVal(db=db, key=onKey(key, on, sep=sep))

    def remOn(self, db: DynamoSubDb, key: bytes, on: int = 0,
              *, sep: bytes = b".") -> bool:
        """Remove entry at onkey = key + sep + on."""
        if not key:
            return False
        return self.remVal(db=db, key=onKey(key, on, sep=sep))

    def remOnAll(self, db: DynamoSubDb, key: bytes = b"", on: int = 0,
                 *, sep: bytes = b".") -> bool:
        """Remove all entries at key for on >= on. Empty key removes whole db."""
        if not key:
            return self.remTop(db=db, top=b"")

        # Find all onkeys matching this base key with on >= requested on
        start_key = onKey(key, on, sep=sep)
        gsi_prefix = _hex(_bcat(key, sep))
        items = self._query_gsi(db, sk_prefix=gsi_prefix, forward=True)

        keys_to_delete = []
        for item in items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                ckey, cn = splitOnKey(full_key, sep=sep)
                if ckey == key and cn >= on:
                    keys_to_delete.append({"PK": item["PK"], "SK": item["SK"]})
            except (ValueError, IndexError):
                continue

        if not keys_to_delete:
            return False

        self._batch_delete(keys_to_delete)
        return True

    def cntOnAll(self, db: DynamoSubDb, key: bytes = b"", on: int = 0,
                 *, sep: bytes = b".") -> int:
        """Count all entries for key with on >= on. Empty key counts whole db."""
        if not key:
            return self.cntAll(db)

        gsi_prefix = _hex(_bcat(key, sep))
        items = self._query_gsi(db, sk_prefix=gsi_prefix, forward=True)

        count = 0
        for item in items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                ckey, cn = splitOnKey(full_key, sep=sep)
                if ckey == key and cn >= on:
                    count += 1
            except (ValueError, IndexError):
                continue

        return count

    # ---- Top-level iteration / management ----

    def getTopItemIter(self, db: DynamoSubDb,
                       top: bytes = b"") -> Iterator[tuple[bytes, bytes]]:
        """Iterate over (key, val) pairs whose keys start with top."""
        prefix = _hex(top) if top else None
        items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        for item in items:
            gsi_sk_val = item.get(_GSI_SK, "")
            full_key = _unhex(gsi_sk_val)
            val = item.get("val")
            if val is not None:
                if isinstance(val, memoryview):
                    val = bytes(val)
                else:
                    val = bytes(val)
                yield full_key, val

    def getOnTopItemIter(self, db: DynamoSubDb, top: bytes = b"",
                         *, sep: bytes = b".") -> Iterator[tuple[bytes, int, bytes]]:
        """Iterate over top branch yielding (key, on, val) triples."""
        for okey, val in self.getTopItemIter(db=db, top=top):
            key, on = splitOnKey(okey, sep=sep)
            yield key, on, val

    def getOnAllItemIter(self, db: DynamoSubDb, key: bytes = b"",
                         on: int = 0, *,
                         sep: bytes = b".") -> Iterator[tuple[bytes, int, bytes]]:
        """
        Iterate (key, on, val) triples for on >= on at key.
        Empty key iterates whole db.
        """
        if not key:
            # Whole db iteration via GSI
            items = self._query_gsi(db, forward=True)
            for item in items:
                gsi_sk_val = item.get(_GSI_SK, "")
                try:
                    full_key = _unhex(gsi_sk_val)
                    ckey, cn = splitOnKey(full_key, sep=sep)
                    val = item.get("val")
                    if val is not None:
                        if isinstance(val, memoryview):
                            val = bytes(val)
                        else:
                            val = bytes(val)
                        yield ckey, cn, val
                except (ValueError, IndexError):
                    continue
        else:
            gsi_prefix = _hex(_bcat(key, sep))
            items = self._query_gsi(db, sk_prefix=gsi_prefix, forward=True)

            for item in items:
                gsi_sk_val = item.get(_GSI_SK, "")
                try:
                    full_key = _unhex(gsi_sk_val)
                    ckey, cn = splitOnKey(full_key, sep=sep)
                    if ckey == key and cn >= on:
                        val = item.get("val")
                        if val is not None:
                            if isinstance(val, memoryview):
                                val = bytes(val)
                            else:
                                val = bytes(val)
                            yield ckey, cn, val
                except (ValueError, IndexError):
                    continue

    def remTop(self, db: DynamoSubDb, top: bytes = b"") -> bool:
        """Remove all entries whose keys start with top."""
        prefix = _hex(top) if top else None
        items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        keys_to_delete = [{"PK": item["PK"], "SK": item["SK"]} for item in items]
        if not keys_to_delete:
            return False

        self._batch_delete(keys_to_delete)
        return True

    delTop = remTop  # backwards compat alias

    def cntTop(self, db: DynamoSubDb, top: bytes = b"") -> int:
        """Count all entries whose keys start with top."""
        prefix = _hex(top) if top else None
        items = self._query_gsi(db, sk_prefix=prefix, forward=True)
        return len(items)

    def cntAll(self, db: DynamoSubDb) -> int:
        """Count all values stored in db."""
        return self.cntTop(db, top=b"")

    # ---- IoSet methods (Phase 2) ----

    def putIoSetVals(self, db: DynamoSubDb, key: bytes, vals,
                     *, sep: bytes = b".") -> bool:
        """Add each val in vals to insertion-ordered set at key.
        Only adds vals not already in the set. Uses hidden ordinal suffix."""
        if not key or vals is None:
            return False

        existing = list(self._get_ioset_items(db, key, sep=sep))
        existing_vals = {v for _, v in existing}

        max_ion = -1
        for _, v in existing:
            pass  # We need the ion values
        # Re-query to get ions
        existing_with_ions = list(self._get_ioset_raw(db, key, sep=sep))
        if existing_with_ions:
            max_ion = max(ion for ion, _ in existing_with_ions)

        added = False
        for val in vals:
            if isinstance(val, memoryview):
                val = bytes(val)
            if val in existing_vals:
                continue
            max_ion += 1
            iokey = suffix(key, max_ion, sep=sep)
            self._put_item(db, iokey, _SK_SINGLE, val, gsi_sk=_hex(iokey))
            existing_vals.add(val)
            added = True

        return added

    def pinIoSetVals(self, db: DynamoSubDb, key: bytes, vals,
                     *, sep: bytes = b".") -> bool:
        """Replace all values at key with vals. Removes old, writes new."""
        if not key or vals is None:
            return False

        self.remIoSet(db=db, key=key, sep=sep)

        ion = 0
        for val in vals:
            if isinstance(val, memoryview):
                val = bytes(val)
            iokey = suffix(key, ion, sep=sep)
            self._put_item(db, iokey, _SK_SINGLE, val, gsi_sk=_hex(iokey))
            ion += 1

        return True

    def addIoSetVal(self, db: DynamoSubDb, key: bytes, val: bytes,
                    *, sep: bytes = b".") -> bool:
        """Add val to insertion-ordered set at key if not already present."""
        if not key or val is None:
            return False

        if isinstance(val, memoryview):
            val = bytes(val)

        # Check for duplicate
        existing = list(self._get_ioset_items(db, key, sep=sep))
        for _, v in existing:
            if v == val:
                return False

        # Find max ion
        existing_raw = list(self._get_ioset_raw(db, key, sep=sep))
        max_ion = max((ion for ion, _ in existing_raw), default=-1)

        # Land at the first free ion via strongly-consistent conditional puts, advancing
        # locally on collision — no silent overwrite under concurrent writers / GSI lag.
        # (Cross-writer dedup stays best-effort; perfect dedup would need a transaction.)
        ion = max_ion + 1
        for _ in range(_APPEND_MAX_RETRY):
            iokey = suffix(key, ion, sep=sep)
            if self._put_item(db, iokey, _SK_SINGLE, val, gsi_sk=_hex(iokey),
                              condition="attribute_not_exists(PK)"):
                return True
            ion += 1
        raise ValueError(
            f"Failed adding IoSet val at {key=} after {_APPEND_MAX_RETRY} attempts "
            "(excessive contention).")

    def getIoSetItemIter(self, db: DynamoSubDb, key: bytes,
                         *, ion: int = 0,
                         sep: bytes = b".") -> Iterator[tuple[bytes, bytes]]:
        """Iterate (key, val) pairs in insertion order at key."""
        if not key:
            return

        for i, v in self._get_ioset_raw(db, key, sep=sep):
            if i >= ion:
                yield key, v

    def getIoSetLastItem(self, db: DynamoSubDb, key: bytes,
                         *, sep: bytes = b".") -> tuple[bytes, bytes] | tuple:
        """Get last (key, val) in the IoSet at key."""
        if not key:
            return ()

        items = list(self._get_ioset_raw(db, key, sep=sep))
        if not items:
            return ()

        _, val = items[-1]
        return key, val

    def remIoSet(self, db: DynamoSubDb, key: bytes,
                 *, sep: bytes = b".") -> bool:
        """Remove all entries in the IoSet at key."""
        if not key:
            return False

        prefix = _hex(_bcat(key, sep))
        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        # Also check for exact key match (ion=0 might use key itself)
        keys_to_delete = []
        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                base, _ = unsuffix(full_key, sep=sep)
                if base == key:
                    keys_to_delete.append({"PK": item["PK"], "SK": item["SK"]})
            except (ValueError, IndexError):
                continue

        if not keys_to_delete:
            return False

        self._batch_delete(keys_to_delete)
        return True

    def remIoSetVal(self, db: DynamoSubDb, key: bytes,
                    val: bytes | None = None,
                    *, sep: bytes = b".") -> bool:
        """Remove specific val from the IoSet at key."""
        if not key or val is None:
            return False

        if isinstance(val, memoryview):
            val = bytes(val)

        prefix = _hex(_bcat(key, sep))
        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                base, _ = unsuffix(full_key, sep=sep)
                if base == key:
                    item_val = item.get("val")
                    if item_val is not None:
                        if isinstance(item_val, memoryview):
                            item_val = bytes(item_val)
                        else:
                            item_val = bytes(item_val)
                        if item_val == val:
                            self._batch_delete([{"PK": item["PK"], "SK": item["SK"]}])
                            return True
            except (ValueError, IndexError):
                continue

        return False

    def cntIoSet(self, db: DynamoSubDb, key: bytes,
                 *, ion: int = 0, sep: bytes = b".") -> int:
        """Count entries in the IoSet at key with ion >= ion."""
        if not key:
            return 0

        count = 0
        for i, _ in self._get_ioset_raw(db, key, sep=sep):
            if i >= ion:
                count += 1
        return count

    def getTopIoSetItemIter(self, db: DynamoSubDb, top: bytes = b"",
                            *, sep: bytes = b".") -> Iterator[tuple[bytes, bytes]]:
        """Iterate over all IoSet entries whose base keys start with top."""
        prefix = _hex(top) if top else None
        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                base, _ = unsuffix(full_key, sep=sep)
                val = item.get("val")
                if val is not None:
                    if isinstance(val, memoryview):
                        val = bytes(val)
                    else:
                        val = bytes(val)
                    yield base, val
            except (ValueError, IndexError):
                continue

    def getIoSetLastItemIterAll(self, db: DynamoSubDb, key: bytes = b"",
                                *, sep: bytes = b".") -> Iterator[tuple[bytes, bytes]]:
        """Iterate yielding the last (key, val) of each IoSet group."""
        prefix = _hex(_bcat(key, sep)) if key else None
        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        # Group by base key, yield last val of each group
        current_base = None
        last_val = None
        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                base, _ = unsuffix(full_key, sep=sep)
                val = item.get("val")
                if val is not None:
                    if isinstance(val, memoryview):
                        val = bytes(val)
                    else:
                        val = bytes(val)

                    if current_base is None:
                        current_base = base
                        last_val = val
                    elif base != current_base:
                        yield current_base, last_val
                        current_base = base
                        last_val = val
                    else:
                        last_val = val
            except (ValueError, IndexError):
                continue

        if current_base is not None:
            yield current_base, last_val

    def getIoSetLastIterAll(self, db: DynamoSubDb, key: bytes = b"",
                            *, sep: bytes = b".") -> Iterator[bytes]:
        """Iterate yielding just the last val of each IoSet group."""
        for _, val in self.getIoSetLastItemIterAll(db, key=key, sep=sep):
            yield val

    # ---- IoSet internal helpers ----

    def _get_ioset_raw(self, db: DynamoSubDb, key: bytes,
                       *, sep: bytes = b".") -> list[tuple[int, bytes]]:
        """Get all (ion, val) pairs in the IoSet at key, sorted by ion."""
        prefix = _hex(_bcat(key, sep))
        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        results = []
        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                base, ion = unsuffix(full_key, sep=sep)
                if base == key:
                    val = item.get("val")
                    if val is not None:
                        if isinstance(val, memoryview):
                            val = bytes(val)
                        else:
                            val = bytes(val)
                        results.append((ion, val))
            except (ValueError, IndexError):
                continue

        results.sort(key=lambda x: x[0])
        return results

    def _get_ioset_items(self, db: DynamoSubDb, key: bytes,
                         *, sep: bytes = b".") -> list[tuple[int, bytes]]:
        """Alias for _get_ioset_raw."""
        return self._get_ioset_raw(db, key, sep=sep)

    # ---- OnIoSet methods (Phase 3) ----

    def putOnIoSetVals(self, db: DynamoSubDb, key: bytes, *, on: int = 0,
                       vals=None, sep: bytes = b".") -> bool:
        """Add each val in vals to insertion-ordered set at onkey."""
        if not key or vals is None:
            return False
        okey = onKey(key, on, sep=sep)
        return self.putIoSetVals(db=db, key=okey, vals=vals, sep=sep)

    def pinOnIoSetVals(self, db: DynamoSubDb, key: bytes, *, on: int = 0,
                       vals=None, sep: bytes = b".") -> bool:
        """Replace all values at onkey with vals."""
        if not key or vals is None:
            return False
        okey = onKey(key, on, sep=sep)
        return self.pinIoSetVals(db=db, key=okey, vals=vals, sep=sep)

    def appendOnIoSetVals(self, db: DynamoSubDb, key: bytes, vals,
                          *, sep: bytes = b".") -> int:
        """Append vals at next available ordinal for key. Returns new on."""
        if not key or vals is None:
            raise ValueError(f"Bad append parameter: {key=} or {vals=}")

        # Find max existing on for this key's IoSet entries
        # IoSet entries have keys like: key.on.ion
        # We need to find the max on
        prefix = _hex(_bcat(key, sep))
        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=False)

        on = 0
        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                # full_key is onKey(key, on) + sep + ion
                onpart, _ = unsuffix(full_key, sep=sep)  # strip ion
                ckey, cn = splitOnKey(onpart, sep=sep)    # split on
                if ckey == key:
                    if cn >= MaxON:
                        raise ValueError(
                            f"Number part {cn=} for key part {key=} exceeds maximum size."
                        )
                    on = cn + 1
                    break  # reverse order, first match is max
            except (ValueError, IndexError):
                continue

        okey = onKey(key, on, sep=sep)
        ion = 0
        for val in vals:
            if isinstance(val, memoryview):
                val = bytes(val)
            iokey = suffix(okey, ion, sep=sep)
            self._put_item(db, iokey, _SK_SINGLE, val, gsi_sk=_hex(iokey))
            ion += 1

        return on

    def addOnIoSetVal(self, db: DynamoSubDb, key: bytes, *, on: int = 0,
                      val: bytes | None = None,
                      sep: bytes = b".") -> bool:
        """Add val to IoSet at onkey if not already present."""
        if not key or val is None:
            return False
        okey = onKey(key, on, sep=sep)
        return self.addIoSetVal(db=db, key=okey, val=val, sep=sep)

    def getOnIoSetItemIter(self, db: DynamoSubDb, key: bytes, *,
                           on: int = 0, ion: int = 0,
                           sep: bytes = b".") -> Iterator[tuple[bytes, int, bytes]]:
        """Iterate (key, on, val) for IoSet at onkey."""
        if not key:
            return
        okey = onKey(key, on, sep=sep)
        for _, val in self.getIoSetItemIter(db=db, key=okey, ion=ion, sep=sep):
            yield key, on, val

    def getOnIoSetLastItem(self, db: DynamoSubDb, key: bytes, on: int = 0,
                           *, sep: bytes = b".") -> tuple[bytes, int, bytes] | tuple:
        """Get last item in the IoSet at onkey."""
        if not key:
            return ()
        okey = onKey(key, on, sep=sep)
        result = self.getIoSetLastItem(db=db, key=okey, sep=sep)
        if not result:
            return ()
        _, val = result
        return key, on, val

    def remOnIoSetVal(self, db: DynamoSubDb, key: bytes, *, on: int = 0,
                      val: bytes | None = None,
                      sep: bytes = b".") -> bool:
        """Remove specific val from IoSet at onkey."""
        if not key or val is None:
            return False
        okey = onKey(key, on, sep=sep)
        return self.remIoSetVal(db=db, key=okey, val=val, sep=sep)

    def remOnAllIoSet(self, db: DynamoSubDb, key: bytes = b"",
                      on: int = 0, *, sep: bytes = b".") -> bool:
        """Remove all IoSet entries at key for on >= on."""
        if not key:
            return self.remTop(db=db, top=b"")

        prefix = _hex(_bcat(key, sep))
        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        keys_to_delete = []
        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                onpart, _ = unsuffix(full_key, sep=sep)  # strip ion
                ckey, cn = splitOnKey(onpart, sep=sep)    # split on
                if ckey == key and cn >= on:
                    keys_to_delete.append({"PK": item["PK"], "SK": item["SK"]})
            except (ValueError, IndexError):
                continue

        if not keys_to_delete:
            return False

        self._batch_delete(keys_to_delete)
        return True

    def cntOnIoSet(self, db: DynamoSubDb, key: bytes, *, on: int = 0,
                   ion: int = 0, sep: bytes = b".") -> int:
        """Count entries in IoSet at onkey."""
        if not key:
            return 0
        okey = onKey(key, on, sep=sep)
        return self.cntIoSet(db=db, key=okey, ion=ion, sep=sep)

    def cntOnAllIoSet(self, db: DynamoSubDb, key: bytes = b"",
                      *, on: int = 0, sep: bytes = b".") -> int:
        """Count all IoSet entries at key for on >= on."""
        if not key:
            return self.cntAll(db)

        prefix = _hex(_bcat(key, sep))
        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        count = 0
        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                onpart, _ = unsuffix(full_key, sep=sep)
                ckey, cn = splitOnKey(onpart, sep=sep)
                if ckey == key and cn >= on:
                    count += 1
            except (ValueError, IndexError):
                continue

        return count

    def getOnTopIoSetItemIter(self, db: DynamoSubDb, top: bytes = b"",
                              *, sep: bytes = b".") -> Iterator[tuple[bytes, int, bytes]]:
        """Iterate (key, on, val) for all IoSet entries under top prefix."""
        prefix = _hex(top) if top else None
        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                onpart, _ = unsuffix(full_key, sep=sep)  # strip ion
                ckey, cn = splitOnKey(onpart, sep=sep)    # split on
                val = item.get("val")
                if val is not None:
                    if isinstance(val, memoryview):
                        val = bytes(val)
                    else:
                        val = bytes(val)
                    yield ckey, cn, val
            except (ValueError, IndexError):
                continue

    def getOnAllIoSetItemIter(self, db: DynamoSubDb, key: bytes = b"",
                              on: int = 0, *,
                              sep: bytes = b".") -> Iterator[tuple[bytes, int, bytes]]:
        """Iterate (key, on, val) for all on >= on at key."""
        if not key:
            yield from self.getOnTopIoSetItemIter(db=db, top=b"", sep=sep)
            return

        prefix = _hex(_bcat(key, sep))
        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                onpart, _ = unsuffix(full_key, sep=sep)
                ckey, cn = splitOnKey(onpart, sep=sep)
                if ckey == key and cn >= on:
                    val = item.get("val")
                    if val is not None:
                        if isinstance(val, memoryview):
                            val = bytes(val)
                        else:
                            val = bytes(val)
                        yield ckey, cn, val
            except (ValueError, IndexError):
                continue

    def getOnAllIoSetLastItemIter(self, db: DynamoSubDb, key: bytes = b"",
                                  on: int = 0, *,
                                  sep: bytes = b".") -> Iterator[tuple[bytes, int, bytes]]:
        """Iterate yielding last (key, on, val) of each on-group."""
        if not key:
            prefix = None
        else:
            prefix = _hex(_bcat(key, sep))

        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=True)

        current_on_key = None
        current_on = None
        last_val = None
        last_ckey = None

        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                onpart, _ = unsuffix(full_key, sep=sep)
                ckey, cn = splitOnKey(onpart, sep=sep)

                if key and (ckey != key or cn < on):
                    continue

                val = item.get("val")
                if val is not None:
                    if isinstance(val, memoryview):
                        val = bytes(val)
                    else:
                        val = bytes(val)

                    on_key = (ckey, cn)
                    if current_on_key is None:
                        current_on_key = on_key
                        current_on = cn
                        last_val = val
                        last_ckey = ckey
                    elif on_key != current_on_key:
                        yield last_ckey, current_on, last_val
                        current_on_key = on_key
                        current_on = cn
                        last_val = val
                        last_ckey = ckey
                    else:
                        last_val = val
            except (ValueError, IndexError):
                continue

        if current_on_key is not None:
            yield last_ckey, current_on, last_val

    def getOnAllIoSetItemBackIter(self, db: DynamoSubDb, key: bytes = b"",
                                  on: int | None = None, *,
                                  sep: bytes = b".") -> Iterator[tuple[bytes, int, bytes]]:
        """Iterate (key, on, val) backwards over all IoSet entries."""
        if not key:
            prefix = None
        else:
            prefix = _hex(_bcat(key, sep))

        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=False)

        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                onpart, _ = unsuffix(full_key, sep=sep)
                ckey, cn = splitOnKey(onpart, sep=sep)

                if key and ckey != key:
                    continue
                if on is not None and cn > on:
                    continue

                val = item.get("val")
                if val is not None:
                    if isinstance(val, memoryview):
                        val = bytes(val)
                    else:
                        val = bytes(val)
                    yield ckey, cn, val
            except (ValueError, IndexError):
                continue

    def getOnAllIoSetLastItemBackIter(self, db: DynamoSubDb, key: bytes = b"",
                                      on: int | None = None, *,
                                      sep: bytes = b".") -> Iterator[tuple[bytes, int, bytes]]:
        """Iterate backwards yielding last (key, on, val) of each on-group."""
        if not key:
            prefix = None
        else:
            prefix = _hex(_bcat(key, sep))

        gsi_items = self._query_gsi(db, sk_prefix=prefix, forward=False)

        current_on_key = None
        current_on = None
        last_val = None
        last_ckey = None

        for item in gsi_items:
            gsi_sk_val = item.get(_GSI_SK, "")
            try:
                full_key = _unhex(gsi_sk_val)
                onpart, _ = unsuffix(full_key, sep=sep)
                ckey, cn = splitOnKey(onpart, sep=sep)

                if key and ckey != key:
                    continue
                if on is not None and cn > on:
                    continue

                val = item.get("val")
                if val is not None:
                    if isinstance(val, memoryview):
                        val = bytes(val)
                    else:
                        val = bytes(val)

                    on_key = (ckey, cn)
                    if current_on_key is None:
                        current_on_key = on_key
                        current_on = cn
                        last_val = val
                        last_ckey = ckey
                    elif on_key != current_on_key:
                        yield last_ckey, current_on, last_val
                        current_on_key = on_key
                        current_on = cn
                        last_val = val
                        last_ckey = ckey
                    else:
                        last_val = val
            except (ValueError, IndexError):
                continue

        if current_on_key is not None:
            yield last_ckey, current_on, last_val

    # ---- Dup methods (Phase 4) ----
    # Map to IoSet internally since DynamoDB has no native dupsort

    def putVals(self, db: DynamoSubDb, key: bytes, vals,
                *, sep: bytes = b".") -> bool:
        """Add each val in vals as duplicate at key."""
        return self.putIoSetVals(db=db, key=key, vals=vals, sep=sep)

    def addVal(self, db: DynamoSubDb, key: bytes, val: bytes,
               *, sep: bytes = b".") -> bool:
        """Add val as duplicate at key if not already present."""
        return self.addIoSetVal(db=db, key=key, val=val, sep=sep)

    def getVals(self, db: DynamoSubDb, key: bytes,
                *, sep: bytes = b".") -> list[bytes]:
        """Get all duplicate values at key."""
        return [v for _, v in self.getIoSetItemIter(db=db, key=key, sep=sep)]

    def getValsIter(self, db: DynamoSubDb, key: bytes,
                    *, sep: bytes = b".") -> Iterator[bytes]:
        """Iterate over all duplicate values at key."""
        for _, v in self.getIoSetItemIter(db=db, key=key, sep=sep):
            yield v

    def cntVals(self, db: DynamoSubDb, key: bytes,
                *, sep: bytes = b".") -> int:
        """Count duplicate values at key."""
        return self.cntIoSet(db=db, key=key, sep=sep)

    def delVals(self, db: DynamoSubDb, key: bytes,
                *, sep: bytes = b".") -> bool:
        """Delete all duplicate values at key."""
        return self.remIoSet(db=db, key=key, sep=sep)

    # ---- IoDup methods ----
    # Map IoDup (LMDB dupsort with hidden proem) to IoSet equivalents

    def putIoDupVals(self, db, key, vals, *, sep=b'.'):
        return self.putIoSetVals(db=db, key=key, vals=vals, sep=sep)

    def addIoDupVal(self, db, key, val, *, sep=b'.'):
        return self.addIoSetVal(db=db, key=key, val=val, sep=sep)

    def getIoDupVals(self, db, key, *, sep=b'.'):
        return [v for _, v in self.getIoSetItemIter(db=db, key=key, sep=sep)]

    def getIoDupItemIter(self, db, key, *, ion=0, sep=b'.'):
        return self.getIoSetItemIter(db=db, key=key, ion=ion, sep=sep)

    def getIoDupValLast(self, db, key, *, sep=b'.'):
        result = self.getIoSetLastItem(db=db, key=key, sep=sep)
        if not result:
            return None
        return result[1]

    def delIoDupVals(self, db, key, *, sep=b'.'):
        return self.remIoSet(db=db, key=key, sep=sep)

    def delIoDupVal(self, db, key, val, *, sep=b'.'):
        return self.remIoSetVal(db=db, key=key, val=val, sep=sep)

    def cntIoDups(self, db, key, *, sep=b'.'):
        return self.cntIoSet(db=db, key=key, sep=sep)

    # ---- OnIoDup methods ----
    # Map OnIoDup (On + IoDup) to OnIoSet equivalents

    def putOnIoDupVals(self, db, key, on=0, vals=b'', *, sep=b'.'):
        return self.putOnIoSetVals(db=db, key=key, on=on, vals=vals, sep=sep)

    def addOnIoDupVal(self, db, key, on=0, val=b'', sep=b'.'):
        return self.addOnIoSetVal(db=db, key=key, on=on, val=val, sep=sep)

    def appendOnIoDupVal(self, db, key, val, *, sep=b'.'):
        return self.appendOnIoSetVals(db=db, key=key, vals=[val], sep=sep)

    def getOnIoDupVals(self, db, key, on=0, sep=b'.'):
        return [v for _, _, v in self.getOnIoSetItemIter(db=db, key=key, on=on, sep=sep)]

    def getOnIoDupItemIter(self, db, key, on=0, ion=0, sep=b'.'):
        return self.getOnIoSetItemIter(db=db, key=key, on=on, ion=ion, sep=sep)

    def getOnIoDupLast(self, db, key, on=0, *, sep=b'.'):
        result = self.getOnIoSetLastItem(db=db, key=key, on=on, sep=sep)
        if not result:
            return None
        return result[2]  # val from (key, on, val)

    def getOnIoDupLastValIter(self, db, key=b'', on=0, *, sep=b'.'):
        for _, _, val in self.getOnAllIoSetLastItemIter(db=db, key=key, on=on, sep=sep):
            yield val

    def getOnIoDupLastItemIter(self, db, key=b'', on=0, *, sep=b'.'):
        return self.getOnAllIoSetLastItemIter(db=db, key=key, on=on, sep=sep)

    def delOnIoDups(self, db, key, on=0, sep=b'.'):
        okey = onKey(key, on, sep=sep)
        return self.remIoSet(db=db, key=okey, sep=sep)

    def delOnIoDupVal(self, db, key, on=0, val=b'', sep=b'.'):
        return self.remOnIoSetVal(db=db, key=key, on=on, val=val, sep=sep)

    def cntOnIoDups(self, db, key, on=0, sep=b'.'):
        return self.cntOnIoSet(db=db, key=key, on=on, sep=sep)

    def getOnIoDupValBackIter(self, db, key=b'', on=0, *, sep=b'.'):
        for _, _, val in self.getOnAllIoSetItemBackIter(db=db, key=key, on=on, sep=sep):
            yield val

    def getOnIoDupItemBackIter(self, db, key=b'', on=0, *, sep=b'.'):
        return self.getOnAllIoSetItemBackIter(db=db, key=key, on=on, sep=sep)

    def getOnIoDupItemIterAll(self, db, key=b'', on=0, *, sep=b'.'):
        return self.getOnAllIoSetItemIter(db=db, key=key, on=on, sep=sep)

    def getOnIoDupIterAll(self, db, key=b'', on=0, *, sep=b'.'):
        for _, _, val in self.getOnAllIoSetItemIter(db=db, key=key, on=on, sep=sep):
            yield val


# ---- Context manager factory ----

@contextmanager
def openDynamoDB(*, cls=None, name="test", temp=True, **kwa):
    """Context manager for DynamoDBer instances.

    Parameters:
        cls: Class to instantiate. Defaults to DynamoDBer.
        name: Database instance name.
        temp: If True, clear data on close.
        **kwa: Passed to cls.open().
    """
    if cls is None:
        cls = DynamoDBer
    dber = None
    try:
        stores = kwa.pop("stores", [])
        dber = cls.open(name=name, stores=stores, **kwa)
        yield dber
    finally:
        if dber:
            dber.close(clear=temp)
