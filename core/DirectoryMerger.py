from enum import Enum
import logging
import os
from pathlib import Path
import shutil
from typing import Callable
import uuid

from constants import MERGE_TEMPORARY_PREFIX

logger = logging.getLogger(__name__)


# ============================================================================
# Enums and Configuration
# ============================================================================


class ConflictResolution(Enum):
    """Strategy for resolving file/directory conflicts during merge."""

    SKIP = "skip"  # Skip conflicting items, keep existing
    OVERWRITE = "overwrite"  # Replace existing items with new ones
    BACKUP = "backup"  # Backup existing before overwriting
    FAIL = "fail"  # Raise error on first conflict
    # Merge directories recursively and overwrite files without asking; every
    # replaced path is listed in MergeResult.overwritten_paths
    MERGE = "merge"


class MergeResult:
    """Result of a directory merge operation.

    Attributes:
        moved_files: Number of files successfully moved
        moved_dirs: Number of directories successfully moved
        skipped: Number of items skipped due to conflicts
        overwritten: Number of items that replaced existing ones
        backed_up: Number of items that were backed up
        errors: List of (path, error_message) tuples
        overwritten_paths: Destinations replaced, so that every collision is traceable
    """

    def __init__(self):
        self.moved_files = 0
        self.moved_dirs = 0
        self.skipped = 0
        self.overwritten = 0
        self.backed_up = 0
        self.errors: list[tuple[Path, str]] = []
        self.overwritten_paths: list[Path] = []

    @property
    def success(self) -> bool:
        """Check if merge completed without errors."""
        return len(self.errors) == 0

    @property
    def total_moved(self) -> int:
        """Total number of items moved."""
        return self.moved_files + self.moved_dirs

    def __str__(self) -> str:
        return (
            f"MergeResult(moved={self.total_moved}, skipped={self.skipped}, "
            f"overwritten={self.overwritten}, errors={len(self.errors)})"
        )


# ============================================================================
# Directory Merger
# ============================================================================


class DirectoryMerger:
    """Handles merging of directory contents with conflict resolution."""

    def __init__(
        self,
        conflict_resolution: ConflictResolution = ConflictResolution.MERGE,
        backup_suffix: str = ".bak",
        progress_callback: Callable[[Path, Path], None] | None = None,
    ):
        """Initialize directory merger.

        Args:
            conflict_resolution: Strategy for handling conflicts
            backup_suffix: Suffix to add to backed up files
            progress_callback: Optional callback(source, dest) called for each item
        """
        self.conflict_resolution = conflict_resolution
        self.backup_suffix = backup_suffix
        self.progress_callback = progress_callback

    def merge_directories(
        self, source: Path, destination: Path, remove_source: bool = True
    ) -> MergeResult:
        """Merge source directory into destination directory.

        This is the main entry point for directory merging operations.

        Args:
            source: Source directory to merge from
            destination: Destination directory to merge into
            remove_source: Whether to remove source directory after merge

        Returns:
            MergeResult with operation statistics and any errors

        Raises:
            ValueError: If source doesn't exist or isn't a directory
            PermissionError: If insufficient permissions (when using FAIL mode)
        """
        # Validate inputs
        if not source.exists():
            raise ValueError(f"Source directory does not exist: {source}")
        if not source.is_dir():
            raise ValueError(f"Source is not a directory: {source}")

        # Create destination if it doesn't exist
        destination.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"Merging {source} -> {destination} (strategy={self.conflict_resolution.value})"
        )

        result = MergeResult()

        try:
            # Perform the merge
            self._merge_recursive(source, destination, result)

            if remove_source and result.success:
                self._remove_source(source)

        except Exception as e:
            logger.error(f"Unexpected error during merge: {e}", exc_info=True)
            result.errors.append((source, str(e)))

        logger.info(f"Merge completed: {result}")
        return result

    @staticmethod
    def _remove_source(source: Path) -> None:
        """Remove the emptied source; a leftover does not undo a completed merge."""
        try:
            shutil.rmtree(source)
            logger.info(f"Removed source directory: {source}")
        except OSError as e:
            logger.error(f"Failed to remove source directory {source}: {e}")

    # ========================================================================
    # Merge Traversal
    # ========================================================================

    def _merge_recursive(self, source: Path, destination: Path, result: MergeResult) -> None:
        """Recursively merge source into destination.

        Args:
            source: Source directory
            destination: Destination directory
            result: MergeResult to update
        """
        try:
            items = list(source.iterdir())
        except PermissionError as e:
            logger.error(f"Cannot read source directory {source}: {e}")
            result.errors.append((source, f"Permission denied: {e}"))
            return

        for item in items:
            dest_item = destination / item.name

            try:
                if item.is_file():
                    self._merge_file(item, dest_item, result)
                elif item.is_dir():
                    self._merge_directory(item, dest_item, result)
                else:
                    # Symlinks, special files, etc.
                    logger.warning(f"Skipping non-regular item: {item}")
                    result.skipped += 1

            except Exception as e:
                logger.error(f"Error merging {item}: {e}", exc_info=True)
                result.errors.append((item, str(e)))

                if self.conflict_resolution == ConflictResolution.FAIL:
                    raise

    def _merge_file(self, source_file: Path, dest_file: Path, result: MergeResult) -> None:
        """Merge a single file.

        Args:
            source_file: Source file path
            dest_file: Destination file path
            result: MergeResult to update

        Raises:
            OSError: If the file cannot be placed; the destination is then unchanged
        """
        if self.progress_callback:
            self.progress_callback(source_file, dest_file)

        if not dest_file.exists():
            self._replace_file(source_file, dest_file)
            result.moved_files += 1
            logger.debug(f"Moved file: {source_file.name}")
            return

        if self.conflict_resolution == ConflictResolution.SKIP:
            logger.debug(f"Skipping existing file: {dest_file}")
            result.skipped += 1
            return

        if self.conflict_resolution == ConflictResolution.FAIL:
            raise FileExistsError(f"File already exists: {dest_file}")

        if dest_file.is_dir():
            raise IsADirectoryError(f"A directory exists where a file is expected: {dest_file}")

        if self.conflict_resolution == ConflictResolution.BACKUP:
            backup_path = self._create_backup(dest_file)
            result.backed_up += 1
            self._replace_file(source_file, dest_file)
            result.moved_files += 1
            logger.debug(f"Backed up file: {dest_file} -> {backup_path}")
            return

        self._overwrite_file(source_file, dest_file, result)

    def _merge_directory(self, source_dir: Path, dest_dir: Path, result: MergeResult) -> None:
        """Merge a directory.

        Args:
            source_dir: Source directory path
            dest_dir: Destination directory path
            result: MergeResult to update

        Raises:
            OSError: If the directory cannot be placed; the destination is then unchanged
        """
        if self.progress_callback:
            self.progress_callback(source_dir, dest_dir)

        if not dest_dir.exists():
            shutil.move(str(source_dir), str(dest_dir))
            result.moved_dirs += 1
            logger.debug(f"Moved directory: {source_dir.name}")
            return

        if self.conflict_resolution == ConflictResolution.SKIP:
            logger.debug(f"Skipping existing directory: {dest_dir}")
            result.skipped += 1
            return

        if self.conflict_resolution == ConflictResolution.FAIL:
            raise FileExistsError(f"Directory already exists: {dest_dir}")

        if not dest_dir.is_dir():
            raise NotADirectoryError(f"A file exists where a directory is expected: {dest_dir}")

        if self.conflict_resolution == ConflictResolution.MERGE:
            self._merge_recursive(source_dir, dest_dir, result)
            result.moved_dirs += 1
            return

        if self.conflict_resolution == ConflictResolution.BACKUP:
            backup_dir = self._backup_path(dest_dir)
            self._swap_directory(source_dir, dest_dir, backup_dir)
            result.backed_up += 1
            result.moved_dirs += 1
            logger.debug(f"Backed up directory: {dest_dir} -> {backup_dir}")
            return

        # OVERWRITE: the original is only deleted once the new directory is in place
        replaced_dir = self._temporary_sibling(dest_dir)
        self._swap_directory(source_dir, dest_dir, replaced_dir)
        result.moved_dirs += 1
        result.overwritten += 1
        result.overwritten_paths.append(dest_dir)
        logger.debug(f"Overwrote directory: {dest_dir}")
        shutil.rmtree(replaced_dir)

    # ========================================================================
    # Placement
    # ========================================================================

    def _overwrite_file(self, source_file: Path, dest_file: Path, result: MergeResult) -> None:
        """Replace an existing file, as MERGE and OVERWRITE share the same policy."""
        self._replace_file(source_file, dest_file)
        result.moved_files += 1
        result.overwritten += 1
        result.overwritten_paths.append(dest_file)
        logger.debug(f"Overwrote file ({self.conflict_resolution.value}): {dest_file}")

    def _replace_file(self, source_file: Path, dest_file: Path) -> None:
        """Move a file onto its destination without deleting the destination first.

        ``os.replace`` swaps the file atomically, so a failure leaves both the
        source and the existing destination untouched. Across volumes the file
        is first copied next to the destination, and the source is only
        removed once the replacement is done.
        """
        if self._is_same_volume(source_file, dest_file.parent):
            os.replace(source_file, dest_file)
            return

        temporary_file = self._temporary_sibling(dest_file)
        try:
            shutil.copy2(source_file, temporary_file)
            os.replace(temporary_file, dest_file)
        except OSError:
            temporary_file.unlink(missing_ok=True)
            raise
        source_file.unlink()

    @staticmethod
    def _swap_directory(source_dir: Path, dest_dir: Path, aside_dir: Path) -> None:
        """Rename the existing directory aside, then move the source in its place.

        Renaming first avoids ``shutil.move`` nesting the source inside the
        existing directory. If the move fails, the original is renamed back.
        """
        os.rename(dest_dir, aside_dir)
        try:
            shutil.move(str(source_dir), str(dest_dir))
        except OSError:
            if dest_dir.exists():
                logger.error(f"Partial move into {dest_dir}; original kept at {aside_dir}")
            else:
                os.rename(aside_dir, dest_dir)
            raise

    @staticmethod
    def _is_same_volume(path: Path, directory: Path) -> bool:
        """Tell whether a rename from path into directory stays on one volume."""
        return path.stat().st_dev == directory.stat().st_dev

    @staticmethod
    def _temporary_sibling(path: Path) -> Path:
        """Return an unused name next to path, on the same volume."""
        return path.with_name(f"{MERGE_TEMPORARY_PREFIX}{path.name}-{uuid.uuid4().hex}")

    # ========================================================================
    # Backup
    # ========================================================================

    def _create_backup(self, file_path: Path) -> Path:
        """Copy a file next to itself before it is replaced.

        Args:
            file_path: File to back up

        Returns:
            Path to the backup copy

        Raises:
            OSError: If the copy fails; no partial backup is left behind
        """
        backup_path = self._backup_path(file_path)
        try:
            shutil.copy2(file_path, backup_path)
        except OSError as e:
            logger.error(f"Failed to create backup of {file_path}: {e}")
            backup_path.unlink(missing_ok=True)
            raise
        return backup_path

    def _backup_path(self, path: Path) -> Path:
        """Return the first unused backup name for path."""
        backup_path = path.parent / f"{path.name}{self.backup_suffix}"

        counter = 1
        while backup_path.exists():
            backup_path = path.parent / f"{path.name}{self.backup_suffix}.{counter}"
            counter += 1

        return backup_path
