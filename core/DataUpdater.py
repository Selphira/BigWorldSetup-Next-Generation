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
    DATA_VERSION_FILE,
    DATA_VERSION_URL,
    DATA_ZIP_URL,
    DOWNLOAD_CHUNK_SIZE,
    DOWNLOAD_TIMEOUT,
    MAX_RETRIES,
)
from core.TranslationManager import tr

logger = logging.getLogger(__name__)


class DataUpdater(QObject):
    """Handles data folder updates from remote source."""

    progress = Signal(int)  # progress_percentage
    status_changed = Signal(str)  # status_message
    update_error = Signal(str)  # error_message

    def check_for_updates(self) -> bool:
        """Check if data updates are available."""
        try:
            self.status_changed.emit(tr("app.checking_data_update"))
            remote_version = self._fetch_remote_version()

            if not remote_version:
                logger.info("No remote version info available")
                return False

            local_version = self._get_local_version()
            needs_update = self._is_update_needed(local_version, remote_version)

            logger.info(
                f"Update {'available' if needs_update else 'not needed'}: "
                f"{remote_version.get('commit_hash', 'unknown')}"
            )
            return needs_update

        except Exception as e:
            logger.warning(f"Failed to check for data updates: {e}")
            return False

    def update_data(self) -> bool:
        """Download and install data update."""
        backup_dir: Path | None = None
        temp_dir: Path | None = None

        try:
            temp_dir = Path(tempfile.mkdtemp(prefix="bws_data_"))
            zip_path = temp_dir / "data.zip"

            if not self._download_file(DATA_ZIP_URL, zip_path):
                return False

            if not self._validate_zip(zip_path):
                self.update_error.emit(tr("app.downloaded_file_corrupted"))
                return False

            self.status_changed.emit(tr("app.backing_up_existing_data"))
            backup_dir = self._create_backup()

            extract_dir = temp_dir / "extracted"
            if not self._extract_zip(zip_path, extract_dir):
                return False

            self.status_changed.emit(tr("app.installing_update"))
            if not self._replace_data_directory(extract_dir):
                return False

            if remote_version := self._fetch_remote_version():
                self._save_local_version(remote_version)

            self.status_changed.emit(tr("app.update_complete"))
            logger.info("Data update completed successfully")
            return True

        except Exception as e:
            logger.error("Data update failed", exc_info=True)
            self.update_error.emit(tr("app.update_failed", error=str(e)))
            if backup_dir:
                self._restore_backup(backup_dir)
            return False

        finally:
            # Cleanup
            for directory in (backup_dir, temp_dir):
                if directory and directory.exists():
                    shutil.rmtree(directory, ignore_errors=True)

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
    def _save_local_version(version_data: dict) -> None:
        """Save version data to local file."""
        try:
            with open(DATA_VERSION_FILE, "w", encoding="utf-8") as f:
                json.dump(version_data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save version file: {e}")

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
                    tr("app;downloading_data", attempt=attempt, max_retries=MAX_RETRIES)
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
                    self.update_error.emit(f"Download failed: {str(e)}")

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
                total = len(members)

                for i, member in enumerate(members):
                    zf.extract(member, extract_dir)
                    progress = 50 + int((i / total) * 40)  # 50-90% for extraction
                    self.progress.emit(progress)

            logger.info(f"Extracted {total} files")
            return True

        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            self.update_error.emit(f"Extraction failed: {str(e)}")
            return False

    @staticmethod
    def _create_backup() -> Path | None:
        """Create backup of current data directory."""
        if not DATA_DIR.exists():
            return None

        try:
            backup_dir = DATA_DIR.parent / f"{DATA_DIR.name}_backup"
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            shutil.copytree(DATA_DIR, backup_dir)
            logger.info(f"Created backup at {backup_dir}")
            return backup_dir
        except Exception as e:
            logger.warning(f"Failed to create backup: {e}")
            return None

    def _restore_backup(self, backup_dir: Path) -> None:
        """Restore data directory from backup."""
        try:
            self.status_changed.emit(tr("app.restoring_backup"))
            if DATA_DIR.exists():
                shutil.rmtree(DATA_DIR)
            shutil.copytree(backup_dir, DATA_DIR)
            logger.info("Backup restored successfully")
        except Exception as e:
            logger.error(f"Failed to restore backup: {e}")

    def _replace_data_directory(self, new_data_dir: Path) -> bool:
        """Atomically replace data directory."""
        try:
            if DATA_DIR.exists():
                shutil.rmtree(DATA_DIR)
            shutil.copytree(new_data_dir, DATA_DIR)
            self.progress.emit(100)
            return True
        except Exception as e:
            logger.error(f"Failed to replace data directory: {e}")
            self.update_error.emit(f"Installation failed: {str(e)}")
            return False
