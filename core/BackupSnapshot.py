"""Verified directory snapshots and restoration manifest composition."""

import hashlib
from pathlib import Path

from constants import BACKUP_MANIFEST_VERSION, BACKUP_SNAPSHOT_CONTRACT, BACKUP_USER_DIRECTORIES
from core.BackupManifest import BackupManifest, FileEntry, TreeManifest
from core.PathSafety import PathSafety
from core.TranslationManager import tr


class BackupSnapshot:
    """Inspect and verify the contents of one filesystem tree."""

    MANIFEST_VERSION: int = BACKUP_MANIFEST_VERSION
    SNAPSHOT_CONTRACT: str = BACKUP_SNAPSHOT_CONTRACT
    USER_DIRECTORIES: frozenset[str] = BACKUP_USER_DIRECTORIES

    def __init__(self, root: Path) -> None:
        self._root: Path = root

    def inventory(self) -> TreeManifest:
        """Hash all regular files; reject links, junctions and special files."""
        root = self._root
        PathSafety.reject_links(root)
        if not root.is_dir():
            raise RuntimeError(tr("backup_manager.error.game_files_missing"))

        files: dict[str, FileEntry] = {}
        directories: list[str] = []
        for directory, folders, names in root.walk(
            follow_symlinks=False, on_error=PathSafety.raise_walk_error
        ):
            for name in sorted(folders + names):
                path = directory / name
                PathSafety.reject_links(path)
                relative = path.relative_to(root).as_posix()
                if path.is_dir():
                    directories.append(relative)
                elif path.is_file():
                    with path.open("rb") as stream:
                        digest = hashlib.file_digest(stream, "sha256").hexdigest()
                    files[relative] = {"size": path.stat().st_size, "sha256": digest}
                else:
                    raise RuntimeError(tr("backup_manager.error.unsafe_path"))
        return {"files": files, "directories": sorted(directories)}

    def verify(self, expected: TreeManifest) -> None:
        if self.inventory() != expected:
            raise RuntimeError(tr("backup_manager.error.integrity_mismatch"))

    @staticmethod
    def byte_size(manifest: TreeManifest) -> int:
        return BackupManifest.from_dict(manifest).byte_size

    @classmethod
    def restored_manifest(cls, snapshot: TreeManifest, current: TreeManifest) -> TreeManifest:
        """Replace user directories entirely, preserving current additions and deletions."""

        backup_manifest = BackupManifest.from_dict(snapshot)
        current_manifest = BackupManifest.from_dict(current)
        return backup_manifest.with_current_user_files(current_manifest).to_dict()
