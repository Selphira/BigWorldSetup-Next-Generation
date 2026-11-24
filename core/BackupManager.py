"""Full verified game snapshots with recoverable restoration transactions."""

from datetime import datetime
import json
import logging
from pathlib import Path
import shutil
import tempfile
from typing import Callable
from uuid import uuid4

from constants import (
    BACKUP_MANIFEST_FILE,
    BACKUP_SPACE_MARGIN,
    RESTORE_JOURNAL_FILE,
)
from core.atomic_io import write_json_atomic
from core.BackupInfo import BackupInfo, BackupStatus
from core.BackupManifest import BackupManifest
from core.BackupSnapshot import BackupSnapshot
from core.PathSafety import PathSafety
from core.RestoreTransaction import RestorePhase, RestoreTransaction
from core.TranslationManager import tr
from core.WeiDULogParser import WeiDULogParser

logger = logging.getLogger(__name__)

# Type aliases for clarity
ProgressCallback = Callable[[str, int, int], None]
OperationResult = tuple[bool, str]  # (success, error_message)


class BackupManager:
    """Manages game backups: creation, restoration, validation."""

    SPACE_MARGIN: float = BACKUP_SPACE_MARGIN

    MANIFEST_FILE = BACKUP_MANIFEST_FILE
    JOURNAL_FILE = RESTORE_JOURNAL_FILE

    # Directory and file structure
    METADATA_FILE = "backup_metadata.json"
    GAME_FILES_DIR = "game_files"
    REMOVED_DIR_PREFIX = "removed"

    def __init__(self) -> None:
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
        *,
        custom_name: str = "",
        notes: str = "",
    ) -> tuple[bool, BackupInfo | None, str]:
        """Create a backup of a game directory.

        Args:
            game_code: Game code
            game_name: Game name
            game_path: Game directory to back up
            progress_callback: Receives the progress message, current and total
            custom_name: Name given by the user, the backup ID being shown when empty
            notes: User notes

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

            self._check_paths(game_path)
            self._backup_root.mkdir(parents=True, exist_ok=True)
            progress_callback(tr("backup_manager.progress.analyzing_files"), 0, 100)

            expected = BackupSnapshot(game_path).inventory()
            if not expected["files"]:
                return False, None, tr("backup_manager.error.no_files_to_backup")

            self._check_free_space(BackupSnapshot.byte_size(expected))

            backup_id = self._generate_backup_id(game_code)
            backup_dir = self._backup_directory(backup_id)
            candidate = Path(tempfile.mkdtemp(prefix=".candidate_", dir=self._backup_root))
            game_files_dir = candidate / self.GAME_FILES_DIR

            game_files_dir.mkdir()
            file_count = self._copy_files(
                self._collect_backup_files(game_path),
                game_path,
                game_files_dir,
                progress_callback,
            )

            BackupSnapshot(game_files_dir).verify(expected)
            BackupSnapshot(game_path).verify(expected)

            write_json_atomic(
                candidate / self.MANIFEST_FILE,
                {
                    "version": BackupSnapshot.MANIFEST_VERSION,
                    "contract": BackupSnapshot.SNAPSHOT_CONTRACT,
                    "game_code": game_code,
                    "game_path": str(game_path.resolve()),
                    "tree": expected,
                },
            )

            backup_info = BackupInfo(
                backup_id=backup_id,
                game_code=game_code,
                game_name=game_name,
                game_path=game_path.resolve(),
                creation_date=datetime.now(),
                total_size=sum(
                    f.stat().st_size for f in game_files_dir.rglob("*") if f.is_file()
                ),
                file_count=file_count,
                is_modded=self.is_game_modded(game_path),
                custom_name=custom_name,
                notes=notes,
                status=BackupStatus.VALID,
            )

            write_json_atomic(candidate / self.METADATA_FILE, backup_info.to_dict())
            candidate.rename(backup_dir)
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
        transaction: RestoreTransaction | None = None

        try:
            transaction = self._prepare_restore(backup_id, game_path)
            transaction.save_phase(RestorePhase.PREPARING)

            self._copy_backup_for_restore(transaction, progress_callback)
            self._preserve_current_user_files(transaction)
            self._verify_prepared_game(transaction)

            self._save_current_game(transaction)
            self._activate_prepared_game(transaction)
            self._verify_restored_game(transaction)

            progress_callback(tr("backup_manager.progress.restoration_complete"), 100, 100)
            logger.info("Backup restored: %s; recovery: %s", backup_id, transaction.directory)
            return True, transaction.directory, ""

        except Exception as error:
            return self._restore_failure(transaction, error)

    def recover_restore(self, transaction: Path) -> OperationResult:
        """Restore the pre-operation tree from a journal, retaining all recovery data."""
        try:
            PathSafety.reject_links(transaction)
            journal = json.loads((transaction / self.JOURNAL_FILE).read_text(encoding="utf-8"))
            game_dir = Path(journal["target"])
            recovery_dir = transaction / "original"
            rollback_dir = transaction / f"rollback_{uuid4().hex}"
            original_manifest = BackupManifest.from_dict(journal["original"])

            if (
                journal["version"] != BackupSnapshot.MANIFEST_VERSION
                or not game_dir.is_absolute()
                or transaction.resolve().parent != game_dir.parent.resolve()
                or not transaction.name.startswith(".bws_restore_")
            ):
                raise RuntimeError(tr("backup_manager.error.unsafe_path"))
            PathSafety.independent_paths(game_dir, transaction)

            if not recovery_dir.exists():
                BackupSnapshot(game_dir).verify(original_manifest.to_dict())
                return True, ""

            BackupSnapshot(recovery_dir).verify(original_manifest.to_dict())
            self._check_space(game_dir.parent, original_manifest.byte_size)

            shutil.copytree(recovery_dir, rollback_dir)
            BackupSnapshot(rollback_dir).verify(original_manifest.to_dict())

            if game_dir.exists():
                BackupSnapshot(game_dir).inventory()
                game_dir.rename(transaction / f"interrupted_{uuid4().hex}")

            rollback_dir.rename(game_dir)
            BackupSnapshot(game_dir).verify(original_manifest.to_dict())

            journal["phase"] = "rolled_back"
            write_json_atomic(transaction / self.JOURNAL_FILE, journal)
            return True, ""

        except Exception as error:
            logger.exception("Recovery failed: %s", transaction)
            return False, tr("backup_manager.error.restore_failed", error=str(error))

    def delete_backup(self, backup_id: str) -> OperationResult:
        try:
            if not self._backup_root:
                return False, tr("backup_manager.error.no_backup_directory")

            backup_dir = self._backup_directory(backup_id)

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
            if (
                directory.name.startswith(".")
                or not directory.is_dir()
                or directory.name.startswith(f"{self.REMOVED_DIR_PREFIX}_")
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

            backup_dir = self._backup_directory(backup_id)
            metadata_path = backup_dir / self.METADATA_FILE

            if not metadata_path.exists():
                return None

            with open(metadata_path, encoding="utf-8") as fp:
                data = json.load(fp)

            info = BackupInfo.from_dict(data)
            if info.backup_id != backup_id:
                return None
            if not (backup_dir / self.MANIFEST_FILE).exists():
                info.status = BackupStatus.INCOMPLETE
            return info

        except Exception as e:
            logger.error("Failed to load backup metadata for %s: %s", backup_id, e)
            return None

    def validate_backup(self, backup_id: str) -> OperationResult:
        """Verify snapshot identity, file paths, sizes, hashes and empty directories."""
        try:
            if not self._backup_root:
                return False, tr("backup_manager.error.no_backup_directory")

            backup_dir = self._backup_directory(backup_id)

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

            if backup_info.status != BackupStatus.VALID:
                raise RuntimeError(tr("backup_manager.error.invalid_backup"))
            manifest = json.loads((backup_dir / self.MANIFEST_FILE).read_text(encoding="utf-8"))
            if (
                manifest["version"] != BackupSnapshot.MANIFEST_VERSION
                or manifest["contract"] != BackupSnapshot.SNAPSHOT_CONTRACT
                or manifest["game_code"] != backup_info.game_code
                or Path(manifest["game_path"]) != backup_info.game_path.resolve()
                or backup_info.file_count != len(manifest["tree"]["files"])
                or backup_info.total_size != BackupSnapshot.byte_size(manifest["tree"])
            ):
                raise RuntimeError(tr("backup_manager.error.invalid_metadata"))
            BackupSnapshot(game_files_dir).verify(manifest["tree"])

            return True, ""

        except Exception as e:
            logger.error("Failed to validate backup %s: %s", backup_id, e)
            return False, tr("backup_manager.error.validation_failed", error=str(e))

    def is_game_modded(self, game_path: Path) -> bool:
        """Check if a game has mods installed by examining weidu.log."""
        try:
            log = game_path / "WeiDU.log"
            if not log.is_file():
                log = game_path / "weidu.log"
            return any(WeiDULogParser().iter_entries(log))
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
    # Restoration steps
    # ========================================

    def _prepare_restore(self, backup_id: str, game_path: Path) -> RestoreTransaction:
        self._check_paths(game_path)

        game_dir = game_path.resolve()
        backup_info = self._load_and_validate_backup(backup_id)
        if game_dir != backup_info.game_path.resolve():
            raise RuntimeError(tr("backup_manager.error.different_target"))

        backup_dir = self._backup_directory(backup_id)
        backup_files_dir = backup_dir / self.GAME_FILES_DIR
        manifest_path = backup_dir / self.MANIFEST_FILE

        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        backup_manifest = BackupManifest.from_dict(manifest_data["tree"])
        current_manifest = BackupManifest.from_dict(BackupSnapshot(game_dir).inventory())
        restored_manifest = backup_manifest.with_current_user_files(current_manifest)

        required_space = backup_manifest.byte_size + current_manifest.byte_size
        self._check_space(game_dir.parent, required_space)

        # Staging beside the game keeps both renames on the same volume.
        transaction_dir = Path(tempfile.mkdtemp(prefix=".bws_restore_", dir=game_dir.parent))
        return RestoreTransaction(
            backup_id=backup_id,
            game_dir=game_dir,
            backup_files_dir=backup_files_dir,
            directory=transaction_dir,
            current_manifest=current_manifest,
            backup_manifest=backup_manifest,
            restored_manifest=restored_manifest,
        )

    def _copy_backup_for_restore(
        self, transaction: RestoreTransaction, progress_callback: ProgressCallback
    ) -> None:
        source_dir = transaction.backup_files_dir
        prepared_dir = transaction.prepared_game_dir

        progress_callback(tr("backup_manager.progress.restoring_backup"), 0, 100)
        prepared_dir.mkdir()
        self._copy_files(
            self._collect_backup_files(source_dir), source_dir, prepared_dir, progress_callback
        )
        BackupSnapshot(prepared_dir).verify(transaction.backup_manifest.to_dict())

    def _preserve_current_user_files(self, transaction: RestoreTransaction) -> None:
        prepared_dir = transaction.prepared_game_dir

        # Replace entire user directories so deleted saves do not reappear.
        for item in list(prepared_dir.iterdir()):
            if item.name.lower() not in BackupSnapshot.USER_DIRECTORIES:
                continue

            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        for item in transaction.game_dir.iterdir():
            if item.name.lower() in BackupSnapshot.USER_DIRECTORIES:
                self._transfer(item, prepared_dir / item.name, move=False)

    def _verify_prepared_game(self, transaction: RestoreTransaction) -> None:
        BackupSnapshot(transaction.prepared_game_dir).verify(
            transaction.restored_manifest.to_dict()
        )
        BackupSnapshot(transaction.game_dir).verify(transaction.current_manifest.to_dict())
        transaction.save_phase(RestorePhase.PREPARED)

    def _save_current_game(self, transaction: RestoreTransaction) -> None:
        try:
            transaction.game_dir.rename(transaction.recovery_dir)
        except PermissionError as error:
            raise PermissionError(
                tr(
                    "backup_manager.error.original_move_denied",
                    game=str(transaction.game_dir),
                    error=str(error),
                )
            ) from error
        transaction.save_phase(RestorePhase.ORIGINAL_SAVED)

    def _activate_prepared_game(self, transaction: RestoreTransaction) -> None:
        transaction.prepared_game_dir.rename(transaction.game_dir)

    def _verify_restored_game(self, transaction: RestoreTransaction) -> None:
        BackupSnapshot(transaction.game_dir).verify(transaction.restored_manifest.to_dict())
        transaction.save_phase(RestorePhase.COMPLETE)

    def _restore_failure(
        self, transaction: RestoreTransaction | None, error: Exception
    ) -> tuple[bool, Path | None, str]:
        """Describe the failure, what became of the game folder and what to do next."""
        recovery_dir = transaction.directory if transaction else None
        logger.exception("Restore failed; recovery directory: %s", recovery_dir)
        message = tr("backup_manager.error.restore_failed", error=str(error))

        # The game folder is only altered once it has been moved aside as a whole.
        if transaction is not None and transaction.recovery_dir.exists():
            message += "\n\n" + tr(
                "backup_manager.error.restore_game_moved",
                game=str(transaction.game_dir),
                original=str(transaction.recovery_dir),
            )
        else:
            message += "\n\n" + tr("backup_manager.error.restore_game_untouched")

        if recovery_dir is not None:
            message += "\n\n" + tr(
                "backup_manager.error.recovery_available", path=str(recovery_dir)
            )

        return False, recovery_dir, message

    # ========================================
    # Filesystem and metadata helpers
    # ========================================

    def _backup_directory(self, backup_id: str) -> Path:
        if not self._backup_root:
            raise RuntimeError(tr("backup_manager.error.no_backup_directory"))
        if (
            not backup_id
            or backup_id in {".", ".."}
            or any(char in backup_id for char in "/\\:")
            or backup_id.startswith(".")
            or backup_id.endswith((".", " "))
        ):
            raise RuntimeError(tr("backup_manager.error.unsafe_path"))
        destination = self._backup_root / backup_id
        PathSafety.reject_links(destination)
        if destination.resolve().parent != self._backup_root.resolve():
            raise RuntimeError(tr("backup_manager.error.unsafe_path"))
        return destination

    def _check_paths(self, game_path: Path) -> None:
        if not self._backup_root:
            raise RuntimeError(tr("backup_manager.error.no_backup_directory"))
        PathSafety.independent_paths(game_path, self._backup_root)
        if game_path.resolve() == Path(game_path.resolve().anchor):
            raise RuntimeError(tr("backup_manager.error.unsafe_path"))

    def _generate_backup_id(self, game_code: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{game_code}_{timestamp}_{uuid4().hex}"

    def _collect_backup_files(self, game_path: Path) -> list[Path]:
        PathSafety.reject_links(game_path)
        paths: list[Path] = []
        for directory, folders, names in game_path.walk(
            follow_symlinks=False, on_error=PathSafety.raise_walk_error
        ):
            for name in sorted(folders + names):
                path = directory / name
                PathSafety.reject_links(path)
                if not path.is_dir() and not path.is_file():
                    raise RuntimeError(tr("backup_manager.error.unsafe_path"))
                paths.append(path)
        return paths

    def _transfer(self, source: Path, destination: Path, *, move: bool) -> None:
        """Copy whole trees or files; shutil.move handles cross-volume transfers."""
        PathSafety.reject_links(source)
        PathSafety.reject_links(destination)
        if move:
            shutil.move(str(source), str(destination))
        elif source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)

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

    def _load_and_validate_backup(self, backup_id: str) -> BackupInfo:
        """Load backup info and validate it's ready for restoration."""
        if not self._backup_root:
            raise RuntimeError(tr("backup_manager.error.no_backup_directory"))

        backup_info = self.load_backup_info(backup_id)
        if not backup_info:
            raise RuntimeError(tr("backup_manager.error.invalid_backup"))

        valid, error = self.validate_backup(backup_id)
        if not valid:
            raise RuntimeError(error)
        return backup_info

    def _save_metadata(self, backup_info: BackupInfo) -> None:
        backup_dir = self._backup_directory(backup_info.backup_id)
        write_json_atomic(backup_dir / self.METADATA_FILE, backup_info.to_dict())

    def _check_free_space(self, required_bytes: int) -> None:
        if not self._backup_root:
            raise RuntimeError(tr("backup_manager.error.no_backup_directory"))
        self._check_space(self._backup_root, required_bytes)

    @staticmethod
    def _check_space(path: Path, required_bytes: int) -> None:
        if shutil.disk_usage(path).free < required_bytes * BackupManager.SPACE_MARGIN:
            raise RuntimeError(tr("backup_manager.error.insufficient_disk_space"))
