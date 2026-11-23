"""Location of the catalogue read by the application: downloaded or shipped."""

from dataclasses import dataclass
from pathlib import Path

from constants import (
    BASE_CATALOGUE_DIR,
    CATALOGUE_GAMES_DIR_NAME,
    CATALOGUE_MODS_DIR_NAME,
    CATALOGUE_RULES_DIR_NAME,
    CATALOGUE_VERSION_FILE_NAME,
    DOWNLOADED_CATALOGUE_DIR,
)


@dataclass(frozen=True)
class CatalogueLocation:
    """Directory of a catalogue and the paths of its parts."""

    root: Path

    @classmethod
    def active(
        cls,
        downloaded_dir: Path = DOWNLOADED_CATALOGUE_DIR,
        base_dir: Path = BASE_CATALOGUE_DIR,
    ) -> "CatalogueLocation":
        """Return the downloaded catalogue once activated, the shipped one otherwise.

        A downloaded catalogue only appears by renaming a fully prepared
        directory that already holds its version file: that file proves the
        activation completed.
        """
        downloaded = cls(downloaded_dir)
        if downloaded.version_file.is_file():
            return downloaded

        return cls(base_dir)

    @property
    def mods_dir(self) -> Path:
        return self.root / CATALOGUE_MODS_DIR_NAME

    @property
    def games_dir(self) -> Path:
        return self.root / CATALOGUE_GAMES_DIR_NAME

    @property
    def rules_dir(self) -> Path:
        return self.root / CATALOGUE_RULES_DIR_NAME

    @property
    def version_file(self) -> Path:
        return self.root / CATALOGUE_VERSION_FILE_NAME
