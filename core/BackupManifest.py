"""Contents and comparison rules for a backup tree."""

from dataclasses import dataclass
from typing import TypedDict

from constants import BACKUP_USER_DIRECTORIES


class FileEntry(TypedDict):
    size: int
    sha256: str


class TreeManifest(TypedDict):
    files: dict[str, FileEntry]
    directories: list[str]


@dataclass
class BackupManifest:
    files: dict[str, FileEntry]
    directories: list[str]

    @property
    def byte_size(self) -> int:
        return sum(entry["size"] for entry in self.files.values())

    @classmethod
    def from_dict(cls, data: TreeManifest) -> "BackupManifest":
        return cls(files=data["files"], directories=data["directories"])

    def to_dict(self) -> TreeManifest:
        return {"files": self.files, "directories": self.directories}

    def with_current_user_files(self, current: "BackupManifest") -> "BackupManifest":
        """Keep current user files, including deletions since the backup."""
        files = {
            path: entry for path, entry in self.files.items() if not self._is_user_path(path)
        }
        files.update(
            {path: entry for path, entry in current.files.items() if self._is_user_path(path)}
        )

        directories = [path for path in self.directories if not self._is_user_path(path)]
        directories.extend(path for path in current.directories if self._is_user_path(path))

        return BackupManifest(files=files, directories=sorted(directories))

    @staticmethod
    def _is_user_path(path: str) -> bool:
        return path.split("/", 1)[0].lower() in BACKUP_USER_DIRECTORIES
