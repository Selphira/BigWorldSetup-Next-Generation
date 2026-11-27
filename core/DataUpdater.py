import hashlib
import logging
from pathlib import Path
import shutil
import tempfile
import threading
import zipfile

from PySide6.QtCore import QObject, Signal
import requests

from constants import (
    BASE_CATALOGUE_DIR,
    CATALOGUE_VERSION_FILE_NAME,
    DATA_SCHEMA_VERSION,
    DATA_VERSION_URL,
    DATA_ZIP_URL,
    DOWNLOAD_CHUNK_SIZE,
    DOWNLOAD_TIMEOUT,
    DOWNLOADED_CATALOGUE_DIR,
    MAX_RETRIES,
    SCHEMAS_DIR,
)
from core.atomic_io import write_json_atomic
from core.CatalogueLocation import CatalogueLocation, is_schema_compatible
from core.ProgressEmitter import ProgressEmitter
from core.TranslationManager import tr
from validation.catalogue import validate_catalogue

logger = logging.getLogger(__name__)


class UpdateCancelledError(Exception):
    """The update was cancelled before the catalogue was touched."""


class DataUpdater(QObject):
    """
    Download the published catalogue into DOWNLOADED_CATALOGUE_DIR.

    The shipped catalogue is never written. Transaction directories are
    created next to the downloaded catalogue, on the same volume.

    May run in a worker thread. A cancellation is accepted until the activation
    of the downloaded catalogue starts; past that point the update always ends
    with the new catalogue or the restored one.

    Signals:
        progress: Progress percentage
        status_changed: Status message
        update_error: Error message
    """

    progress = Signal(int)
    status_changed = Signal(str)
    update_error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._cancel_lock = threading.Lock()
        self._cancelled = False
        self._activation_started = False
        self._progress = ProgressEmitter(self.progress.emit)

    def request_cancel(self) -> bool:
        """
        Ask the update to stop without touching the catalogue.

        Returns:
            True if the catalogue is guaranteed to stay unchanged, False if its
            activation has already started and must be awaited
        """
        with self._cancel_lock:
            if self._activation_started:
                return False

            self._cancelled = True
            return True

    def check_for_updates(self) -> bool:
        """Check if data updates are available and compatible."""
        try:
            remote_version = self._fetch_remote_version()
            if self._cancelled:
                logger.info("Data update check cancelled")
                return False

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
        """Download, validate and activate the published catalogue."""
        backup_dir: Path | None = None
        temp_dir: Path | None = None
        committed = False

        try:
            if not self.recover_pending_update():
                raise OSError("Catalogue recovery failed; backup retained")
            remote_version = self._fetch_remote_version()
            self._raise_if_cancelled()
            if not remote_version or not remote_version.get("sha256"):
                raise ValueError("Missing published SHA-256")
            if remote_version and not self._is_data_compatible(remote_version):
                logger.error("Cannot update: incompatible data schema")
                self.update_error.emit(tr("app.incompatible_data_schema"))
                return False

            DOWNLOADED_CATALOGUE_DIR.parent.mkdir(parents=True, exist_ok=True)
            temp_dir = Path(
                tempfile.mkdtemp(prefix="bws_data_", dir=DOWNLOADED_CATALOGUE_DIR.parent)
            )
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

            self._raise_if_cancelled()
            report = validate_catalogue(extract_dir, SCHEMAS_DIR)
            if not report.valid:
                raise ValueError(f"Invalid catalogue: {report.diagnostics}")
            write_json_atomic(extract_dir / CATALOGUE_VERSION_FILE_NAME, remote_version)

            self._start_activation()
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

        except UpdateCancelledError:
            logger.info("Data update cancelled before activation")
            return False

        except Exception as e:
            logger.error("Data update failed", exc_info=True)
            self.update_error.emit(tr("app.update_failed", error=str(e)))
            return False

        finally:
            if backup_dir and not committed:
                self._restore_backup(backup_dir)
            if temp_dir and temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise UpdateCancelledError

    def _start_activation(self) -> None:
        """Enter the activation, from which a cancellation is refused."""
        with self._cancel_lock:
            self._raise_if_cancelled()
            self._activation_started = True

    @staticmethod
    def _is_data_compatible(remote_version: dict) -> bool:
        """Check if remote data schema is compatible with this app version."""
        is_compatible = is_schema_compatible(remote_version)
        logger.info(
            f"Schema compatibility check: App={DATA_SCHEMA_VERSION} vs "
            f"Remote={remote_version.get('data_schema_version', 'unknown')} → "
            f"{'Compatible' if is_compatible else 'Incompatible'}"
        )
        return is_compatible

    @staticmethod
    def _fetch_remote_version() -> dict | None:
        """Fetch remote version information."""
        try:
            with requests.get(DATA_VERSION_URL, timeout=DOWNLOAD_TIMEOUT) as response:
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.warning(f"Failed to fetch remote version: {e}")
            return None

    @staticmethod
    def _get_local_version() -> dict | None:
        """Get the version of the catalogue read once updates are enabled."""
        return CatalogueLocation.active(
            DOWNLOADED_CATALOGUE_DIR, BASE_CATALOGUE_DIR, downloaded_enabled=True
        ).read_version()

    @staticmethod
    def _is_update_needed(local: dict | None, remote: dict) -> bool:
        """Tell whether the published version is newer than the local one.

        Commit hashes only tell two versions apart; their publication
        timestamps order them, so that an application shipped with a newer
        catalogue does not download an older one.
        """
        if not local:
            return True

        local_hash = local.get("commit_hash", "")
        if local_hash and local_hash == remote.get("commit_hash", ""):
            return False

        return remote.get("timestamp", 0) > local.get("timestamp", 0)

    def _download_file(self, url: str, dest: Path) -> bool:
        """Download file with progress reporting and retry logic."""
        for attempt in range(MAX_RETRIES):
            try:
                self._raise_if_cancelled()
                self.status_changed.emit(
                    tr("app.downloading_data", attempt=attempt, max_retries=MAX_RETRIES)
                )

                with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as response:
                    response.raise_for_status()
                    downloaded = self._write_response(response, dest)

                logger.info(f"Downloaded {downloaded} bytes")
                return True

            except UpdateCancelledError:
                raise

            except Exception as e:
                logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                if attempt == MAX_RETRIES - 1:
                    self.update_error.emit(tr("app.download_failed", error=str(e)))

        return False

    def _write_response(self, response: requests.Response, dest: Path) -> int:
        """Stream the response body to `dest`, checking for cancellation between chunks."""
        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0
        self._progress.restart()

        with open(dest, "wb") as f:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                self._raise_if_cancelled()
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        progress = int((downloaded / total_size) * 50)  # 0-50% for download
                        self._progress.report(progress)

        return downloaded

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
                self._progress.restart()

                for i, member in enumerate(members):
                    self._raise_if_cancelled()
                    zf.extract(member, extract_dir)
                    progress = 50 + int((i / total) * 40)  # 50-90% for extraction
                    self._progress.report(progress)

            logger.info(f"Extracted {total} files")
            return True

        except UpdateCancelledError:
            raise

        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            self.update_error.emit(tr("app.extraction_failed", error=str(e)))
            return False

    @staticmethod
    def _create_backup() -> Path | None:
        """Move the current catalogue to a recoverable sibling before activation."""
        if not DOWNLOADED_CATALOGUE_DIR.exists():
            return None
        backup = DOWNLOADED_CATALOGUE_DIR.with_name(f"{DOWNLOADED_CATALOGUE_DIR.name}_backup")
        if backup.exists():
            raise FileExistsError(f"Pending catalogue recovery: {backup}")
        DOWNLOADED_CATALOGUE_DIR.rename(backup)
        return backup

    def recover_pending_update(self) -> bool:
        """Roll back an interrupted activation before any catalogue is loaded."""
        backup = DOWNLOADED_CATALOGUE_DIR.with_name(f"{DOWNLOADED_CATALOGUE_DIR.name}_backup")
        return not backup.exists() or self._restore_backup(backup)

    def _restore_backup(self, backup_dir: Path) -> bool:
        """Restore by rename; retain the backup if any recovery step fails."""
        discarded: Path | None = None
        try:
            self.status_changed.emit(tr("app.restoring_backup"))
            if DOWNLOADED_CATALOGUE_DIR.exists():
                discarded = Path(
                    tempfile.mkdtemp(
                        prefix="bws_discarded_", dir=DOWNLOADED_CATALOGUE_DIR.parent
                    )
                )
                DOWNLOADED_CATALOGUE_DIR.rename(discarded / DOWNLOADED_CATALOGUE_DIR.name)
            backup_dir.rename(DOWNLOADED_CATALOGUE_DIR)
            logger.info("Backup restored successfully")
            return True
        except OSError as error:
            logger.exception("Failed to restore backup at %s", backup_dir)
            self.update_error.emit(tr("app.update_failed", error=str(error)))
            return False
        finally:
            if discarded and DOWNLOADED_CATALOGUE_DIR.exists():
                shutil.rmtree(discarded, ignore_errors=True)

    def _replace_data_directory(self, new_data_dir: Path) -> bool:
        """Activate the fully prepared catalogue on the same filesystem."""
        try:
            new_data_dir.rename(DOWNLOADED_CATALOGUE_DIR)
            self._progress.force(100)
            return True
        except OSError as error:
            logger.exception("Failed to activate catalogue")
            self.update_error.emit(tr("app.update_failed", error=str(error)))
            return False
