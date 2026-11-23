"""Location of the catalogue read by the application: downloaded or shipped."""

from dataclasses import dataclass
import json
import logging
from pathlib import Path

from constants import (
    BASE_CATALOGUE_DIR,
    CATALOGUE_GAMES_DIR_NAME,
    CATALOGUE_MODS_DIR_NAME,
    CATALOGUE_RULES_DIR_NAME,
    CATALOGUE_UPDATE_ENABLED,
    CATALOGUE_VERSION_FILE_NAME,
    DATA_SCHEMA_VERSION,
    DOWNLOADED_CATALOGUE_DIR,
)

logger = logging.getLogger(__name__)


def is_schema_compatible(version: dict) -> bool:
    """Tell whether a catalogue version can be read by this application.

    Versions published without a schema are assumed compatible; the major
    number of the schema must match the one of the application.
    """
    schema = version.get("data_schema_version")
    if not schema:
        return True

    if not isinstance(schema, str):
        return False

    return schema.split(".")[0] == DATA_SCHEMA_VERSION.split(".")[0]


@dataclass(frozen=True)
class CatalogueLocation:
    """Directory of a catalogue and the paths of its parts."""

    root: Path

    @classmethod
    def active(
        cls,
        downloaded_dir: Path = DOWNLOADED_CATALOGUE_DIR,
        base_dir: Path = BASE_CATALOGUE_DIR,
        downloaded_enabled: bool = CATALOGUE_UPDATE_ENABLED,
    ) -> "CatalogueLocation":
        """Return the downloaded catalogue when it supersedes the shipped one.

        The downloaded catalogue is read only if downloads are enabled and it is
        complete, of a compatible schema and strictly newer than the shipped
        one; in any other case (first launch, invalid or deleted download,
        application shipped with a newer catalogue) the shipped one is read.
        """
        base = cls(base_dir)
        if not downloaded_enabled:
            return base

        downloaded = cls(downloaded_dir)
        downloaded_version = downloaded.read_version()
        if (
            downloaded_version is None
            or not downloaded.has_every_part()
            or not is_schema_compatible(downloaded_version)
        ):
            return base

        if cls._timestamp(downloaded_version) <= cls._timestamp(base.read_version()):
            return base

        return downloaded

    def read_version(self) -> dict | None:
        """Return the version of this catalogue, None if absent or unreadable.

        A downloaded catalogue only appears by renaming a fully prepared
        directory that already holds its version file: that file proves the
        activation completed.
        """
        if not self.version_file.is_file():
            return None

        try:
            version = json.loads(self.version_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            logger.warning(f"Unreadable catalogue version {self.version_file}: {error}")
            return None

        return version if isinstance(version, dict) else None

    def has_every_part(self) -> bool:
        return all(part.is_dir() for part in (self.mods_dir, self.games_dir, self.rules_dir))

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

    @staticmethod
    def _timestamp(version: dict | None) -> float:
        """Publication time of a version; 0 when unknown, older than any published one."""
        timestamp = (version or {}).get("timestamp", 0)
        return timestamp if isinstance(timestamp, int | float) else 0
