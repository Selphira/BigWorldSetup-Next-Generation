"""Paths, manifests and durable phases of one restoration."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from constants import BACKUP_MANIFEST_VERSION, RESTORE_JOURNAL_FILE
from core.atomic_io import write_json_atomic
from core.BackupManifest import BackupManifest


class RestorePhase(StrEnum):
    PREPARING = "preparing"
    PREPARED = "prepared"
    ORIGINAL_SAVED = "original_saved"
    COMPLETE = "complete"


@dataclass
class RestoreTransaction:
    backup_id: str
    game_dir: Path
    backup_files_dir: Path
    directory: Path
    current_manifest: BackupManifest
    backup_manifest: BackupManifest
    restored_manifest: BackupManifest

    @property
    def prepared_game_dir(self) -> Path:
        return self.directory / "staged"

    @property
    def recovery_dir(self) -> Path:
        return self.directory / "original"

    def save_phase(self, phase: RestorePhase) -> None:
        """Persist a phase without changing the existing journal format."""
        journal: dict[str, object] = {
            "version": BACKUP_MANIFEST_VERSION,
            "phase": phase.value,
            "target": str(self.game_dir),
            "backup_id": self.backup_id,
            "original": self.current_manifest.to_dict(),
        }
        if phase != RestorePhase.PREPARING:
            journal["restored"] = self.restored_manifest.to_dict()

        write_json_atomic(self.directory / RESTORE_JOURNAL_FILE, journal)
