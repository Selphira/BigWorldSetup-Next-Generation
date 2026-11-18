"""Path safety checks shared by backups and folder configuration."""

from pathlib import Path

from core.TranslationManager import tr


class PathSafety:
    """Reject links and overlapping roots before filesystem operations."""

    @staticmethod
    def reject_links(path: Path) -> None:
        for item in (path, *path.parents):
            if item.is_symlink() or item.is_junction():
                raise RuntimeError(tr("backup_manager.error.unsafe_path"))

    @classmethod
    def independent_paths(cls, first: Path, second: Path) -> None:
        cls.reject_links(first)
        cls.reject_links(second)
        first, second = first.resolve(), second.resolve()
        if first.is_relative_to(second) or second.is_relative_to(first):
            raise RuntimeError(tr("backup_manager.error.unsafe_path"))

    @staticmethod
    def raise_walk_error(error: OSError) -> None:
        """Never turn an unreadable subtree into a successful partial snapshot."""
        raise error
