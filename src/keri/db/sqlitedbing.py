# -*- encoding: utf-8 -*-
"""
keri.db.sqlitedbing module

SQLite-backed DBer implementing the same interface as LMDBer, WebDBer, and
DynamoDBer.  Enables keripy to run on any platform with Python's built-in
sqlite3 module — no external dependencies required.

Uses WAL journal mode for concurrent read/write performance and a single
``keri_store`` table with (subdb, key, sort) composite primary key to
emulate LMDB named sub-databases with optional duplicate sort support.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

# ---------------------------------------------------------------------------
# Replicate key-composition utilities here to avoid importing dbing
# (which pulls in lmdb).  Same approach as dynamodbing.py / webdbing.py.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Sort key prefixes by access pattern (bytes, not strings — SQLite stores
# key and sort columns as BLOB).
# ---------------------------------------------------------------------------

_SK_SINGLE = b"V"            # Single value per key
_SK_ON_PREFIX = b"ON#"       # Ordinal-keyed
_SK_IO_PREFIX = b"IO#"       # Insertion-ordered set
_SK_ONIO_PREFIX = b"ONIO#"   # On + IoSet combined
_SK_META = b"META"           # Metadata entries


# ---------------------------------------------------------------------------
# SQLiteSubDb — one declared sub-database handle
# ---------------------------------------------------------------------------

@dataclass
class SQLiteSubDb:
    """
    One declared SQLite-backed subdb.

    Mirrors DynamoSubDb / webdbing.SubDb but without any cloud state.
    """
    name: str
    dupsort: bool = False
    flags_persisted: bool = False
    opened: bool = False

    def flags(self) -> dict[str, bool]:
        """Return subdb flags used by upstream wrapper tests."""
        return {"dupsort": self.dupsort}


# ---------------------------------------------------------------------------
# SQLiteEnv — minimal named-subdb opener used by upstream wrappers
# ---------------------------------------------------------------------------

class SQLiteEnv:
    """Minimal named-subdb opener used by upstream wrappers (subing.py)."""

    def __init__(self, owner: "SQLiteDBer"):
        self.owner = owner

    def open_db(self, key: bytes | str, dupsort: bool = False) -> SQLiteSubDb:
        """
        Open a preconfigured named subdb handle.

        Parameters:
            key: Subdb name as bytes or UTF-8 text.
            dupsort: Requested duplicate flag.

        Returns:
            The SQLiteSubDb handle for the requested store.

        Raises:
            KeyError: If the store was not declared when the DBer was opened.
        """
        name = self.owner._storify(key)
        if name not in self.owner._stores:
            raise KeyError(f"Store not configured in SQLiteDBer: {name}")
        subdb = self.owner._stores[name]
        if not subdb.opened:
            if not subdb.flags_persisted:
                subdb.dupsort = bool(dupsort)
                subdb.flags_persisted = True
                # Persist dupsort metadata to SQLite
                self.owner._put_meta(subdb, {"dupsort": subdb.dupsort})
            subdb.opened = True
        return subdb


# ---------------------------------------------------------------------------
# SQLiteDBer — the main database class
# ---------------------------------------------------------------------------

class SQLiteDBer:
    """
    SQLite-backed DBer.

    Implements the same method interface as LMDBer, WebDBer, and DynamoDBer,
    enabling subing.py / koming.py / basing.py wrappers to work unchanged.

    Uses WAL journal mode for concurrent readers.  All writes go through a
    single sqlite3.Connection (auto-commit disabled; explicit commits via
    flush()).
    """

    def __init__(self, *, name: str, stores: dict[str, SQLiteSubDb],
                 conn: sqlite3.Connection, path: str):
        self.name = name
        self.env = SQLiteEnv(self)
        self._stores = stores
        self.stores = list(stores)
        self._version = None
        self._conn = conn
        self.opened = True
        self.temp = False
        self.readonly = False
        self.path = path

    # ---- Factory classmethod ----

    @classmethod
    def open(
        cls,
        name: str,
        stores: list[str],
        *,
        path: str = "",
        clear: bool = False,
    ) -> "SQLiteDBer":
        """
        Open a SQLite-backed SQLiteDBer instance.

        Parameters:
            name: Base namespace for this database instance.
            stores: Declared subdb names available through env.open_db.
            path: Filesystem path for the SQLite database file.
                  Defaults to ':memory:' when empty.
            clear: When True, delete all rows for these stores.

        Returns:
            A SQLiteDBer ready for CRUD operations.
        """
        if not path:
            path = ":memory:"

        conn = sqlite3.connect(path, isolation_level="DEFERRED",
                               check_same_thread=False)

        # WAL mode only works for file-backed databases
        if path and path != ":memory:":
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

        cls._ensure_schema(conn)

        # Build store handles
        all_store_names = [cls._storify(store) for store in stores]
        meta_store = "__meta__"
        if meta_store not in all_store_names:
            all_store_names.append(meta_store)

        opened: dict[str, SQLiteSubDb] = {}
        for store_name in all_store_names:
            opened[store_name] = SQLiteSubDb(name=store_name)

        dber = cls(name=name, stores=opened, conn=conn, path=path)

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

    # ---- Schema setup ----

    @staticmethod
    def _ensure_schema(conn: sqlite3.Connection):
        """Create the keri_store and keri_meta tables."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS keri_store (
                subdb  TEXT    NOT NULL,
                key    BLOB   NOT NULL,
                sort   BLOB   NOT NULL DEFAULT x'',
                value  BLOB   NOT NULL,
                PRIMARY KEY (subdb, key, sort)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_subdb_key
            ON keri_store (subdb, key)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_subdb
            ON keri_store (subdb)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS keri_meta (
                subdb   TEXT PRIMARY KEY,
                dupsort INTEGER NOT NULL DEFAULT 0,
                flags   TEXT
            )
        """)
        conn.commit()

    # ---- Key normalisation ----

    @staticmethod
    def _storify(key: bytes | str) -> str:
        """Convert a subdb key to its canonical string form."""
        if isinstance(key, str):
            return key
        if isinstance(key, bytes):
            return key.decode("utf-8")
        raise TypeError(f"Unsupported store handle type: {type(key)}")

    # ---- Internal SQLite helpers ----

    def _put_meta(self, db: SQLiteSubDb, meta: dict):
        """Store metadata for a subdb."""
        self._conn.execute(
            "INSERT OR REPLACE INTO keri_meta (subdb, dupsort, flags) "
            "VALUES (?, ?, ?)",
            (db.name, int(meta.get("dupsort", False)), json.dumps(meta)),
        )
        self._conn.commit()

    def _get_meta(self, db: SQLiteSubDb) -> dict | None:
        """Read metadata for a subdb."""
        cur = self._conn.execute(
            "SELECT flags FROM keri_meta WHERE subdb = ?",
            (db.name,),
        )
        row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return json.loads(row[0])

    def _clear_store(self, store_name: str):
        """Delete all items belonging to a store."""
        self._conn.execute(
            "DELETE FROM keri_store WHERE subdb = ?",
            (store_name,),
        )
        self._conn.execute(
            "DELETE FROM keri_meta WHERE subdb = ?",
            (store_name,),
        )
        self._conn.commit()

    # ---- Lifecycle ----

    def flush(self) -> int:
        """Commit any pending transaction.  Returns 0."""
        self._conn.commit()
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

    def getVer(self) -> str | None:
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
        if self._conn:
            self._conn.close()
            self._conn = None

    # ---- Single-value CRUD (placeholder stubs) ----

    def putVal(self, db: SQLiteSubDb, key: bytes, val: bytes) -> bool:
        """
        Insert val at key without overwriting an existing value.

        Uses atomic INSERT OR IGNORE to avoid TOCTOU race conditions.

        Returns:
            True when inserted.  False when key already exists.
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO keri_store (subdb, key, sort, value) "
            "VALUES (?, ?, ?, ?)",
            (db.name, key, _SK_SINGLE, val),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def setVal(self, db: SQLiteSubDb, key: bytes, val: bytes) -> bool:
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
        self._conn.execute(
            "INSERT OR REPLACE INTO keri_store (subdb, key, sort, value) "
            "VALUES (?, ?, ?, ?)",
            (db.name, key, _SK_SINGLE, val),
        )
        self._conn.commit()
        return True

    def getVal(self, db: SQLiteSubDb, key: bytes) -> bytes | None:
        """
        Return stored value at key, or None when missing.
        """
        if not key:
            raise KeyError(
                f"Key: `{key}` is either empty, too big (for lmdb), "
                "or wrong DUPFIXED size. ref) lmdb.BadValsizeError"
            )
        cur = self._conn.execute(
            "SELECT value FROM keri_store WHERE subdb = ? AND key = ? AND sort = ?",
            (db.name, key, _SK_SINGLE),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return bytes(row[0])

    def remVal(self, db: SQLiteSubDb, key: bytes) -> bool:
        """
        Remove the exact entry at key.

        Returns:
            True when removed.  False when key is empty or missing.
        """
        if not key:
            return False
        cur = self._conn.execute(
            "DELETE FROM keri_store WHERE subdb = ? AND key = ? AND sort = ?",
            (db.name, key, _SK_SINGLE),
        )
        self._conn.commit()
        return cur.rowcount > 0

    delVal = remVal  # backwards compat alias

    # ---- Ordinal (ON#) CRUD ----

    @staticmethod
    def _on_sort_key(on: int) -> bytes:
        """Build ordinal sort key: _SK_ON_PREFIX + 32-hex-padded ordinal."""
        return _SK_ON_PREFIX + b"%032x" % on

    @staticmethod
    def _ensure_key(key: bytes | str | memoryview) -> bytes:
        """Normalise key to bytes."""
        if isinstance(key, memoryview):
            return bytes(key)
        if isinstance(key, str):
            return key.encode("utf-8")
        return key

    def putOnVal(self, db: SQLiteSubDb, key: bytes, on: int = 0,
                 val: bytes | None = None, *, sep: bytes = b".") -> bool:
        """
        Insert val at ordinal *on* for *key*.  Does NOT overwrite.

        Returns False if val is None or if (key, on) already exists.
        """
        if val is None:
            return False
        key = self._ensure_key(key)
        sort = self._on_sort_key(on)
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO keri_store (subdb, key, sort, value) "
            "VALUES (?, ?, ?, ?)",
            (db.name, key, sort, val),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def pinOnVal(self, db: SQLiteSubDb, key: bytes, on: int = 0,
                 val: bytes | None = None, *, sep: bytes = b".") -> bool:
        """
        Insert or REPLACE val at ordinal *on* for *key*.

        Returns False if val is None.
        """
        if val is None:
            return False
        key = self._ensure_key(key)
        sort = self._on_sort_key(on)
        self._conn.execute(
            "INSERT OR REPLACE INTO keri_store (subdb, key, sort, value) "
            "VALUES (?, ?, ?, ?)",
            (db.name, key, sort, val),
        )
        self._conn.commit()
        return True

    def appendOnVal(self, db: SQLiteSubDb, key: bytes, val: bytes,
                    *, sep: bytes = b".") -> int:
        """
        Append val after the highest existing ordinal for *key*.

        Returns the new ordinal number (0 if first entry).
        """
        key = self._ensure_key(key)
        on_lo = _SK_ON_PREFIX
        on_hi = _SK_ON_PREFIX + b"\xff"
        cur = self._conn.execute(
            "SELECT sort FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "ORDER BY sort DESC LIMIT 1",
            (db.name, key, on_lo, on_hi),
        )
        row = cur.fetchone()
        if row is None:
            new_on = 0
        else:
            # Extract ordinal from sort key: strip _SK_ON_PREFIX, parse hex
            existing_sort = bytes(row[0])
            hex_part = existing_sort[len(_SK_ON_PREFIX):]
            new_on = int(hex_part, 16) + 1

        sort = self._on_sort_key(new_on)
        self._conn.execute(
            "INSERT INTO keri_store (subdb, key, sort, value) "
            "VALUES (?, ?, ?, ?)",
            (db.name, key, sort, val),
        )
        self._conn.commit()
        return new_on

    def getOnItem(self, db: SQLiteSubDb, key: bytes, on: int = 0,
                  *, sep: bytes = b".") -> tuple | None:
        """
        Return (key, on, val) tuple at ordinal *on*, or None if missing.
        """
        key = self._ensure_key(key)
        sort = self._on_sort_key(on)
        cur = self._conn.execute(
            "SELECT key, value FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort = ?",
            (db.name, key, sort),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return (bytes(row[0]), on, bytes(row[1]))

    def getOnVal(self, db: SQLiteSubDb, key: bytes, on: int = 0,
                 *, sep: bytes = b".") -> bytes | None:
        """
        Return val at ordinal *on*, or None if missing.
        """
        item = self.getOnItem(db, key, on, sep=sep)
        if item is None:
            return None
        return item[2]

    def remOn(self, db: SQLiteSubDb, key: bytes, on: int = 0,
              *, sep: bytes = b".") -> bool:
        """
        Remove entry at (key, on).  Returns True if removed.
        """
        key = self._ensure_key(key)
        sort = self._on_sort_key(on)
        cur = self._conn.execute(
            "DELETE FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort = ?",
            (db.name, key, sort),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def remOnAll(self, db: SQLiteSubDb, key: bytes = b"", on: int = 0,
                 *, sep: bytes = b".") -> bool:
        """
        Remove all ordinal entries at *key* with ordinal >= *on*.

        If key is empty, remove across all keys in the subdb.
        Returns True if any rows were removed.
        """
        sort_lo = self._on_sort_key(on)
        on_hi = _SK_ON_PREFIX + b"\xff"
        if key:
            key = self._ensure_key(key)
            cur = self._conn.execute(
                "DELETE FROM keri_store "
                "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ?",
                (db.name, key, sort_lo, on_hi),
            )
        else:
            cur = self._conn.execute(
                "DELETE FROM keri_store "
                "WHERE subdb = ? AND sort >= ? AND sort < ?",
                (db.name, sort_lo, on_hi),
            )
        self._conn.commit()
        return cur.rowcount > 0

    def cntOnAll(self, db: SQLiteSubDb, key: bytes = b"", on: int = 0,
                 *, sep: bytes = b".") -> int:
        """
        Count ordinal entries at *key* with ordinal >= *on*.

        If key is empty, count across all keys in the subdb.
        """
        sort_lo = self._on_sort_key(on)
        on_hi = _SK_ON_PREFIX + b"\xff"
        if key:
            key = self._ensure_key(key)
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM keri_store "
                "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ?",
                (db.name, key, sort_lo, on_hi),
            )
        else:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM keri_store "
                "WHERE subdb = ? AND sort >= ? AND sort < ?",
                (db.name, sort_lo, on_hi),
            )
        return cur.fetchone()[0]

    # ---- Top-level iteration and management ----

    def getTopItemIter(self, db: SQLiteSubDb,
                       top: bytes = b"") -> Iterator[tuple[bytes, bytes]]:
        """
        Iterate (key, val) pairs for single-value entries where key starts
        with *top* prefix.  If top is empty, iterate all single-value entries.
        """
        if top:
            top = self._ensure_key(top)
            # Prefix range: top <= key < top + b"\xff"
            top_hi = top + b"\xff"
            cur = self._conn.execute(
                "SELECT key, value FROM keri_store "
                "WHERE subdb = ? AND sort = ? AND key >= ? AND key < ? "
                "ORDER BY key",
                (db.name, _SK_SINGLE, top, top_hi),
            )
        else:
            cur = self._conn.execute(
                "SELECT key, value FROM keri_store "
                "WHERE subdb = ? AND sort = ? "
                "ORDER BY key",
                (db.name, _SK_SINGLE),
            )
        for row in cur:
            yield (bytes(row[0]), bytes(row[1]))

    def getOnTopItemIter(self, db: SQLiteSubDb,
                         top: bytes = b"", *,
                         sep: bytes = b".") -> Iterator[tuple[bytes, int, bytes]]:
        """
        Iterate (key, on, val) triples for ordinal entries where key starts
        with *top* prefix.  If top is empty, iterate all ordinal entries.
        """
        on_lo = _SK_ON_PREFIX
        on_hi = _SK_ON_PREFIX + b"\xff"
        if top:
            top = self._ensure_key(top)
            top_hi = top + b"\xff"
            cur = self._conn.execute(
                "SELECT key, sort, value FROM keri_store "
                "WHERE subdb = ? AND sort >= ? AND sort < ? "
                "AND key >= ? AND key < ? "
                "ORDER BY key, sort",
                (db.name, on_lo, on_hi, top, top_hi),
            )
        else:
            cur = self._conn.execute(
                "SELECT key, sort, value FROM keri_store "
                "WHERE subdb = ? AND sort >= ? AND sort < ? "
                "ORDER BY key, sort",
                (db.name, on_lo, on_hi),
            )
        prefix_len = len(_SK_ON_PREFIX)
        for row in cur:
            key = bytes(row[0])
            sort = bytes(row[1])
            on = int(sort[prefix_len:], 16)
            val = bytes(row[2])
            yield (key, on, val)

    def getOnAllItemIter(self, db: SQLiteSubDb,
                         key: bytes = b"", on: int = 0, *,
                         sep: bytes = b".") -> Iterator[tuple[bytes, int, bytes]]:
        """
        Iterate (key, on, val) for ordinal entries at a specific key with
        ordinal >= *on*.  If key is empty, iterate across all keys.
        """
        sort_lo = self._on_sort_key(on)
        on_hi = _SK_ON_PREFIX + b"\xff"
        if key:
            key = self._ensure_key(key)
            cur = self._conn.execute(
                "SELECT key, sort, value FROM keri_store "
                "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
                "ORDER BY key, sort",
                (db.name, key, sort_lo, on_hi),
            )
        else:
            cur = self._conn.execute(
                "SELECT key, sort, value FROM keri_store "
                "WHERE subdb = ? AND sort >= ? AND sort < ? "
                "ORDER BY key, sort",
                (db.name, sort_lo, on_hi),
            )
        prefix_len = len(_SK_ON_PREFIX)
        for row in cur:
            k = bytes(row[0])
            sort = bytes(row[1])
            ordinal = int(sort[prefix_len:], 16)
            val = bytes(row[2])
            yield (k, ordinal, val)

    def remTop(self, db: SQLiteSubDb, top: bytes = b"") -> bool:
        """
        Remove ALL entries (any sort key type) where key starts with *top*.
        If top is empty, remove all entries in the subdb.  Returns True if
        any rows were removed.
        """
        if top:
            top = self._ensure_key(top)
            top_hi = top + b"\xff"
            cur = self._conn.execute(
                "DELETE FROM keri_store "
                "WHERE subdb = ? AND key >= ? AND key < ?",
                (db.name, top, top_hi),
            )
        else:
            cur = self._conn.execute(
                "DELETE FROM keri_store WHERE subdb = ?",
                (db.name,),
            )
        self._conn.commit()
        return cur.rowcount > 0

    delTop = remTop  # backwards compat alias

    def cntTop(self, db: SQLiteSubDb, top: bytes = b"") -> int:
        """
        Count single-value entries (sort=_SK_SINGLE) with key prefix *top*.
        If top is empty, count all single-value entries.
        """
        if top:
            top = self._ensure_key(top)
            top_hi = top + b"\xff"
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM keri_store "
                "WHERE subdb = ? AND sort = ? AND key >= ? AND key < ?",
                (db.name, _SK_SINGLE, top, top_hi),
            )
        else:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM keri_store "
                "WHERE subdb = ? AND sort = ?",
                (db.name, _SK_SINGLE),
            )
        return cur.fetchone()[0]

    def cntAll(self, db: SQLiteSubDb) -> int:
        """
        Count all single-value entries in the subdb.
        """
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM keri_store "
            "WHERE subdb = ? AND sort = ?",
            (db.name, _SK_SINGLE),
        )
        return cur.fetchone()[0]

    # ---- IoSet (Insertion-Ordered Set) helpers ----

    @staticmethod
    def _io_sort_key(ion: int) -> bytes:
        """Build IoSet sort key: _SK_IO_PREFIX + 32-hex-padded ion."""
        return _SK_IO_PREFIX + b"%032x" % ion

    def _next_ion(self, db: SQLiteSubDb, key: bytes) -> int:
        """
        Find the next available insertion-order number for *key*.

        Returns max existing ion + 1, or 0 if no IoSet entries exist.
        """
        io_lo = _SK_IO_PREFIX
        io_hi = _SK_IO_PREFIX + b"\xff"
        cur = self._conn.execute(
            "SELECT sort FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "ORDER BY sort DESC LIMIT 1",
            (db.name, key, io_lo, io_hi),
        )
        row = cur.fetchone()
        if row is None:
            return 0
        existing_sort = bytes(row[0])
        hex_part = existing_sort[len(_SK_IO_PREFIX):]
        return int(hex_part, 16) + 1

    def _ioset_has_val(self, db: SQLiteSubDb, key: bytes, val: bytes) -> bool:
        """
        Check if *val* already exists in the IoSet at *key*.
        """
        io_lo = _SK_IO_PREFIX
        io_hi = _SK_IO_PREFIX + b"\xff"
        cur = self._conn.execute(
            "SELECT 1 FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "AND value = ? LIMIT 1",
            (db.name, key, io_lo, io_hi, val),
        )
        return cur.fetchone() is not None

    # ---- IoSet public methods ----

    def putIoSetVals(self, db: SQLiteSubDb, key: bytes, vals,
                     *, sep: bytes = b".") -> bool:
        """
        Add each val from *vals* to the IoSet at *key*.

        Skips vals already present (no duplicates).
        Returns True if any vals were added.
        """
        key = self._ensure_key(key)
        added = False
        for val in vals:
            if not self._ioset_has_val(db, key, val):
                ion = self._next_ion(db, key)
                sort = self._io_sort_key(ion)
                self._conn.execute(
                    "INSERT INTO keri_store (subdb, key, sort, value) "
                    "VALUES (?, ?, ?, ?)",
                    (db.name, key, sort, val),
                )
                added = True
        if added:
            self._conn.commit()
        return added

    def pinIoSetVals(self, db: SQLiteSubDb, key: bytes, vals,
                     *, sep: bytes = b".") -> bool:
        """
        DELETE all existing IoSet entries at *key*, then INSERT *vals*
        with fresh ion numbering.

        Returns True.
        """
        key = self._ensure_key(key)
        io_lo = _SK_IO_PREFIX
        io_hi = _SK_IO_PREFIX + b"\xff"
        self._conn.execute(
            "DELETE FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ?",
            (db.name, key, io_lo, io_hi),
        )
        for ion, val in enumerate(vals):
            sort = self._io_sort_key(ion)
            self._conn.execute(
                "INSERT INTO keri_store (subdb, key, sort, value) "
                "VALUES (?, ?, ?, ?)",
                (db.name, key, sort, val),
            )
        self._conn.commit()
        return True

    def addIoSetVal(self, db: SQLiteSubDb, key: bytes, val: bytes,
                    *, sep: bytes = b".") -> bool:
        """
        Add a single *val* to the IoSet at *key*.

        Returns False if *val* is already present.
        """
        key = self._ensure_key(key)
        if self._ioset_has_val(db, key, val):
            return False
        ion = self._next_ion(db, key)
        sort = self._io_sort_key(ion)
        self._conn.execute(
            "INSERT INTO keri_store (subdb, key, sort, value) "
            "VALUES (?, ?, ?, ?)",
            (db.name, key, sort, val),
        )
        self._conn.commit()
        return True

    def getIoSetItemIter(self, db: SQLiteSubDb, key: bytes, *,
                         ion: int = 0,
                         sep: bytes = b".") -> Iterator[tuple[bytes, bytes]]:
        """
        Iterate (key, val) pairs in insertion order starting from *ion*.
        """
        key = self._ensure_key(key)
        sort_lo = self._io_sort_key(ion)
        io_hi = _SK_IO_PREFIX + b"\xff"
        cur = self._conn.execute(
            "SELECT key, value FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "ORDER BY sort",
            (db.name, key, sort_lo, io_hi),
        )
        for row in cur:
            yield (bytes(row[0]), bytes(row[1]))

    def getIoSetLastItem(self, db: SQLiteSubDb, key: bytes, *,
                         sep: bytes = b".") -> tuple:
        """
        Return (key, val) for the last inserted item in the IoSet at *key*.

        Returns empty tuple () if no entries exist.
        """
        key = self._ensure_key(key)
        io_lo = _SK_IO_PREFIX
        io_hi = _SK_IO_PREFIX + b"\xff"
        cur = self._conn.execute(
            "SELECT key, value FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "ORDER BY sort DESC LIMIT 1",
            (db.name, key, io_lo, io_hi),
        )
        row = cur.fetchone()
        if row is None:
            return ()
        return (bytes(row[0]), bytes(row[1]))

    def remIoSet(self, db: SQLiteSubDb, key: bytes,
                 *, sep: bytes = b".") -> bool:
        """
        Remove all IoSet entries at *key*.

        Returns True if any entries were removed.
        """
        key = self._ensure_key(key)
        io_lo = _SK_IO_PREFIX
        io_hi = _SK_IO_PREFIX + b"\xff"
        cur = self._conn.execute(
            "DELETE FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ?",
            (db.name, key, io_lo, io_hi),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def remIoSetVal(self, db: SQLiteSubDb, key: bytes, val=None,
                    *, sep: bytes = b".") -> bool:
        """
        Remove a specific *val* from the IoSet at *key*.

        Returns True if removed, False if not found.
        """
        key = self._ensure_key(key)
        io_lo = _SK_IO_PREFIX
        io_hi = _SK_IO_PREFIX + b"\xff"
        # Find the sort key for this specific value
        cur = self._conn.execute(
            "SELECT sort FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "AND value = ? LIMIT 1",
            (db.name, key, io_lo, io_hi, val),
        )
        row = cur.fetchone()
        if row is None:
            return False
        sort = bytes(row[0])
        cur = self._conn.execute(
            "DELETE FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort = ?",
            (db.name, key, sort),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def cntIoSet(self, db: SQLiteSubDb, key: bytes, *,
                 ion: int = 0, sep: bytes = b".") -> int:
        """
        Count IoSet entries at *key* with ion >= *ion*.
        """
        key = self._ensure_key(key)
        sort_lo = self._io_sort_key(ion)
        io_hi = _SK_IO_PREFIX + b"\xff"
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ?",
            (db.name, key, sort_lo, io_hi),
        )
        return cur.fetchone()[0]

    def getTopIoSetItemIter(self, db: SQLiteSubDb, top: bytes = b"",
                            *, sep: bytes = b".") -> Iterator[tuple[bytes, bytes]]:
        """
        Iterate (key, val) across all IoSet entries where key starts with
        *top* prefix.  Ordered by key then sort.
        """
        io_lo = _SK_IO_PREFIX
        io_hi = _SK_IO_PREFIX + b"\xff"
        if top:
            top = self._ensure_key(top)
            top_hi = top + b"\xff"
            cur = self._conn.execute(
                "SELECT key, value FROM keri_store "
                "WHERE subdb = ? AND sort >= ? AND sort < ? "
                "AND key >= ? AND key < ? "
                "ORDER BY key, sort",
                (db.name, io_lo, io_hi, top, top_hi),
            )
        else:
            cur = self._conn.execute(
                "SELECT key, value FROM keri_store "
                "WHERE subdb = ? AND sort >= ? AND sort < ? "
                "ORDER BY key, sort",
                (db.name, io_lo, io_hi),
            )
        for row in cur:
            yield (bytes(row[0]), bytes(row[1]))

    # ---- OnIoSet (Ordinal + Insertion-Ordered Set) helpers ----

    @staticmethod
    def _onio_sort_key(on: int, ion: int) -> bytes:
        """Build OnIoSet sort key: _SK_ONIO_PREFIX + 32-hex-on + b"." + 32-hex-ion."""
        return _SK_ONIO_PREFIX + b"%032x" % on + b"." + b"%032x" % ion

    @staticmethod
    def _onio_prefix(on: int) -> bytes:
        """Build OnIoSet prefix for all items at a given ordinal."""
        return _SK_ONIO_PREFIX + b"%032x" % on + b"."

    def _next_onio_ion(self, db: SQLiteSubDb, key: bytes, on: int) -> int:
        """
        Find max ion at (key, on) and return max+1, or 0 if none exist.
        """
        prefix = self._onio_prefix(on)
        prefix_hi = prefix + b"\xff"
        cur = self._conn.execute(
            "SELECT sort FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "ORDER BY sort DESC LIMIT 1",
            (db.name, key, prefix, prefix_hi),
        )
        row = cur.fetchone()
        if row is None:
            return 0
        existing_sort = bytes(row[0])
        # Extract ion: everything after the prefix
        ion_hex = existing_sort[len(prefix):]
        return int(ion_hex, 16) + 1

    def _onio_has_val(self, db: SQLiteSubDb, key: bytes, on: int,
                      val: bytes) -> bool:
        """Check if val exists in the OnIoSet at (key, on)."""
        prefix = self._onio_prefix(on)
        prefix_hi = prefix + b"\xff"
        cur = self._conn.execute(
            "SELECT 1 FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "AND value = ? LIMIT 1",
            (db.name, key, prefix, prefix_hi, val),
        )
        return cur.fetchone() is not None

    def _next_onio_on(self, db: SQLiteSubDb, key: bytes) -> int:
        """
        Find max on for key across all OnIoSet entries.

        Extract on from sort key: strip _SK_ONIO_PREFIX, split on b".",
        first part is on hex.  Returns max+1 or 0.
        """
        onio_lo = _SK_ONIO_PREFIX
        onio_hi = _SK_ONIO_PREFIX + b"\xff"
        cur = self._conn.execute(
            "SELECT sort FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "ORDER BY sort DESC LIMIT 1",
            (db.name, key, onio_lo, onio_hi),
        )
        row = cur.fetchone()
        if row is None:
            return 0
        existing_sort = bytes(row[0])
        # Strip ONIO# prefix, split on ".", take the on part
        after_prefix = existing_sort[len(_SK_ONIO_PREFIX):]
        on_hex = after_prefix.split(b".")[0]
        return int(on_hex, 16) + 1

    # ---- OnIoSet public methods ----

    def putOnIoSetVals(self, db: SQLiteSubDb, key: bytes, *, on: int = 0,
                       vals=None, sep: bytes = b".") -> bool:
        """
        Add vals to the IoSet at (key, on).  Skip duplicates.

        Returns True if any vals were added.  Returns False if vals is None.
        """
        if vals is None:
            return False
        key = self._ensure_key(key)
        added = False
        for val in vals:
            if not self._onio_has_val(db, key, on, val):
                ion = self._next_onio_ion(db, key, on)
                sort = self._onio_sort_key(on, ion)
                self._conn.execute(
                    "INSERT INTO keri_store (subdb, key, sort, value) "
                    "VALUES (?, ?, ?, ?)",
                    (db.name, key, sort, val),
                )
                added = True
        if added:
            self._conn.commit()
        return added

    def pinOnIoSetVals(self, db: SQLiteSubDb, key: bytes, *, on: int = 0,
                       vals=None, sep: bytes = b".") -> bool:
        """
        Replace all vals at (key, on).  DELETE existing then INSERT.

        Returns False if vals is None.
        """
        if vals is None:
            return False
        key = self._ensure_key(key)
        prefix = self._onio_prefix(on)
        prefix_hi = prefix + b"\xff"
        self._conn.execute(
            "DELETE FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ?",
            (db.name, key, prefix, prefix_hi),
        )
        for ion, val in enumerate(vals):
            sort = self._onio_sort_key(on, ion)
            self._conn.execute(
                "INSERT INTO keri_store (subdb, key, sort, value) "
                "VALUES (?, ?, ?, ?)",
                (db.name, key, sort, val),
            )
        self._conn.commit()
        return True

    def appendOnIoSetVals(self, db: SQLiteSubDb, key: bytes, vals,
                          *, sep: bytes = b".") -> int:
        """
        Auto-increment on: find next on for key, insert vals at that on.

        Returns the new on number.
        """
        key = self._ensure_key(key)
        new_on = self._next_onio_on(db, key)
        for ion, val in enumerate(vals):
            sort = self._onio_sort_key(new_on, ion)
            self._conn.execute(
                "INSERT INTO keri_store (subdb, key, sort, value) "
                "VALUES (?, ?, ?, ?)",
                (db.name, key, sort, val),
            )
        self._conn.commit()
        return new_on

    def addOnIoSetVal(self, db: SQLiteSubDb, key: bytes, *, on: int = 0,
                      val=None, sep: bytes = b".") -> bool:
        """
        Add single val to (key, on).  Returns False if None or duplicate.
        """
        if val is None:
            return False
        key = self._ensure_key(key)
        if self._onio_has_val(db, key, on, val):
            return False
        ion = self._next_onio_ion(db, key, on)
        sort = self._onio_sort_key(on, ion)
        self._conn.execute(
            "INSERT INTO keri_store (subdb, key, sort, value) "
            "VALUES (?, ?, ?, ?)",
            (db.name, key, sort, val),
        )
        self._conn.commit()
        return True

    def getOnIoSetItemIter(self, db: SQLiteSubDb, key: bytes, *,
                           on: int = 0, ion: int = 0,
                           sep: bytes = b".") -> Iterator[tuple[bytes, int, bytes]]:
        """
        Iterate (key, on, val) for IoSet at (key, on) starting from ion.
        """
        key = self._ensure_key(key)
        sort_lo = self._onio_sort_key(on, ion)
        prefix_hi = self._onio_prefix(on) + b"\xff"
        cur = self._conn.execute(
            "SELECT key, sort, value FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "ORDER BY sort",
            (db.name, key, sort_lo, prefix_hi),
        )
        prefix_len = len(_SK_ONIO_PREFIX)
        for row in cur:
            k = bytes(row[0])
            sort = bytes(row[1])
            after_prefix = sort[prefix_len:]
            on_hex = after_prefix.split(b".")[0]
            ordinal = int(on_hex, 16)
            val = bytes(row[2])
            yield (k, ordinal, val)

    def getOnIoSetLastItem(self, db: SQLiteSubDb, key: bytes, on: int = 0,
                           *, sep: bytes = b".") -> tuple:
        """
        Return (key, on, val) for the last item at (key, on).

        Returns empty tuple () if no entries exist.
        """
        key = self._ensure_key(key)
        prefix = self._onio_prefix(on)
        prefix_hi = prefix + b"\xff"
        cur = self._conn.execute(
            "SELECT key, sort, value FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "ORDER BY sort DESC LIMIT 1",
            (db.name, key, prefix, prefix_hi),
        )
        row = cur.fetchone()
        if row is None:
            return ()
        k = bytes(row[0])
        sort = bytes(row[1])
        after_prefix = sort[len(_SK_ONIO_PREFIX):]
        on_hex = after_prefix.split(b".")[0]
        ordinal = int(on_hex, 16)
        val = bytes(row[2])
        return (k, ordinal, val)

    def remOnIoSetVal(self, db: SQLiteSubDb, key: bytes, *, on: int = 0,
                      val=None, sep: bytes = b".") -> bool:
        """
        Remove specific val from (key, on).  If val is None, remove ALL
        at (key, on).  Returns True if any removed.
        """
        key = self._ensure_key(key)
        prefix = self._onio_prefix(on)
        prefix_hi = prefix + b"\xff"
        if val is None:
            # Remove all entries at (key, on)
            cur = self._conn.execute(
                "DELETE FROM keri_store "
                "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ?",
                (db.name, key, prefix, prefix_hi),
            )
            self._conn.commit()
            return cur.rowcount > 0
        # Find the sort key for this specific value
        cur = self._conn.execute(
            "SELECT sort FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ? "
            "AND value = ? LIMIT 1",
            (db.name, key, prefix, prefix_hi, val),
        )
        row = cur.fetchone()
        if row is None:
            return False
        sort = bytes(row[0])
        cur = self._conn.execute(
            "DELETE FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort = ?",
            (db.name, key, sort),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def remOnAllIoSet(self, db: SQLiteSubDb, key: bytes = b"", on: int = 0,
                      *, sep: bytes = b".") -> bool:
        """
        Remove all OnIoSet entries at key with ordinal >= on.

        Empty key removes across all keys.
        """
        sort_lo = self._onio_prefix(on)
        onio_hi = _SK_ONIO_PREFIX + b"\xff"
        if key:
            key = self._ensure_key(key)
            cur = self._conn.execute(
                "DELETE FROM keri_store "
                "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ?",
                (db.name, key, sort_lo, onio_hi),
            )
        else:
            cur = self._conn.execute(
                "DELETE FROM keri_store "
                "WHERE subdb = ? AND sort >= ? AND sort < ?",
                (db.name, sort_lo, onio_hi),
            )
        self._conn.commit()
        return cur.rowcount > 0

    def cntOnIoSet(self, db: SQLiteSubDb, key: bytes, *,
                   on: int = 0, ion: int = 0,
                   sep: bytes = b".") -> int:
        """Count entries at (key, on) starting from ion."""
        key = self._ensure_key(key)
        sort_lo = self._onio_sort_key(on, ion)
        prefix_hi = self._onio_prefix(on) + b"\xff"
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM keri_store "
            "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ?",
            (db.name, key, sort_lo, prefix_hi),
        )
        return cur.fetchone()[0]

    def cntOnAllIoSet(self, db: SQLiteSubDb, key: bytes = b"", *,
                      on: int = 0, sep: bytes = b".") -> int:
        """
        Count all OnIoSet entries at key with ordinal >= on.

        Empty key counts all.
        """
        sort_lo = self._onio_prefix(on)
        onio_hi = _SK_ONIO_PREFIX + b"\xff"
        if key:
            key = self._ensure_key(key)
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM keri_store "
                "WHERE subdb = ? AND key = ? AND sort >= ? AND sort < ?",
                (db.name, key, sort_lo, onio_hi),
            )
        else:
            cur = self._conn.execute(
                "SELECT COUNT(*) FROM keri_store "
                "WHERE subdb = ? AND sort >= ? AND sort < ?",
                (db.name, sort_lo, onio_hi),
            )
        return cur.fetchone()[0]

    # ---- Dup methods (delegate to IoSet) ----

    def putVals(self, db, key, vals, *, sep=b'.'):
        return self.putIoSetVals(db, key, vals, sep=sep)

    def addVal(self, db, key, val, *, sep=b'.'):
        return self.addIoSetVal(db, key, val, sep=sep)

    def getVals(self, db, key, *, sep=b'.'):
        return [v for k, v in self.getIoSetItemIter(db, key, sep=sep)]

    def getValsIter(self, db, key, *, sep=b'.'):
        for k, v in self.getIoSetItemIter(db, key, sep=sep):
            yield v

    def cntVals(self, db, key, *, sep=b'.'):
        return self.cntIoSet(db, key, sep=sep)

    def delVals(self, db, key, *, sep=b'.'):
        return self.remIoSet(db, key, sep=sep)

    # ---- IoDup methods (delegate to IoSet) ----

    def putIoDupVals(self, db, key, vals, *, sep=b'.'):
        return self.putIoSetVals(db, key, vals, sep=sep)

    def addIoDupVal(self, db, key, val, *, sep=b'.'):
        return self.addIoSetVal(db, key, val, sep=sep)

    def getIoDupVals(self, db, key, *, sep=b'.'):
        return [v for k, v in self.getIoSetItemIter(db, key, sep=sep)]

    def getIoDupItemIter(self, db, key, *, ion=0, sep=b'.'):
        return self.getIoSetItemIter(db, key, ion=ion, sep=sep)

    def getIoDupValLast(self, db, key, *, sep=b'.'):
        item = self.getIoSetLastItem(db, key, sep=sep)
        return item[1] if item else None

    def delIoDupVals(self, db, key, *, sep=b'.'):
        return self.remIoSet(db, key, sep=sep)

    def delIoDupVal(self, db, key, val, *, sep=b'.'):
        return self.remIoSetVal(db, key, val, sep=sep)

    def cntIoDups(self, db, key, *, sep=b'.'):
        return self.cntIoSet(db, key, sep=sep)

    # ---- OnIoDup methods (delegate to OnIoSet) ----

    def putOnIoDupVals(self, db, key, on=0, vals=b'', *, sep=b'.'):
        return self.putOnIoSetVals(db, key, on=on, vals=vals, sep=sep)

    def addOnIoDupVal(self, db, key, on=0, val=b'', sep=b'.'):
        return self.addOnIoSetVal(db, key, on=on, val=val, sep=sep)

    def appendOnIoDupVal(self, db, key, val, *, sep=b'.'):
        return self.appendOnIoSetVals(db, key, [val], sep=sep)

    def getOnIoDupVals(self, db, key, on=0, sep=b'.'):
        return [v for k, o, v in self.getOnIoSetItemIter(db, key, on=on, sep=sep)]

    def getOnIoDupItemIter(self, db, key, on=0, ion=0, sep=b'.'):
        return self.getOnIoSetItemIter(db, key, on=on, ion=ion, sep=sep)

    def getOnIoDupLast(self, db, key, on=0, *, sep=b'.'):
        item = self.getOnIoSetLastItem(db, key, on=on, sep=sep)
        return item[2] if item else None

    def delOnIoDups(self, db, key, on=0, sep=b'.'):
        return self.remOnIoSetVal(db, key, on=on, sep=sep)

    def delOnIoDupVal(self, db, key, on=0, val=b'', sep=b'.'):
        return self.remOnIoSetVal(db, key, on=on, val=val, sep=sep)

    def cntOnIoDups(self, db, key, on=0, sep=b'.'):
        return self.cntOnIoSet(db, key, on=on, sep=sep)

    # ---- Last-item iteration helpers ----

    def getIoSetLastItemIterAll(self, db: SQLiteSubDb, key: bytes = b"",
                                *, sep: bytes = b".") -> Iterator[tuple[bytes, bytes]]:
        """
        For each distinct key with IoSet entries (optionally filtered by
        *key* prefix), yield the last (key, val) pair.
        """
        io_lo = _SK_IO_PREFIX
        io_hi = _SK_IO_PREFIX + b"\xff"
        if key:
            key = self._ensure_key(key)
            key_hi = key + b"\xff"
            cur = self._conn.execute(
                "SELECT k.key, k.value FROM keri_store k "
                "INNER JOIN ("
                "  SELECT key, MAX(sort) AS max_sort FROM keri_store "
                "  WHERE subdb = ? AND sort >= ? AND sort < ? "
                "  AND key >= ? AND key < ? "
                "  GROUP BY key"
                ") m ON k.key = m.key AND k.sort = m.max_sort "
                "AND k.subdb = ? "
                "ORDER BY k.key",
                (db.name, io_lo, io_hi, key, key_hi, db.name),
            )
        else:
            cur = self._conn.execute(
                "SELECT k.key, k.value FROM keri_store k "
                "INNER JOIN ("
                "  SELECT key, MAX(sort) AS max_sort FROM keri_store "
                "  WHERE subdb = ? AND sort >= ? AND sort < ? "
                "  GROUP BY key"
                ") m ON k.key = m.key AND k.sort = m.max_sort "
                "AND k.subdb = ? "
                "ORDER BY k.key",
                (db.name, io_lo, io_hi, db.name),
            )
        for row in cur:
            yield (bytes(row[0]), bytes(row[1]))

    def getIoSetLastIterAll(self, db: SQLiteSubDb, key: bytes = b"",
                            *, sep: bytes = b".") -> Iterator[bytes]:
        """
        Same as getIoSetLastItemIterAll but yield just val (not key).
        """
        for _, val in self.getIoSetLastItemIterAll(db, key, sep=sep):
            yield val


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

@contextmanager
def openSQLite(*, cls=None, name="test", stores=None, path="",
               temp=False, **kwa):
    """Context manager for SQLiteDBer instances.

    Parameters:
        cls: Class to instantiate.  Defaults to SQLiteDBer.
        name: Database instance name.
        stores: List of store names.
        path: Filesystem path for the SQLite database file.
        temp: If True, clear data on close.
        **kwa: Passed to cls.open().
    """
    if cls is None:
        cls = SQLiteDBer
    if stores is None:
        stores = []
    dber = None
    try:
        dber = cls.open(name=name, stores=stores, path=path, **kwa)
        dber.temp = temp
        yield dber
    finally:
        if dber:
            dber.close(clear=temp)
