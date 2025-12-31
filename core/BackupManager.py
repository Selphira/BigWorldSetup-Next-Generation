from datetime import datetime
import json
import logging
from pathlib import Path
import re
import shutil
from typing import Callable

from core.BackupInfo import BackupInfo, BackupStatus
from core.TranslationManager import tr

logger = logging.getLogger(__name__)

# Type aliases for clarity
ProgressCallback = Callable[[str, int, int], None]
OperationResult = tuple[bool, str]  # (success, error_message)


class BackupManager:
    """Manages game backups: creation, restoration, validation."""

    # File exclusion patterns
    EXCLUDED_PATTERNS = [
        re.compile(p, re.IGNORECASE)
        for p in (
            r"^setup-.*\.exe$",
            r".*\.debug$",
            r".*\.tp2$",
            r".*\.bif$",
        )
    ]

    # Directory classifications
    ORIGINAL_GAME_DIRS = {
        "lang",
        "Manuals",
        "movies",
        "music",
        "override",
        "portraits",
        "scripts",
        "save",
        "mpsave",
    }

    PRESERVED_DIRS = {"portraits", "save", "mpsave", "data", "sod-dlc"}

    # Directory and file structure
    METADATA_FILE = "backup_metadata.json"
    GAME_FILES_DIR = "game_files"
    REMOVED_DIR_PREFIX = "removed"

    # Restoration progress phases (percentage)
    PROGRESS_PHASE_REMOVE = 5
    PROGRESS_PHASE_PREPARE = 10
    PROGRESS_PHASE_RESTORE = 80

    def __init__(self):
        self._backup_root: Path | None = None

    def set_backup_root(self, backup_root: Path | str) -> None:
        self._backup_root = Path(backup_root)

    # ========================================
    # Public API
    # ========================================

    def create_backup(
        self,
        game_code: str,
        game_name: str,
        game_path: Path,
        progress_callback: ProgressCallback,
    ) -> tuple[bool, BackupInfo | None, str]:
        """Create a backup of a game directory.

        Returns:
            (success, backup_info, error_message)
        """
        try:
            if not self._backup_root:
                return False, None, tr("backup_manager.error.no_backup_directory")

            if not game_path.exists():
                return (
                    False,
                    None,
                    tr(
                        "backup_manager.error.game_directory_not_found",
                        path=str(game_path),
                    ),
                )

            progress_callback(tr("backup_manager.progress.analyzing_files"), 0, 100)

            files = self._collect_backup_files(game_path)
            if not files:
                return False, None, tr("backup_manager.error.no_files_to_backup")

            total_size = sum(p.stat().st_size for p in files if p.is_file())
            self._check_free_space(total_size)

            backup_id = self._generate_backup_id(game_code)
            backup_dir = self._backup_root / backup_id
            game_files_dir = backup_dir / self.GAME_FILES_DIR
            game_files_dir.mkdir(parents=True)

            file_count = self._copy_files(files, game_path, game_files_dir, progress_callback)

            backup_info = BackupInfo(
                backup_id=backup_id,
                game_code=game_code,
                game_name=game_name,
                game_path=game_path,
                creation_date=datetime.now(),
                total_size=sum(
                    f.stat().st_size for f in game_files_dir.rglob("*") if f.is_file()
                ),
                file_count=file_count,
                is_modded=self.is_game_modded(game_path),
                status=BackupStatus.VALID,
            )

            self._save_metadata(backup_info)
            logger.info("Backup created: %s", backup_id)
            return True, backup_info, ""

        except RuntimeError as e:
            # RuntimeError contains translated messages from _check_free_space
            logger.exception("Backup creation failed")
            return False, None, str(e)
        except Exception as e:
            logger.exception("Backup creation failed")
            return False, None, tr("backup_manager.error.backup_creation_failed", error=str(e))

    def restore_backup(
        self,
        backup_id: str,
        game_path: Path,
        progress_callback: ProgressCallback,
    ) -> tuple[bool, Path | None, str]:
        """Restore a backup to a game directory.

        Returns:
            (success, removed_files_dir, error_message)
        """
        try:
            backup_info = self._load_and_validate_backup(backup_id)
            backup_dir = self._backup_root / backup_id
            game_files_dir = backup_dir / self.GAME_FILES_DIR

            removed_files_dir = self._create_removed_dir(backup_id)
            can_move = self._same_partition(game_path, self._backup_root)

            progress_callback(tr("backup_manager.progress.removing_files"), 0, 100)
            self._move_current_files(game_path, removed_files_dir, can_move)

            progress_callback(
                tr("backup_manager.progress.restoring_backup"),
                self.PROGRESS_PHASE_REMOVE + self.PROGRESS_PHASE_PREPARE,
                100,
            )

            self._restore_files(
                game_files_dir,
                game_path,
                backup_info.file_count,
                progress_callback,
            )

            progress_callback(
                tr("backup_manager.progress.restoring_data"),
                self.PROGRESS_PHASE_REMOVE
                + self.PROGRESS_PHASE_PREPARE
                + self.PROGRESS_PHASE_RESTORE,
                100,
            )

            self._restore_preserved_dirs(game_path, removed_files_dir, can_move)

            progress_callback(tr("backup_manager.progress.restoration_complete"), 100, 100)
            logger.info("Backup restored: %s", backup_id)
            return True, removed_files_dir, ""

        except RuntimeError as e:
            # RuntimeError contains translated messages from _load_and_validate_backup
            logger.exception("Restore failed")
            return False, None, str(e)
        except Exception as e:
            logger.exception("Restore failed")
            return False, None, tr("backup_manager.error.restore_failed", error=str(e))

    def delete_backup(self, backup_id: str) -> OperationResult:
        try:
            if not self._backup_root:
                return False, tr("backup_manager.error.no_backup_directory")

            backup_dir = self._backup_root / backup_id

            if not backup_dir.exists():
                return False, tr("backup_manager.error.backup_not_found", id=backup_id)

            shutil.rmtree(backup_dir)
            logger.info("Backup deleted: %s", backup_id)
            return True, ""

        except Exception as e:
            logger.error("Failed to delete backup %s: %s", backup_id, e)
            return False, tr("backup_manager.error.delete_failed", error=str(e))

    def list_backups(self, game_codes: list[str] | None = None) -> list[BackupInfo]:
        """List all backups, optionally filtered by game codes."""
        backups = []

        if not self._backup_root or not self._backup_root.exists():
            return backups

        for directory in self._backup_root.iterdir():
            # Skip non-directories and removed file directories
            if not directory.is_dir() or directory.name.startswith(
                f"{self.REMOVED_DIR_PREFIX}_"
            ):
                continue

            info = self.load_backup_info(directory.name)
            if not info:
                continue

            if game_codes and info.game_code not in game_codes:
                continue

            backups.append(info)

        return sorted(backups, key=lambda b: b.creation_date, reverse=True)

    def load_backup_info(self, backup_id: str) -> BackupInfo | None:
        try:
            if not self._backup_root:
                return None

            backup_dir = self._backup_root / backup_id
            metadata_path = backup_dir / self.METADATA_FILE

            if not metadata_path.exists():
                return None

            with open(metadata_path, encoding="utf-8") as fp:
                data = json.load(fp)

            return BackupInfo.from_dict(data)

        except Exception as e:
            logger.error("Failed to load backup metadata for %s: %s", backup_id, e)
            return None

    def validate_backup(self, backup_id: str) -> OperationResult:
        """Validate a backup's integrity by checking metadata and file count."""
        try:
            if not self._backup_root:
                return False, tr("backup_manager.error.no_backup_directory")

            backup_dir = self._backup_root / backup_id

            if not backup_dir.exists():
                return False, tr("backup_manager.error.backup_directory_not_found")

            if not (backup_dir / self.METADATA_FILE).exists():
                return False, tr("backup_manager.error.metadata_missing")

            game_files_dir = backup_dir / self.GAME_FILES_DIR
            if not game_files_dir.exists():
                return False, tr("backup_manager.error.game_files_missing")

            backup_info = self.load_backup_info(backup_id)
            if not backup_info:
                return False, tr("backup_manager.error.invalid_metadata")

            actual_count = sum(1 for f in game_files_dir.rglob("*") if f.is_file())
            if actual_count != backup_info.file_count:
                return (
                    False,
                    tr(
                        "backup_manager.error.file_count_mismatch",
                        expected=backup_info.file_count,
                        found=actual_count,
                    ),
                )

            return True, ""

        except Exception as e:
            logger.error("Failed to validate backup %s: %s", backup_id, e)
            return False, tr("backup_manager.error.validation_failed", error=str(e))

    def is_game_modded(self, game_path: Path) -> bool:
        """Check if a game has mods installed by examining weidu.log."""
        try:
            return bool((game_path / "weidu.log").read_text(errors="ignore").strip())
        except FileNotFoundError:
            return False

    def update_backup_metadata(
        self,
        backup_id: str,
        custom_name: str | None = None,
        notes: str | None = None,
    ) -> OperationResult:
        try:
            backup_info = self.load_backup_info(backup_id)
            if not backup_info:
                return False, tr("backup_manager.error.backup_not_found", id=backup_id)

            if custom_name is not None:
                backup_info.custom_name = custom_name
            if notes is not None:
                backup_info.notes = notes

            self._save_metadata(backup_info)
            return True, ""

        except Exception as e:
            logger.error("Failed to update backup metadata for %s: %s", backup_id, e)
            return False, tr("backup_manager.error.metadata_update_failed", error=str(e))

    def calculate_backup_size(self, game_path: Path) -> int:
        """Calculate total size of files to be backed up in bytes."""
        return sum(
            file.stat().st_size
            for file in self._collect_backup_files(game_path)
            if file.is_file()
        )

    def get_free_space(self) -> int:
        """Get free space in backup directory in bytes."""
        return shutil.disk_usage(self._backup_root).free if self._backup_root else 0

    # ========================================
    # Private Methods
    # ========================================

    def _generate_backup_id(self, game_code: str) -> str:
        """Generate unique backup ID: {game_code}_{timestamp}."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{game_code}_{timestamp}"

    def _collect_backup_files(self, game_path: Path) -> list[Path]:
        """Collect all files that should be backed up."""
        files: list[Path] = []

        for item in game_path.iterdir():
            if self._should_exclude(item.name):
                continue

            if item.is_dir():
                if item.name.lower() in self.ORIGINAL_GAME_DIRS:
                    files.append(item)
                    files.extend(item.rglob("*"))
            else:
                files.append(item)

        return files

    def _should_exclude(self, name: str) -> bool:
        return any(pattern.match(name) for pattern in self.EXCLUDED_PATTERNS)

    def _transfer(self, source: Path, destination: Path, *, move: bool) -> None:
        """Transfer a file or directory using move or copy."""
        if move:
            shutil.move(str(source), str(destination))
        else:
            shutil.copy2(source, destination)

    def _move_current_files(self, game_path: Path, removed_files_dir: Path, move: bool) -> None:
        """Move current game files to removed directory, preserving specific dirs."""
        for item in game_path.iterdir():
            if item.name.lower() in self.PRESERVED_DIRS:
                continue
            self._transfer(item, removed_files_dir / item.name, move=move)

    def _restore_preserved_dirs(
        self, game_path: Path, removed_files_dir: Path, move: bool
    ) -> None:
        """Restore preserved directories from removed files back to game path."""
        preserved_dir = removed_files_dir / "preserved"
        if not preserved_dir.exists():
            return

        for item in preserved_dir.iterdir():
            target = game_path / item.name
            if move:
                shutil.move(str(item), str(target))
            else:
                shutil.copytree(item, target, dirs_exist_ok=True)

    def _copy_files(
        self,
        files: list[Path],
        source_root: Path,
        destination_root: Path,
        progress_callback: ProgressCallback,
    ) -> int:
        """Copy files to backup directory with progress reporting."""
        total_files = sum(1 for f in files if f.is_file())
        copied_count = 0

        for file_path in files:
            relative_path = file_path.relative_to(source_root)
            destination_path = destination_root / relative_path

            if file_path.is_dir():
                destination_path.mkdir(parents=True, exist_ok=True)
            else:
                progress_callback(
                    tr("backup_manager.progress.copying_file", file=relative_path.name),
                    copied_count,
                    total_files,
                )
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(file_path, destination_path)
                copied_count += 1

        return copied_count

    def _restore_files(
        self,
        source_dir: Path,
        destination_dir: Path,
        total_files: int,
        progress_callback: ProgressCallback,
    ) -> None:
        """Restore files from backup to game directory with progress reporting."""
        restored_count = 0

        for file_path in source_dir.rglob("*"):
            if not file_path.is_file():
                continue

            relative_path = file_path.relative_to(source_dir)
            target_path = destination_dir / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)

            progress_percent = (
                self.PROGRESS_PHASE_REMOVE
                + self.PROGRESS_PHASE_PREPARE
                + int((restored_count / total_files) * self.PROGRESS_PHASE_RESTORE)
            )
            progress_callback(
                tr("backup_manager.progress.restoring_file", file=file_path.name),
                progress_percent,
                100,
            )

            self._transfer(file_path, target_path, move=False)
            restored_count += 1

    def _load_and_validate_backup(self, backup_id: str) -> BackupInfo:
        """Load backup info and validate it's ready for restoration."""
        if not self._backup_root:
            raise RuntimeError(tr("backup_manager.error.no_backup_directory"))

        backup_info = self.load_backup_info(backup_id)
        if not backup_info or backup_info.status != BackupStatus.VALID:
            raise RuntimeError(tr("backup_manager.error.invalid_backup"))

        return backup_info

    def _save_metadata(self, backup_info: BackupInfo) -> None:
        backup_dir = self._backup_root / backup_info.backup_id
        metadata_path = backup_dir / self.METADATA_FILE

        with open(metadata_path, "w", encoding="utf-8") as fp:
            json.dump(backup_info.to_dict(), fp, indent=2)

    def _create_removed_dir(self, backup_id: str) -> Path:
        """Create timestamped directory for files removed during restoration."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dir_name = f"{self.REMOVED_DIR_PREFIX}_{backup_id}_{timestamp}"

        removed_dir = self._backup_root / dir_name
        removed_dir.mkdir(parents=True, exist_ok=True)
        return removed_dir

    def _same_partition(self, path1: Path, path2: Path) -> bool:
        """Check if two paths are on the same filesystem for move optimization."""
        try:
            return path1.stat().st_dev == path2.stat().st_dev
        except Exception:
            return False

    def _check_free_space(self, required_bytes: int) -> None:
        """Ensure sufficient disk space with 50% safety margin."""
        free_bytes = shutil.disk_usage(self._backup_root).free
        if free_bytes < required_bytes * 1.5:
            raise RuntimeError(tr("backup_manager.error.insufficient_disk_space"))
