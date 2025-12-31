from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class BackupStatus(Enum):
    """Status of a backup."""

    VALID = "valid"
    CORRUPTED = "corrupted"
    INCOMPLETE = "incomplete"


@dataclass
class BackupInfo:
    """Information about a game backup."""

    backup_id: str  # e.g., "BG2EE_20231225_143022"
    game_code: str  # e.g., "BG2EE"
    game_name: str  # e.g., "Baldur's Gate II: Enhanced Edition"
    game_path: Path  # Original game path
    creation_date: datetime
    total_size: int  # Total size in bytes
    file_count: int  # Number of files backed up
    is_modded: bool = False  # Whether this is a modded game backup
    custom_name: str = ""  # Optional custom name
    notes: str = ""  # Optional user notes
    status: BackupStatus = BackupStatus.VALID

    @property
    def display_name(self) -> str:
        """Get display name (custom name or backup_id)."""
        return self.custom_name if self.custom_name else self.backup_id

    def get_backup_dir(self, backup_root: Path) -> Path:
        """Get the backup directory path.

        Args:
            backup_root: Root backup directory from state manager

        Returns:
            Path to this backup's directory
        """
        return backup_root / self.backup_id

    @property
    def size_mb(self) -> float:
        """Get size in megabytes."""
        return self.total_size / (1024 * 1024)

    @property
    def size_gb(self) -> float:
        """Get size in gigabytes."""
        return self.total_size / (1024 * 1024 * 1024)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "backup_id": self.backup_id,
            "game_code": self.game_code,
            "game_name": self.game_name,
            "game_path": str(self.game_path),
            "creation_date": self.creation_date.isoformat(),
            "total_size": self.total_size,
            "file_count": self.file_count,
            "is_modded": self.is_modded,
            "custom_name": self.custom_name,
            "notes": self.notes,
            "status": self.status.value,
        }

    @staticmethod
    def from_dict(data: dict) -> "BackupInfo":
        """Create BackupInfo from dictionary."""
        return BackupInfo(
            backup_id=data["backup_id"],
            game_code=data["game_code"],
            game_name=data["game_name"],
            game_path=Path(data["game_path"]),
            creation_date=datetime.fromisoformat(data["creation_date"]),
            total_size=data["total_size"],
            file_count=data["file_count"],
            is_modded=data.get("is_modded", False),
            custom_name=data.get("custom_name", ""),
            notes=data.get("notes", ""),
            status=BackupStatus(data.get("status", "valid")),
        )
