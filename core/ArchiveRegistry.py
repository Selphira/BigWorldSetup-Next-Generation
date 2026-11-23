"""Persistent identity of the archives checked or associated for each mod."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import Enum
import json
import logging
from pathlib import Path
import threading
from typing import Any

from constants import ARCHIVE_REGISTRY_FORMAT
from core.atomic_io import set_aside_unreadable, write_json_atomic

logger = logging.getLogger(__name__)


class ArchiveSource(str, Enum):
    """How an archive reached the user's disk."""

    DOWNLOAD = "download"
    MANUAL = "manual"


@dataclass(frozen=True)
class ArchiveRecord:
    """Identity of an archive on disk and the proof established for it.

    `size` and `mtime_ns` describe the file at the time `sha256` was computed;
    the hash only remains a proof while both still match the disk.

    Attributes:
        mod_id: Mod the archive belongs to
        path: Absolute path of the archive
        source: Downloaded or associated by the user
        size: File size when last observed
        mtime_ns: Modification time when last observed
        sha256: Computed hash, None until verified
        expected_sha256: Catalogue hash at verification time, None if unpublished
        accepted_mismatch: The user explicitly accepted a hash differing from the catalogue
        verified_at: ISO timestamp of the hash computation
    """

    mod_id: str
    path: str
    source: ArchiveSource
    size: int = 0
    mtime_ns: int = 0
    sha256: str | None = None
    expected_sha256: str | None = None
    accepted_mismatch: bool = False
    verified_at: str | None = None

    @property
    def archive_path(self) -> Path:
        return Path(self.path)

    @property
    def is_verified(self) -> bool:
        return self.sha256 is not None

    def matches_disk(self) -> bool:
        """Tell whether the file still has the identity observed when it was hashed."""
        try:
            stat = self.archive_path.stat()
        except OSError:
            return False
        return stat.st_size == self.size and stat.st_mtime_ns == self.mtime_ns

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"source": self.source.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArchiveRecord:
        return cls(
            mod_id=str(data["mod_id"]),
            path=str(data["path"]),
            source=ArchiveSource(data["source"]),
            size=int(data["size"]),
            mtime_ns=int(data["mtime_ns"]),
            sha256=data["sha256"],
            expected_sha256=data["expected_sha256"],
            accepted_mismatch=bool(data["accepted_mismatch"]),
            verified_at=data["verified_at"],
        )

    @classmethod
    def unverified(cls, mod_id: str, path: Path, source: ArchiveSource) -> ArchiveRecord:
        """Record an archive whose content has not been hashed yet."""
        return cls(mod_id=mod_id, path=str(path), source=source)

    def with_hash(
        self, sha256: str, size: int, mtime_ns: int, expected_sha256: str | None, accepted: bool
    ) -> ArchiveRecord:
        return replace(
            self,
            size=size,
            mtime_ns=mtime_ns,
            sha256=sha256.lower(),
            expected_sha256=expected_sha256.lower() if expected_sha256 else None,
            accepted_mismatch=accepted,
            verified_at=datetime.now().isoformat(timespec="seconds"),
        )


class ArchiveRegistry:
    """One archive record per mod, saved atomically after each change.

    The registry only holds proofs: losing it forces a new verification, never
    an unverified success. An unreadable file is therefore set aside for
    diagnosis and replaced by an empty registry.

    Verification workers record their proofs from their own thread, so every
    access is serialized.
    """

    def __init__(self, path: Path, records: dict[str, ArchiveRecord] | None = None) -> None:
        self.path = path
        self._records: dict[str, ArchiveRecord] = records or {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> ArchiveRegistry:
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["format"] != ARCHIVE_REGISTRY_FORMAT:
                raise ValueError(f"format {data['format']}")
            records = {
                mod_id: ArchiveRecord.from_dict(record)
                for mod_id, record in data["archives"].items()
            }
            return cls(path, records)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            cls._set_aside_unreadable(path, error)
            return cls(path)

    def save(self) -> None:
        with self._lock:
            archives = {mod_id: record.to_dict() for mod_id, record in self._records.items()}
            write_json_atomic(
                self.path, {"format": ARCHIVE_REGISTRY_FORMAT, "archives": archives}
            )

    @staticmethod
    def _set_aside_unreadable(path: Path, error: Exception) -> None:
        try:
            target = set_aside_unreadable(path)
            logger.warning("Archive registry unreadable (%s), set aside: %s", error, target)
        except OSError:
            logger.exception("Archive registry unreadable (%s) and cannot be set aside", error)

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    def get(self, mod_id: str) -> ArchiveRecord | None:
        with self._lock:
            return self._records.get(mod_id)

    def put(self, record: ArchiveRecord) -> None:
        with self._lock:
            self._records[record.mod_id] = record
            self.save()

    def remove(self, mod_id: str) -> None:
        with self._lock:
            if self._records.pop(mod_id, None) is not None:
                self.save()
