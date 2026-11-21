"""Selective extraction: only .tp2 and .tra files for parsing."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil

from core.ArchiveExtractor import ArchiveExtractor
from core.validators.StructureValidator import StructureValidator

logger = logging.getLogger(__name__)


class SelectiveExtractor:
    """Extract only .tp2 and .tra files from mod archives for parsing."""

    ALLOWED_EXTENSIONS = {".tp2", ".tra", ".ini"}

    def extract_for_parsing(self, archive_path: Path, destination: Path, tp2_name: str) -> bool:
        """Extract only .tp2 and .tra files needed for parsing.

        Creates directory structure: destination/tp2_name/

        Args:
            archive_path: Path to archive file
            destination: Base extraction directory
            tp2_name: TP2 name (used for subdirectory)

        Returns:
            True if extraction successful and files found
        """
        mod_dir = destination

        # Skip if already exists
        if self._already_extracted(mod_dir, tp2_name):
            logger.info(f"Mod already extracted, skipping: {tp2_name}")
            return True

        # Create temporary extraction directory
        temp_dir = destination / f"_temp_{tp2_name}"

        try:
            # Extract full archive to temp
            logger.info(f"Extracting {archive_path.name} for parsing...")
            if not ArchiveExtractor.extract_archive(archive_path, temp_dir):
                logger.error(f"Failed to extract archive: {archive_path.name}")
                return False

            # Validate and fix structure
            valid, tp2_path = StructureValidator.validate_structure(temp_dir, tp2_name)

            if not valid:
                logger.info(f"Fixing structure for {tp2_name}...")
                fixed = StructureValidator.normalize_structure(temp_dir, tp2_name)

                if not fixed:
                    logger.warning(f"Could not fix structure for {tp2_name}")
                    return False

                logger.info(f"Structure fixed for {tp2_name}")

            # Move only .tp2 and .tra files to final destination
            files_moved = self._move_selective_files(temp_dir, mod_dir, tp2_name)

            if files_moved == 0:
                logger.warning(f"No .tp2 or .tra files found in {archive_path.name}")
                return False

            logger.info(f"Extracted {files_moved} files for {tp2_name}")

            return True

        finally:
            # Clean up temp directory
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _already_extracted(self, mod_dir: Path, tp2_name: str) -> bool:
        """Check if mod is already extracted by looking for .tp2 file.

        Args:
            mod_dir: Mod directory
            tp2_name: TP2 name to look for

        Returns:
            True if mod already extracted
        """
        if not mod_dir.exists():
            return False

        # Look for tp2 file (case-insensitive)
        for tp2_pattern in [f"{tp2_name}.tp2", f"setup-{tp2_name}.tp2"]:
            if list(mod_dir.rglob(tp2_pattern)):
                return True

        return False

    def _move_selective_files(self, source_dir: Path, target_dir: Path, tp2_name: str) -> int:
        """Move only .tp2 and .tra files from source to target, preserving structure.

        Args:
            source_dir: Source directory (temp extraction)
            target_dir: Target directory (final location)
            tp2_name: TP2 name for validation

        Returns:
            Number of files moved
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        files_moved = 0

        # Find all .tp2 and .tra files recursively
        for file_path in source_dir.rglob("*"):
            if not file_path.is_file():
                continue

            # Check if file should be kept
            if not self._should_keep_file(file_path):
                continue

            # Calculate relative path from source
            rel_path = file_path.relative_to(source_dir)
            target_path = target_dir / rel_path

            # Create parent directories
            target_path.parent.mkdir(parents=True, exist_ok=True)

            # Move file
            try:
                shutil.move(str(file_path), str(target_path))
                files_moved += 1
                logger.debug(f"Moved: {rel_path}")
            except Exception as e:
                logger.error(f"Failed to move {rel_path}: {e}")

        return files_moved

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

    def extract_multiple(
        self,
        archives: list[tuple[Path, str]],
        destination: Path,
        progress_callback: callable | None = None,
    ) -> dict[str, bool]:
        """Extract multiple archives for parsing.

        Args:
            archives: List of (archive_path, tp2_name) tuples
            destination: Base extraction directory
            progress_callback: Optional callback(current, total, tp2_name)

        Returns:
            Dictionary mapping tp2_name to success status
        """
        results = {}
        total = len(archives)

        for i, (archive_path, tp2_name) in enumerate(archives, 1):
            if progress_callback:
                progress_callback(i, total, tp2_name)

            try:
                success = self.extract_for_parsing(archive_path, destination, tp2_name)
                results[tp2_name] = success
            except Exception as e:
                logger.error(f"Error extracting {tp2_name}: {e}", exc_info=True)
                results[tp2_name] = False

        return results
