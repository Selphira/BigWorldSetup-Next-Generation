"""Selective extraction: only the .tp2, .tra and .ini files needed for parsing."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from pathlib import Path
import shutil

from core.ArchiveExtractor import ArchiveExtractor
from core.validators.StructureValidator import StructureValidator

logger = logging.getLogger(__name__)

# Stamps recording which archive each extraction comes from:
# <destination>/.mod_sync/<tp2>.json = {"sha256": ..., "files": [relative paths]}
EXTRACTION_STAMPS_DIR = ".mod_sync"


@dataclass(frozen=True, slots=True)
class ExtractionStamp:
    """Archive an extraction comes from, and the files it produced."""

    sha256: str
    files: tuple[str, ...]


class SelectiveExtractor:
    """Extract only .tp2 and .tra files from mod archives for parsing."""

    ALLOWED_EXTENSIONS = {".tp2", ".tra", ".ini"}

    def extract_for_parsing(
        self, archive_path: Path, destination: Path, tp2_name: str, archive_sha256: str
    ) -> bool:
        """Make the extraction of a mod match its current archive.

        An extraction of the same archive is kept; any other one is removed
        before extracting, so files of a previous archive are never read.

        Args:
            archive_path: Path to archive file
            destination: Base extraction directory
            tp2_name: TP2 name of the mod
            archive_sha256: Hash of the archive, recorded with the extraction

        Returns:
            True if the extraction now matches the archive
        """
        stamp = self.read_stamp(destination, tp2_name)
        if stamp and stamp.sha256 == archive_sha256 and self._files_present(destination, stamp):
            logger.debug(f"Extraction up to date: {tp2_name}")
            return True

        self._remove_extraction(destination, tp2_name, stamp)

        temp_dir = destination / f"_temp_{tp2_name}"
        try:
            files = self._extract_files(archive_path, temp_dir, destination, tp2_name)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        if not files:
            return False

        self._write_stamp(destination, tp2_name, ExtractionStamp(archive_sha256, files))
        logger.info(f"Extracted {len(files)} files for {tp2_name}")
        return True

    def read_stamp(self, destination: Path, tp2_name: str) -> ExtractionStamp | None:
        """Return the archive the extraction of a mod comes from, if recorded."""
        try:
            data = json.loads(self._stamp_path(destination, tp2_name).read_text(encoding="utf-8"))
            return ExtractionStamp(str(data["sha256"]), tuple(data["files"]))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as e:
            logger.warning(f"Unreadable extraction stamp for {tp2_name}: {e}")
            return None

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _extract_files(
        self, archive_path: Path, temp_dir: Path, destination: Path, tp2_name: str
    ) -> tuple[str, ...]:
        """Extract an archive and move its parsing files to the destination.

        Returns:
            Paths of the moved files, relative to the destination
        """
        logger.info(f"Extracting {archive_path.name} for parsing...")
        if not ArchiveExtractor.extract_archive(archive_path, temp_dir):
            logger.error(f"Failed to extract archive: {archive_path.name}")
            return ()

        valid, _ = StructureValidator.validate_structure(temp_dir, tp2_name)
        if not valid:
            logger.info(f"Fixing structure for {tp2_name}...")
            if not StructureValidator.normalize_structure(temp_dir, tp2_name):
                logger.warning(f"Could not fix structure for {tp2_name}")
                return ()

        files = self._move_selective_files(temp_dir, destination, tp2_name)
        if not files:
            logger.warning(f"No .tp2 or .tra files found in {archive_path.name}")
        return files

    def _move_selective_files(self, source_dir: Path, target_dir: Path, tp2_name: str) -> tuple[str, ...]:
        """Move only the parsing files from source to target, preserving structure.

        Args:
            source_dir: Source directory (temp extraction)
            target_dir: Target directory (final location)
            tp2_name: TP2 name of the mod

        Returns:
            Paths of the moved files, relative to the target
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        moved_files: list[str] = []

        for file_path in source_dir.rglob("*"):
            if not file_path.is_file() or not self._should_keep_file(file_path):
                continue

            rel_path = file_path.relative_to(source_dir)
            target_path = target_dir / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)

            try:
                shutil.move(str(file_path), str(target_path))
                moved_files.append(rel_path.as_posix())
                logger.debug(f"Moved: {rel_path}")
            except Exception as e:
                logger.error(f"Failed to move {rel_path} for {tp2_name}: {e}")

        return tuple(moved_files)

    def _should_keep_file(self, file_path: Path) -> bool:
        """Determine if a file should be kept.

        Args:
            file_path: Path to file

        Returns:
            True if file should be kept
        """
        ext = file_path.suffix.lower()

        if ext in self.ALLOWED_EXTENSIONS:
            return True

        return False

    # ------------------------------------------------------------------
    # Previous extraction
    # ------------------------------------------------------------------

    def _remove_extraction(self, destination: Path, tp2_name: str, stamp: ExtractionStamp | None) -> None:
        """Remove the files of a previous extraction of a mod.

        Without stamp, the extraction predates them: the usual TP2 locations
        of the mod are removed instead.
        """
        if stamp:
            for relative_path in stamp.files:
                file_path = destination / relative_path
                if file_path.resolve().is_relative_to(destination.resolve()):
                    file_path.unlink(missing_ok=True)
                    self._prune_empty_dirs(file_path.parent, destination)
            self._stamp_path(destination, tp2_name).unlink(missing_ok=True)
            return

        mod_dir = destination / tp2_name
        if mod_dir.is_dir():
            logger.info(f"Removing unstamped extraction of {tp2_name}")
            shutil.rmtree(mod_dir)
        for root_tp2 in (f"{tp2_name}.tp2", f"setup-{tp2_name}.tp2"):
            (destination / root_tp2).unlink(missing_ok=True)

    @staticmethod
    def _prune_empty_dirs(directory: Path, destination: Path) -> None:
        while directory != destination and directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            directory = directory.parent

    @staticmethod
    def _files_present(destination: Path, stamp: ExtractionStamp) -> bool:
        return bool(stamp.files) and all((destination / path).is_file() for path in stamp.files)

    def _write_stamp(self, destination: Path, tp2_name: str, stamp: ExtractionStamp) -> None:
        stamp_path = self._stamp_path(destination, tp2_name)
        stamp_path.parent.mkdir(parents=True, exist_ok=True)
        stamp_path.write_text(
            json.dumps({"sha256": stamp.sha256, "files": list(stamp.files)}), encoding="utf-8"
        )

    @staticmethod
    def _stamp_path(destination: Path, tp2_name: str) -> Path:
        return destination / EXTRACTION_STAMPS_DIR / f"{tp2_name.lower()}.json"
