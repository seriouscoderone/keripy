"""ArtifactStore: the store-artifact effect for a publish command.

A configurable capability that persists a public SAD (keyed by SAID) to a
content-addressed store AND claims serializable first-seen (which AID published
this SAID here first). Two impls: LocalArtifactStore (in-memory, tests) and
S3ArtifactStore (S3 CAS + DynamoDB conditional first-seen). The store is a
generic verb; the "first publisher" meaning is composed by the pipeline."""
import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from keri.help import helping


@dataclass
class FirstSeenResult:
    created: bool           # True if this call wrote the bytes for the first time
    first_seen: bool        # True if `by` is the first publisher of this SAID here
    first_publisher: str    # the AID that first published this SAID (== by if first_seen)
    first_at: str           # ISO-8601 timestamp of the first publication


@runtime_checkable
class ArtifactStore(Protocol):
    def store(self, said: str, raw: bytes, by: str) -> FirstSeenResult:
        """Persist `raw` under `said` (idempotent) and claim first-seen for `by`."""
        ...


class LocalArtifactStore:
    """In-memory, thread-safe ArtifactStore for the local runtime + tests."""

    def __init__(self):
        self._lock = threading.Lock()
        self._blobs: dict[str, bytes] = {}
        self._first: dict[str, tuple[str, str]] = {}   # said -> (publisher, dt)

    def store(self, said: str, raw: bytes, by: str) -> FirstSeenResult:
        with self._lock:
            created = said not in self._blobs
            self._blobs[said] = bytes(raw)
            if said not in self._first:
                dt = helping.nowIso8601()
                self._first[said] = (by, dt)
                return FirstSeenResult(created=created, first_seen=True,
                                       first_publisher=by, first_at=dt)
            publisher, dt = self._first[said]
            return FirstSeenResult(created=created, first_seen=False,
                                   first_publisher=publisher, first_at=dt)

    def get(self, said: str) -> bytes | None:
        return self._blobs.get(said)


import boto3  # noqa: E402  (stdlib-then-third-party; boto3 only needed for S3ArtifactStore)


class S3ArtifactStore:
    """Prod ArtifactStore: S3 CAS (object key ``<key_prefix><said>``,
    Content-Type application/schema+json) + serializable first-seen via a
    DynamoDBer conditional write in the service's ``pub`` namespace."""

    def __init__(self, bucket: str, db, *, store_name: str = "pub.",
                 key_prefix: str = "oobi/", s3=None):
        self.bucket = bucket
        self.db = db
        self.store_name = store_name
        self.key_prefix = key_prefix
        self._s3 = s3 or boto3.client("s3")

    def store(self, said: str, raw: bytes, by: str) -> FirstSeenResult:
        # Idempotent by SAID: same content → same key; overwriting is a no-op.
        self._s3.put_object(
            Bucket=self.bucket,
            Key=f"{self.key_prefix}{said}",
            Body=bytes(raw),
            ContentType="application/schema+json",
        )
        claimed, existing = self.db.claimFirstSeen(
            self.store_name, said.encode("utf-8"), by.encode("utf-8")
        )
        if claimed:
            return FirstSeenResult(
                created=True,
                first_seen=True,
                first_publisher=by,
                first_at=helping.nowIso8601(),
            )
        prior = existing.decode("utf-8") if existing else ""
        return FirstSeenResult(
            created=False,
            first_seen=False,
            first_publisher=prior,
            first_at="",
        )
