"""Proof that a mod was prepared from a given archive."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
import logging
from pathlib import Path

from constants import EXTRACT_DIR, EXTRACT_MANIFEST_DIR, EXTRACT_MANIFEST_FORMAT
from core.ArchiveRegistry import ArchiveRecord
from core.atomic_io import write_json_atomic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractionManifest:
    """Archive identity recorded once extraction, normalization and merge succeeded.

    Attributes:
        mod_id: Mod whose archive was extracted
        archive_sha256: Hash of the extracted archive
        archive_mtime_ns: Modification time of the archive when it was hashed
    """

    mod_id: str
    archive_sha256: str
    archive_mtime_ns: int

    @staticmethod
    def path_for(game_dir: Path, mod_id: str) -> Path:
        """Location of the manifest of a mod prepared in a game folder."""
        return game_dir / EXTRACT_DIR / EXTRACT_MANIFEST_DIR / f"{mod_id}.json"

    @classmethod
    def from_record(cls, record: ArchiveRecord) -> ExtractionManifest:
        return cls(record.mod_id, record.sha256 or "", record.mtime_ns or 0)

    @classmethod
    def load(cls, path: Path) -> ExtractionManifest | None:
        """Read a manifest; a missing, unreadable or foreign file proves nothing."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("format_version") != EXTRACT_MANIFEST_FORMAT:
                return None
            return cls(
                str(data["mod_id"]), str(data["archive_sha256"]), int(data["archive_mtime_ns"])
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            logger.warning("Ignoring unreadable extraction manifest %s: %s", path, error)
            return None

    @classmethod
    def load_for_owners(
        cls, game_dir: Path, owners: Iterable[str]
    ) -> dict[str, ExtractionManifest | None]:
        """Read once the manifest of each distinct mod prepared in a game folder.

        The table belongs to a single verification: a preparation may change
        between two of them, so it is never kept.
        """
        return {
            owner: cls.load(cls.path_for(game_dir, owner)) for owner in dict.fromkeys(owners)
        }

    def save(self, path: Path) -> None:
        write_json_atomic(
            path,
            {
                "format_version": EXTRACT_MANIFEST_FORMAT,
                "mod_id": self.mod_id,
                "archive_sha256": self.archive_sha256,
                "archive_mtime_ns": self.archive_mtime_ns,
            },
        )

    @staticmethod
    def discard(path: Path) -> None:
        """Invalidate a preparation before it is redone."""
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            logger.warning("Could not remove extraction manifest %s: %s", path, error)

    def matches(self, archive_path: Path | None, record: ArchiveRecord | None) -> bool:
        """Tell whether the prepared content still comes from the current archive.

        The modification time is compared with the file on disk, and the hash
        with the registry whenever it holds a current proof of that file. An
        archive deleted after preparation leaves the manifest as the only proof.
        """
        if archive_path is None or not archive_path.is_file():
            return True
        if archive_path.stat().st_mtime_ns != self.archive_mtime_ns:
            return False
        if (
            record is not None
            and record.archive_path == archive_path
            and record.is_verified
            and record.matches_disk()
        ):
            return record.sha256 == self.archive_sha256
        return True
