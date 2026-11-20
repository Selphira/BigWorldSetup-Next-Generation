"""Locate the archive of a mod and tell how strongly its identity is proven."""

from __future__ import annotations

from dataclasses import dataclass, replace
import logging
from pathlib import Path

from core.ArchiveRegistry import ArchiveRecord, ArchiveRegistry, ArchiveSource
from core.DownloadManager import ArchiveStatus, ArchiveVerifier, HashAlgorithm
from core.Mod import Mod, ModFile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArchiveResolution:
    """Archive retained for a mod and the state of its proof.

    Attributes:
        path: Archive to use, None when nothing is known on disk
        status: Proof state; UNKNOWN means present but not hashed since its last change
        record: Registry entry backing the status, if any
    """

    path: Path | None
    status: ArchiveStatus
    record: ArchiveRecord | None = None


class ArchiveService:
    """Resolve, verify and associate mod archives without any Qt dependency.

    Hashing reads the whole file: callers must run `verify` and
    `associate_manual` outside the UI thread.
    """

    def __init__(
        self, registry: ArchiveRegistry, verifier: ArchiveVerifier | None = None
    ) -> None:
        self._registry = registry
        self._verifier = verifier or ArchiveVerifier()

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(self, mod: Mod, download_dir: Path, platform: str) -> ArchiveResolution:
        """Find the archive of a mod, a manual association taking precedence.

        No file is hashed: a status other than UNKNOWN relies on a registry
        proof whose disk identity is unchanged.
        """
        expected = mod.get_file_for_platform(platform)
        record = self._registry.get(mod.id)

        if record is not None and record.source is ArchiveSource.MANUAL:
            if not record.archive_path.is_file():
                return ArchiveResolution(None, ArchiveStatus.MANUAL_EXPECTED, record)
            return self._resolve_present(record.archive_path, record, expected)

        if expected is None or not expected.filename:
            return ArchiveResolution(None, ArchiveStatus.MANUAL_EXPECTED)

        path = download_dir / expected.filename
        if not path.is_file():
            status = (
                ArchiveStatus.MISSING
                if expected.has_download_url()
                else ArchiveStatus.MANUAL_EXPECTED
            )
            return ArchiveResolution(None, status)

        if record is not None and record.archive_path != path:
            record = None
        return self._resolve_present(path, record, expected)

    def _resolve_present(
        self, path: Path, record: ArchiveRecord | None, expected: ModFile | None
    ) -> ArchiveResolution:
        if record is None or not record.is_verified or not record.matches_disk():
            return ArchiveResolution(path, ArchiveStatus.UNKNOWN, record)
        return ArchiveResolution(path, self._status_of(record, expected), record)

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(
        self, mod: Mod, path: Path, source: ArchiveSource, platform: str
    ) -> ArchiveResolution:
        """Hash an archive and record the proof.

        An earlier explicit acceptance survives only for the very same content.
        """
        expected = mod.get_file_for_platform(platform)
        previous = self._registry.get(mod.id)
        accepted = (
            previous is not None
            and previous.accepted_mismatch
            and previous.matches_disk()
            and previous.archive_path == path
        )
        return self._hash_and_record(mod.id, path, source, expected, accepted)

    def associate_manual(self, mod: Mod, path: Path, platform: str) -> ArchiveResolution:
        """Associate a user archive with a mod after hashing it.

        A size or hash differing from the catalogue is not recorded: the
        previous association is left untouched and the returned record can be
        passed to `accept_mismatch` once the user has reviewed the difference.
        """
        expected = mod.get_file_for_platform(platform)
        sha256, size, mtime_ns = self._hash(path)
        record = ArchiveRecord.unverified(mod.id, path, ArchiveSource.MANUAL).with_hash(
            sha256, size, mtime_ns, self._expected_hash(expected), False
        )
        status = self._status_of(record, expected)

        if not status.is_usable:
            logger.warning("Manual archive for %s not associated: %s", mod.id, status.value)
            return ArchiveResolution(path, status, record)

        self._registry.put(record)
        logger.info("Manual archive associated with %s: %s (%s)", mod.id, path, status.value)
        return ArchiveResolution(path, status, record)

    def accept_mismatch(
        self, mod: Mod, record: ArchiveRecord, platform: str
    ) -> ArchiveResolution:
        """Record the user's explicit acceptance of a reviewed, differing archive.

        The acceptance only covers the content that was hashed: a file changed
        since then must be verified and reviewed again.
        """
        if not record.is_verified or not record.matches_disk():
            return ArchiveResolution(record.archive_path, ArchiveStatus.UNKNOWN, record)

        accepted = replace(record, accepted_mismatch=True)
        self._registry.put(accepted)
        logger.warning(
            "Archive accepted for %s despite catalogue difference: %s (sha256 %s, expected %s)",
            mod.id,
            accepted.path,
            accepted.sha256,
            accepted.expected_sha256,
        )
        status = self._status_of(accepted, mod.get_file_for_platform(platform))
        return ArchiveResolution(accepted.archive_path, status, accepted)

    def remove_manual(self, mod_id: str) -> None:
        record = self._registry.get(mod_id)
        if record is not None and record.source is ArchiveSource.MANUAL:
            self._registry.remove(mod_id)

    def _hash_and_record(
        self,
        mod_id: str,
        path: Path,
        source: ArchiveSource,
        expected: ModFile | None,
        accepted: bool,
    ) -> ArchiveResolution:
        if not path.is_file():
            return ArchiveResolution(None, ArchiveStatus.MISSING)

        sha256, size, mtime_ns = self._hash(path)
        record = ArchiveRecord.unverified(mod_id, path, source).with_hash(
            sha256, size, mtime_ns, self._expected_hash(expected), accepted
        )
        self._registry.put(record)
        return ArchiveResolution(path, self._status_of(record, expected), record)

    def _hash(self, path: Path) -> tuple[str, int, int]:
        """Hash a file and return the disk identity observed around the read.

        The stat is taken before reading so that a file modified meanwhile no
        longer matches the record at the next resolution.
        """
        stat = path.stat()
        sha256 = self._verifier.calculate_hash(path, HashAlgorithm.SHA256)
        return sha256, stat.st_size, stat.st_mtime_ns

    @staticmethod
    def _expected_hash(expected: ModFile | None) -> str | None:
        if expected is None or not expected.sha256:
            return None
        return expected.sha256.lower()

    @staticmethod
    def _status_of(record: ArchiveRecord, expected: ModFile | None) -> ArchiveStatus:
        if expected is not None and expected.size > 0 and record.size != expected.size:
            if not record.accepted_mismatch:
                return ArchiveStatus.INVALID_SIZE
        expected_hash = ArchiveService._expected_hash(expected)
        if expected_hash is None:
            return ArchiveStatus.NO_REFERENCE
        if record.sha256 == expected_hash:
            return ArchiveStatus.VALID
        if record.accepted_mismatch:
            return ArchiveStatus.MISMATCH_ACCEPTED
        return ArchiveStatus.INVALID_HASH
