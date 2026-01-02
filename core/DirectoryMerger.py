from enum import Enum
import logging
from pathlib import Path
import shutil
from typing import Callable

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
    MERGE = "merge"  # Merge directories recursively, overwrite files


class MergeResult:
    """Result of a directory merge operation.

    Attributes:
        moved_files: Number of files successfully moved
        moved_dirs: Number of directories successfully moved
        skipped: Number of items skipped due to conflicts
        overwritten: Number of items that replaced existing ones
        backed_up: Number of items that were backed up
        errors: List of (path, error_message) tuples
    """

    def __init__(self):
        self.moved_files = 0
        self.moved_dirs = 0
        self.skipped = 0
        self.overwritten = 0
        self.backed_up = 0
        self.errors: list[tuple[Path, str]] = []

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

            # Remove source directory if requested and merge was successful
            if remove_source and result.success:
                self._remove_directory_tree(source)
                logger.info(f"Removed source directory: {source}")

        except Exception as e:
            logger.error(f"Unexpected error during merge: {e}", exc_info=True)
            result.errors.append((source, str(e)))

        logger.info(f"Merge completed: {result}")
        return result

    # ========================================================================
    # Internal Methods
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
        """
        # Call progress callback if provided
        if self.progress_callback:
            self.progress_callback(source_file, dest_file)

        # Handle conflict if destination exists
        if dest_file.exists():
            if not self._handle_file_conflict(source_file, dest_file, result):
                return  # Conflict not resolved, skip this file

        # Move/copy the file
        try:
            shutil.move(str(source_file), str(dest_file))
            result.moved_files += 1
            logger.debug(f"Moved file: {source_file.name}")

        except (PermissionError, OSError) as e:
            logger.error(f"Failed to move file {source_file}: {e}")
            result.errors.append((source_file, str(e)))

            if self.conflict_resolution == ConflictResolution.FAIL:
                raise

    def _merge_directory(self, source_dir: Path, dest_dir: Path, result: MergeResult) -> None:
        """Merge a directory.

        Args:
            source_dir: Source directory path
            dest_dir: Destination directory path
            result: MergeResult to update
        """
        # Call progress callback if provided
        if self.progress_callback:
            self.progress_callback(source_dir, dest_dir)

        # If destination doesn't exist, simple move
        if not dest_dir.exists():
            try:
                shutil.move(str(source_dir), str(dest_dir))
                result.moved_dirs += 1
                logger.debug(f"Moved directory: {source_dir.name}")
                return
            except (PermissionError, OSError) as e:
                logger.error(f"Failed to move directory {source_dir}: {e}")
                result.errors.append((source_dir, str(e)))

                if self.conflict_resolution == ConflictResolution.FAIL:
                    raise
                return

        # Destination exists - handle based on strategy
        if self.conflict_resolution == ConflictResolution.SKIP:
            logger.debug(f"Skipping existing directory: {dest_dir}")
            result.skipped += 1
            return

        elif self.conflict_resolution == ConflictResolution.OVERWRITE:
            # Remove existing and move new
            self._remove_directory_tree(dest_dir)
            shutil.move(str(source_dir), str(dest_dir))
            result.moved_dirs += 1
            result.overwritten += 1
            logger.debug(f"Overwrote directory: {dest_dir}")
            return

        elif self.conflict_resolution == ConflictResolution.BACKUP:
            # Backup existing and move new
            backup_path = self._create_backup(dest_dir)
            if backup_path:
                result.backed_up += 1
            shutil.move(str(source_dir), str(dest_dir))
            result.moved_dirs += 1
            logger.debug(f"Backed up and moved directory: {dest_dir}")
            return

        elif self.conflict_resolution == ConflictResolution.MERGE:
            # Recursively merge contents
            self._merge_recursive(source_dir, dest_dir, result)
            result.moved_dirs += 1
            return

        elif self.conflict_resolution == ConflictResolution.FAIL:
            raise FileExistsError(f"Directory already exists: {dest_dir}")

    def _handle_file_conflict(
        self, source_file: Path, dest_file: Path, result: MergeResult
    ) -> bool:
        """Handle conflict when destination file exists.

        Args:
            source_file: Source file path
            dest_file: Destination file path
            result: MergeResult to update

        Returns:
            True if conflict was resolved and file can be moved, False to skip
        """
        if self.conflict_resolution == ConflictResolution.MERGE:
            logger.debug(f"Overwriting file (merge mode): {dest_file}")
            dest_file.unlink()
            result.overwritten += 1
            return True

        elif self.conflict_resolution == ConflictResolution.SKIP:
            logger.debug(f"Skipping existing file: {dest_file}")
            result.skipped += 1
            return False

        elif self.conflict_resolution == ConflictResolution.OVERWRITE:
            logger.debug(f"Overwriting file: {dest_file}")
            dest_file.unlink()
            result.overwritten += 1
            return True

        elif self.conflict_resolution == ConflictResolution.BACKUP:
            backup_path = self._create_backup(dest_file)
            if backup_path:
                result.backed_up += 1
                logger.debug(f"Backed up file: {dest_file} -> {backup_path}")
            return True

        elif self.conflict_resolution == ConflictResolution.FAIL:
            raise FileExistsError(f"File already exists: {dest_file}")

        return False

    def _create_backup(self, path: Path) -> Path | None:
        """Create a backup of a file or directory.

        Args:
            path: Path to backup

        Returns:
            Path to backup, or None if backup failed
        """
        backup_path = path.parent / f"{path.name}{self.backup_suffix}"

        # Find unique backup name if needed
        counter = 1
        while backup_path.exists():
            backup_path = path.parent / f"{path.name}{self.backup_suffix}.{counter}"
            counter += 1

        try:
            if path.is_file():
                shutil.copy2(str(path), str(backup_path))
            elif path.is_dir():
                shutil.copytree(str(path), str(backup_path))

            logger.debug(f"Created backup: {backup_path}")
            return backup_path

        except (PermissionError, OSError) as e:
            logger.error(f"Failed to create backup of {path}: {e}")
            return None

    @staticmethod
    def _remove_directory_tree(path: Path) -> None:
        """Safely remove a directory tree.

        Args:
            path: Directory to remove
        """
        try:
            shutil.rmtree(str(path))
        except (PermissionError, OSError) as e:
            logger.error(f"Failed to remove directory {path}: {e}")
            # Not critical, continue
