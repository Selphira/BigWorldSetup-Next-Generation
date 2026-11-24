"""Names already shown for the backups of an installation, to spot a name given twice."""

from collections.abc import Collection, Iterable

from core.BackupInfo import BackupInfo


class BackupNames:
    """Display names of an installation's backups, compared without case or surrounding spaces.

    An installation may span several games, such as EET with BGEE and BG2EE:
    the backups of all its games share the same names. A backup's own name is
    left out when it is being renamed, so keeping it unchanged is not reported
    as a duplicate.
    """

    def __init__(
        self,
        backups: Iterable[BackupInfo],
        game_codes: Collection[str],
        excluding_backup_id: str | None = None,
    ) -> None:
        """Collect the names in use.

        Args:
            backups: Known backups, of any game
            game_codes: Games of the installation whose backup names are compared
            excluding_backup_id: Backup being renamed, whose name is ignored
        """
        self._names: set[str] = {
            self._normalize(backup.display_name)
            for backup in backups
            if backup.game_code in game_codes and backup.backup_id != excluding_backup_id
        }

    def contains(self, name: str) -> bool:
        """Tell whether another backup of the installation already shows this name.

        Args:
            name: Name typed by the user; an empty name falls back to the
                unique backup ID and never conflicts

        Returns:
            True if the name is already used
        """
        normalized = self._normalize(name)
        return bool(normalized) and normalized in self._names

    @staticmethod
    def _normalize(name: str) -> str:
        return name.strip().casefold()
