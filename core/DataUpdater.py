import hashlib
import json
import logging
from pathlib import Path
import shutil
import tempfile
import zipfile

from PySide6.QtCore import QObject, Signal
import requests

from constants import (
    DATA_DIR,
    DATA_SCHEMA_VERSION,
    DATA_VERSION_FILE,
    DATA_VERSION_URL,
    DATA_ZIP_URL,
    DOWNLOAD_CHUNK_SIZE,
    DOWNLOAD_TIMEOUT,
    MAX_RETRIES,
    SCHEMAS_DIR,
)
from core.atomic_io import write_json_atomic
from core.TranslationManager import tr
from validation.catalogue import validate_catalogue

logger = logging.getLogger(__name__)


class DataUpdater(QObject):
    """Handles data folder updates from remote source."""

    progress = Signal(int)  # progress_percentage
    status_changed = Signal(str)  # status_message
    update_error = Signal(str)  # error_message

    def check_for_updates(self) -> bool:
        """Check if data updates are available and compatible."""
        try:
            remote_version = self._fetch_remote_version()

            if not remote_version:
                logger.info("No remote version info available")
                return False

            if not self._is_data_compatible(remote_version):
                logger.warning("Remote data schema is incompatible with this app version")
                return False

            local_version = self._get_local_version()
            needs_update = self._is_update_needed(local_version, remote_version)

            logger.info(
                f"Update {'available' if needs_update else 'not needed'}: "
                f"{remote_version.get('commit_hash', 'unknown')} "
                f"(schema: {remote_version.get('data_schema_version', 'unknown')})"
            )
            return needs_update

        except Exception as e:
            logger.warning(f"Failed to check for data updates: {e}")
            return False

    def update_data(self) -> bool:
        """Download and install data update while preserving custom mods."""
        backup_dir: Path | None = None
        temp_dir: Path | None = None
        committed = False

        try:
            if not self.recover_pending_update():
                raise OSError("Catalogue recovery failed; backup retained")
            remote_version = self._fetch_remote_version()
            if not remote_version or not remote_version.get("sha256"):
                raise ValueError("Missing published SHA-256")
            if remote_version and not self._is_data_compatible(remote_version):
                logger.error("Cannot update: incompatible data schema")
                self.update_error.emit(tr("app.incompatible_data_schema"))
                return False

            temp_dir = Path(tempfile.mkdtemp(prefix="bws_data_", dir=DATA_DIR.parent))
            zip_path = temp_dir / "data.zip"

            if not self._download_file(DATA_ZIP_URL, zip_path):
                return False

            with zip_path.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if checksum != remote_version["sha256"].lower():
                raise ValueError("Catalogue SHA-256 mismatch")

            if not self._validate_zip(zip_path):
                self.update_error.emit(tr("app.downloaded_file_corrupted"))
                return False

            extract_dir = temp_dir / "extracted"
            if not self._extract_zip(zip_path, extract_dir):
                return False

            report = validate_catalogue(extract_dir, SCHEMAS_DIR)
            if not report.valid:
                raise ValueError(f"Invalid catalogue: {report.diagnostics}")
            # Preserve all local data outside the official catalogue directories.
            if DATA_DIR.exists():
                for item in DATA_DIR.iterdir():
                    if item.name in {"mods", "games", "rules", DATA_VERSION_FILE.name}:
                        continue
                    destination = extract_dir / item.name
                    if destination.is_dir():
                        shutil.rmtree(destination)
                    elif destination.exists():
                        destination.unlink()
                    if item.is_dir():
                        shutil.copytree(item, destination)
                    else:
                        shutil.copy2(item, destination)
            write_json_atomic(extract_dir / DATA_VERSION_FILE.name, remote_version)

            self.status_changed.emit(tr("app.backing_up_existing_data"))
            backup_dir = self._create_backup()

            self.status_changed.emit(tr("app.installing_update"))
            if not self._replace_data_directory(extract_dir):
                return False

            if backup_dir:
                backup_dir.rename(temp_dir / "previous")
            committed = True

            self.status_changed.emit(tr("app.update_complete"))
            logger.info("Data update completed successfully")
            return True

        except Exception as e:
            logger.error("Data update failed", exc_info=True)
            self.update_error.emit(tr("app.update_failed", error=str(e)))
            return False

        finally:
            if backup_dir and not committed:
                self._restore_backup(backup_dir)
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    @staticmethod
    def _is_data_compatible(remote_version: dict) -> bool:
        """Check if remote data schema is compatible with this app version."""
        remote_schema = remote_version.get("data_schema_version")

        if not remote_schema:
            logger.warning("Remote data has no schema version - assuming compatible")
            return True

        try:
            app_major = DATA_SCHEMA_VERSION.split(".")[0]
            remote_major = remote_schema.split(".")[0]
            is_compatible = app_major == remote_major

            logger.info(
                f"Schema compatibility check: "
                f"App={DATA_SCHEMA_VERSION} vs Remote={remote_schema} → "
                f"{'Compatible' if is_compatible else 'Incompatible'}"
            )

            return is_compatible

        except (IndexError, AttributeError) as e:
            logger.error(f"Invalid schema version format: {e}")
            return False

    @staticmethod
    def _fetch_remote_version() -> dict | None:
        """Fetch remote version information."""
        try:
            response = requests.get(DATA_VERSION_URL, timeout=DOWNLOAD_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to fetch remote version: {e}")
            return None

    @staticmethod
    def _get_local_version() -> dict | None:
        """Get local data version."""
        if not DATA_VERSION_FILE.exists():
            return None
        try:
            with open(DATA_VERSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read local version: {e}")
            return None

    @staticmethod
    def _is_update_needed(local: dict | None, remote: dict) -> bool:
        """Compare versions to determine if update is needed."""
        if not local:
            return True

        local_hash = local.get("commit_hash", "")
        remote_hash = remote.get("commit_hash", "")

        if local_hash and remote_hash:
            return local_hash != remote_hash

        local_time = local.get("timestamp", 0)
        remote_time = remote.get("timestamp", 0)

        return remote_time > local_time

    def _download_file(self, url: str, dest: Path) -> bool:
        """Download file with progress reporting and retry logic."""
        for attempt in range(MAX_RETRIES):
            try:
                self.status_changed.emit(
                    tr("app.downloading_data", attempt=attempt, max_retries=MAX_RETRIES)
                )

                response = requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT)
                response.raise_for_status()

                total_size = int(response.headers.get("content-length", 0))
                downloaded = 0

                with open(dest, "wb") as f:
                    for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_size > 0:
                                progress = int(
                                    (downloaded / total_size) * 50
                                )  # 0-50% for download
                                self.progress.emit(progress)

                logger.info(f"Downloaded {downloaded} bytes")
                return True

            except Exception as e:
                logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                if attempt == MAX_RETRIES - 1:
                    self.update_error.emit(tr("app.download_failed", error=str(e)))

        return False

    def _validate_zip(self, zip_path: Path) -> bool:
        """Validate ZIP file integrity."""
        try:
            self.status_changed.emit(tr("app.validating_download"))
            with zipfile.ZipFile(zip_path, "r") as zf:
                bad_file = zf.testzip()
                if bad_file:
                    logger.error(f"Corrupted file in ZIP: {bad_file}")
                    return False
                return True
        except Exception as e:
            logger.error(f"ZIP validation failed: {e}")
            return False

    def _extract_zip(self, zip_path: Path, extract_dir: Path) -> bool:
        """Extract ZIP file with progress reporting."""
        try:
            self.status_changed.emit(tr("app.extracting_data"))
            extract_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(zip_path, "r") as zf:
                members = zf.namelist()
                root = extract_dir.resolve()
                for member in members:
                    target = (root / member).resolve()
                    if not target.is_relative_to(root) or ":" in member or "\\" in member:
                        raise ValueError(f"Unsafe archive member: {member}")
                total = len(members)

                for i, member in enumerate(members):
                    zf.extract(member, extract_dir)
                    progress = 50 + int((i / total) * 40)  # 50-90% for extraction
                    self.progress.emit(progress)

            logger.info(f"Extracted {total} files")
            return True

        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            self.update_error.emit(tr("app.extraction_failed", error=str(e)))
            return False

    @staticmethod
    def _create_backup() -> Path | None:
        """Move the current catalogue to a recoverable sibling before activation."""
        if not DATA_DIR.exists():
            return None
        backup = DATA_DIR.with_name(f"{DATA_DIR.name}_backup")
        if backup.exists():
            raise FileExistsError(f"Pending catalogue recovery: {backup}")
        DATA_DIR.rename(backup)
        return backup

    def recover_pending_update(self) -> bool:
        """Roll back an interrupted activation before any catalogue is loaded."""
        backup = DATA_DIR.with_name(f"{DATA_DIR.name}_backup")
        return not backup.exists() or self._restore_backup(backup)

    def _restore_backup(self, backup_dir: Path) -> bool:
        """Restore by rename; retain the backup if any recovery step fails."""
        discarded: Path | None = None
        try:
            self.status_changed.emit(tr("app.restoring_backup"))
            if DATA_DIR.exists():
                discarded = Path(tempfile.mkdtemp(prefix="bws_discarded_", dir=DATA_DIR.parent))
                DATA_DIR.rename(discarded / "data")
            backup_dir.rename(DATA_DIR)
            logger.info("Backup restored successfully")
            return True
        except OSError as error:
            logger.exception("Failed to restore backup at %s", backup_dir)
            self.update_error.emit(tr("app.update_failed", error=str(error)))
            return False
        finally:
            if discarded and DATA_DIR.exists():
                shutil.rmtree(discarded, ignore_errors=True)

    def _replace_data_directory(self, new_data_dir: Path) -> bool:
        """Activate the fully prepared catalogue on the same filesystem."""
        try:
            new_data_dir.rename(DATA_DIR)
            self.progress.emit(100)
            return True
        except OSError as error:
            logger.exception("Failed to activate catalogue")
            self.update_error.emit(tr("app.update_failed", error=str(error)))
            return False
