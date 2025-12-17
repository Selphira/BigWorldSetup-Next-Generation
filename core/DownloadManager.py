from dataclasses import dataclass
from enum import Enum
import hashlib
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

logger = logging.getLogger(__name__)


# ============================================================================
# Enums and Constants
# ============================================================================


class ArchiveStatus(str, Enum):
    """Status of a mod archive.

    Attributes:
        VALID: Archive present and hash matches
        INVALID_HASH: Archive present but hash mismatch
        INVALID_SIZE: Archive present but size mismatch
        MISSING: Archive not present in download folder
        MANUAL: Archive requires manual download (no URL provided)
        DOWNLOADING: Archive is currently being downloaded
        ERROR: Download or verification error
    """

    VALID = "valid"
    INVALID_HASH = "invalid_hash"
    INVALID_SIZE = "invalid_size"
    MISSING = "missing"
    MANUAL = "manual"
    DOWNLOADING = "downloading"
    ERROR = "error"
    VERIFYING = "verifying"
    UNKNOWN = "unknown"

    @property
    def is_available(self) -> bool:
        """Check if archive is available and valid.

        Returns:
            True if status is VALID
        """
        return self == ArchiveStatus.VALID

    @property
    def needs_download(self) -> bool:
        """Check if archive needs to be downloaded.

        Returns:
            True if status is MISSING, INVALID_HASH, INVALID_SIZE, ERROR or UNKNOWN
        """
        return self in (
            ArchiveStatus.MISSING,
            ArchiveStatus.INVALID_HASH,
            ArchiveStatus.INVALID_SIZE,
            ArchiveStatus.ERROR,
            ArchiveStatus.UNKNOWN,
        )

    @property
    def is_downloading(self) -> bool:
        """Check if download is in progress.

        Returns:
            True if status is DOWNLOADING
        """
        return self == ArchiveStatus.DOWNLOADING


class HashAlgorithm(str, Enum):
    """Supported hash algorithms for verification.

    Attributes:
        MD5: MD5 hash algorithm
        SHA1: SHA-1 hash algorithm
        SHA256: SHA-256 hash algorithm
    """

    MD5 = "md5"
    SHA1 = "sha1"
    SHA256 = "sha256"


# ============================================================================
# Data Models
# ============================================================================


@dataclass
class ArchiveInfo:
    """Information about a mod archive.

    Attributes:
        mod_id: Mod identifier
        filename: Archive filename
        url: Download URL (None for manual downloads)
        expected_hash: Expected hash value for verification
        hash_algorithm: Algorithm used for hash (default: MD5)
        file_size: Expected file size in bytes (0 if unknown)
    """

    mod_id: str
    filename: str | None
    url: str | None
    expected_hash: str
    hash_algorithm: HashAlgorithm = HashAlgorithm.SHA256
    file_size: int = 0

    @property
    def requires_manual_download(self) -> bool:
        """Check if archive requires manual download.

        Returns:
            True if no URL is provided
        """
        return self.url is None or self.url == ""

    def __str__(self) -> str:
        """String representation for logging."""
        manual = " (manual)" if self.requires_manual_download else ""
        return f"{self.mod_id}: {self.filename}{manual}"


@dataclass
class DownloadProgress:
    """Progress information for an active download.

    Attributes:
        archive_info: Archive being downloaded
        bytes_received: Number of bytes downloaded
        bytes_total: Total bytes to download
        speed_bps: Current download speed in bytes per second
        error_message: Error message if download failed
    """

    archive_info: ArchiveInfo
    bytes_received: int = 0
    bytes_total: int = 0
    speed_bps: float = 0.0
    error_message: str = ""

    @property
    def progress_percent(self) -> float:
        """Calculate download progress percentage.

        Returns:
            Progress as percentage (0-100)
        """
        if self.bytes_total <= 0:
            return 0.0
        return (self.bytes_received / self.bytes_total) * 100.0

    @property
    def time_remaining_seconds(self) -> float:
        """Estimate time remaining for download.

        Returns:
            Estimated seconds remaining (0 if unknown)
        """
        if self.speed_bps <= 0:
            return 0.0

        remaining_bytes = self.bytes_total - self.bytes_received
        return remaining_bytes / self.speed_bps

    @property
    def speed_mbps(self) -> float:
        """Get download speed in MB/s.

        Returns:
            Speed in megabytes per second
        """
        return self.speed_bps / (1024 * 1024)

    @property
    def has_error(self) -> bool:
        """Check if download has an error.

        Returns:
            True if error message is present
        """
        return bool(self.error_message)


# ============================================================================
# Archive Verification
# ============================================================================


class ArchiveVerifier:
    """Verifies archive integrity using hash comparison.

    Supports MD5, SHA1, and SHA256 hash algorithms.
    """

    # Buffer size for reading files (1 MB)
    BUFFER_SIZE = 1024 * 1024

    def __init__(self):
        """Initialize archive verifier."""
        self._hash_functions = {
            HashAlgorithm.MD5: hashlib.md5,
            HashAlgorithm.SHA1: hashlib.sha1,
            HashAlgorithm.SHA256: hashlib.sha256,
        }

    def verify_archive(self, file_path: Path, info: ArchiveInfo) -> ArchiveStatus:
        """Verify archive integrity by comparing hashes.

        Args:
            file_path: Path to archive file
            info: ArchiveInfo to use

        Returns:
            ArchiveStatus
        """
        if not file_path.exists():
            return ArchiveStatus.MISSING

        try:
            if info.file_size > 0:
                actual_size = file_path.stat().st_size
                if actual_size != info.file_size:
                    logger.warning(
                        f"Invalid size for {file_path}: {actual_size} (Expected: {info.file_size})"
                    )
                    return ArchiveStatus.INVALID_SIZE

            actual_hash = self.calculate_hash(file_path, info.hash_algorithm)
            if actual_hash.lower() != info.expected_hash.lower():
                logger.warning(
                    f"Invalid hash for {file_path}: {actual_hash.lower()} (Expected: {info.expected_hash.lower()})"
                )
                return ArchiveStatus.INVALID_HASH

            return ArchiveStatus.VALID
        except Exception as e:
            logger.error(f"Error verifying archive {file_path}: {e}", exc_info=True)
            return ArchiveStatus.ERROR

    def calculate_hash(
        self, file_path: Path, algorithm: HashAlgorithm = HashAlgorithm.MD5
    ) -> str:
        """Calculate hash of a file.

        Args:
            file_path: Path to file
            algorithm: Hash algorithm to use

        Returns:
            Hex digest of the hash

        Raises:
            ValueError: If algorithm is not supported
            FileNotFoundError: If file doesn't exist
        """
        if algorithm not in self._hash_functions:
            raise ValueError(f"Unsupported hash algorithm: {algorithm}")

        hash_func = self._hash_functions[algorithm]()

        with open(file_path, "rb") as f:
            while chunk := f.read(self.BUFFER_SIZE):
                hash_func.update(chunk)

        return hash_func.hexdigest()


# ============================================================================
# Download Worker (runs in separate thread)
# ============================================================================


class DownloadWorker(QObject):
    """Worker for downloading a single archive in a separate thread.

    Signals:
        progress: Emitted on download progress (bytes_received, bytes_total, speed_bps)
        finished: Emitted when download completes successfully (file_path)
        error: Emitted on download error (error_message)
    """

    # Signals
    progress = Signal(int, int, float)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, archive_info: ArchiveInfo, download_path: Path):
        """Initialize download worker.

        Args:
            archive_info: Archive to download
            download_path: Directory to save the file
        """
        super().__init__()
        self.archive_info = archive_info
        self.download_path = download_path
        self._network_manager: QNetworkAccessManager | None = None
        self._reply: QNetworkReply | None = None
        self._bytes_received = 0
        self._start_time = 0.0
        self._output_file = None
        self._temp_path = None

    def start_download(self) -> None:
        """Start the download."""
        if not self.archive_info.url:
            self.error.emit("No download URL provided")
            return

        request = QNetworkRequest(self.archive_info.url)
        request.setHeader(
            QNetworkRequest.KnownHeaders.UserAgentHeader,
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36",
        )
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )

        self._network_manager = QNetworkAccessManager(self)
        self._reply = self._network_manager.get(request)

        self._reply.downloadProgress.connect(self._on_download_progress)
        self._reply.finished.connect(self._on_download_finished)
        self._reply.errorOccurred.connect(self._on_download_error)

        # Prepare temporary file
        self._temp_path = self.download_path / (self.archive_info.filename + ".part")
        self._output_file = open(self._temp_path, "wb")

        # Connect data ready signal
        self._reply.readyRead.connect(self._on_ready_read)

        import time

        self._start_time = time.time()

        logger.info(f"Started download: {self.archive_info.filename}")

    def cancel(self) -> None:
        """Cancel the download and delete partial file."""
        if self._reply:
            self._reply.abort()

        logger.info(f"Cancelled download: {self.archive_info.filename}")

        # Delete partial file
        try:
            if self._output_file:
                self._output_file.close()
                self._output_file = None
            if self._temp_path and self._temp_path.exists():
                self._temp_path.unlink()
        except Exception as e:
            logger.warning(f"Failed to remove partial file: {e}")

    def _on_ready_read(self) -> None:
        """Handle data ready to be read."""
        if self._reply and self._output_file:
            data = self._reply.readAll()
            self._output_file.write(data.data())

    def _on_download_progress(self, bytes_received: int, bytes_total: int) -> None:
        """Handle download progress update.

        Args:
            bytes_received: Bytes downloaded so far
            bytes_total: Total bytes to download
        """
        self._bytes_received = bytes_received

        # Calculate speed
        import time

        elapsed = time.time() - self._start_time
        speed_bps = bytes_received / elapsed if elapsed > 0 else 0.0

        if bytes_total < 0:
            bytes_total = self.archive_info.file_size

        self.progress.emit(bytes_received, bytes_total, speed_bps)

    def _on_download_finished(self) -> None:
        """Handle download completion."""
        if self._output_file:
            self._output_file.close()
            self._output_file = None

        if self._reply and self._reply.error() == QNetworkReply.NetworkError.NoError:
            final_path = self.download_path / self.archive_info.filename
            if final_path.exists():
                final_path.unlink(missing_ok=True)
            try:
                if self._temp_path.exists():
                    self._temp_path.rename(final_path)
            except Exception as e:
                logger.error(f"Failed to rename file: {e}")
                self.error.emit(str(e))
                return

            logger.info(f"Download completed: {self.archive_info.filename}")
            self.finished.emit(str(final_path))

        if self._reply:
            self._reply.deleteLater()
            self._reply = None

        if self._network_manager:
            self._network_manager.deleteLater()
            self._network_manager = None

    def _on_download_error(self, error_code) -> None:
        """Handle download error.

        Args:
            error_code: Qt network error code
        """
        if self._output_file:
            self._output_file.close()
            self._output_file = None

        error_string = self._reply.errorString() if self._reply else "Unknown error"
        logger.error(f"Download error for {self.archive_info.filename}: {error_string}")

        # Clean up .part file
        try:
            if self._temp_path and self._temp_path.exists():
                self._temp_path.unlink()
        except Exception as e:
            logger.warning(f"Failed to delete partial file: {e}")

        if self._network_manager:
            self._network_manager.deleteLater()
            self._network_manager = None

        self.error.emit(error_string)


# ============================================================================
# Download Manager
# ============================================================================


class DownloadManager(QObject):
    """Manages multiple concurrent downloads.

    Handles download queue, concurrent download limit, and progress tracking.

    Signals:
        download_started: Emitted when download starts (mod_id)
        download_progress: Emitted on progress (mod_id, DownloadProgress)
        download_finished: Emitted when download completes (mod_id)
        download_canceled: Emitted when download is canceled (mod_id)
        download_error: Emitted on error (mod_id, error_message)
        queue_changed: Emitted when queue state changes
    """

    # Signals
    download_started = Signal(str)
    download_progress = Signal(str, object)
    download_finished = Signal(str)
    download_canceled = Signal(str)
    download_error = Signal(str, str)
    queue_changed = Signal()

    MAX_CONCURRENT_DOWNLOADS = 5

    def __init__(self, download_path: Path):
        """Initialize download manager.

        Args:
            download_path: Directory for downloaded files
        """
        super().__init__()
        self.download_path = download_path

        self._active_downloads: dict[str, tuple[DownloadWorker, QThread, DownloadProgress]] = {}
        self._download_queue: list[ArchiveInfo] = []

    def set_download_path(self, download_path: Path):
        """Update download path."""
        self.download_path = download_path

    def add_to_queue(self, archive_info: ArchiveInfo) -> None:
        """Add archive to download queue.

        Args:
            archive_info: Archive to download
        """
        if archive_info.mod_id not in self._active_downloads:
            if archive_info not in self._download_queue:
                self._download_queue.append(archive_info)
                logger.info(f"Added to queue: {archive_info.filename}")
                self.queue_changed.emit()
                self._process_queue()

    def start_download(self, archive_info: ArchiveInfo) -> None:
        """Start downloading an archive immediately.

        Args:
            archive_info: Archive to download
        """
        if archive_info.mod_id in self._active_downloads:
            logger.warning(f"Already downloading: {archive_info.filename}")
            return

        if len(self._active_downloads) >= self.MAX_CONCURRENT_DOWNLOADS:
            self.add_to_queue(archive_info)
            return

        self._start_download_worker(archive_info)

    def cancel_download(self, mod_id: str) -> None:
        """Cancel an active download.

        Args:
            mod_id: Mod identifier
        """
        try:
            if mod_id in self._active_downloads:
                worker, thread, _ = self._active_downloads[mod_id]
                archive_info = worker.archive_info
                worker.cancel()
                del self._active_downloads[mod_id]
                thread.quit()
                logger.info(f"Cancelled download: {mod_id}")

                self.download_canceled.emit(mod_id)
                self.queue_changed.emit()
                self._process_queue()
        except Exception as e:
            logger.error(f"Error cancelling download: {e}", exc_info=True)

    def get_active_downloads(self) -> list[DownloadProgress]:
        """Get list of active downloads.

        Returns:
            List of download progress objects
        """
        return [progress for _, _, progress in self._active_downloads.values()]

    def get_queue_size(self) -> int:
        """Get number of downloads in queue.

        Returns:
            Queue size
        """
        return len(self._download_queue)

    def _start_download_worker(self, archive_info: ArchiveInfo) -> None:
        """Start a download worker in a separate thread.

        Args:
            archive_info: Archive to download
        """
        try:
            thread = QThread(self)
            worker = DownloadWorker(archive_info, self.download_path)
            progress = DownloadProgress(archive_info)

            worker.moveToThread(thread)

            mod_id = archive_info.mod_id

            worker.progress.connect(
                lambda br, bt, s, mid=mod_id: self._on_worker_progress(mid, br, bt, s),
                Qt.ConnectionType.QueuedConnection,
            )
            worker.finished.connect(
                lambda fp, mid=mod_id: self._on_worker_finished(mid, fp),
                Qt.ConnectionType.QueuedConnection,
            )
            worker.error.connect(
                lambda e, mid=mod_id: self._on_worker_error(mid, e),
                Qt.ConnectionType.QueuedConnection,
            )

            # Connect thread signals
            thread.started.connect(worker.start_download)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(worker.deleteLater)

            # Store in active downloads
            self._active_downloads[mod_id] = (worker, thread, progress)

            thread.start()

            logger.info(f"Started download worker for {archive_info.filename}")
            self.download_started.emit(mod_id)
            self.queue_changed.emit()

        except Exception as e:
            logger.error(f"Failed to start download worker: {e}", exc_info=True)
            self.download_error.emit(archive_info.mod_id, str(e))

    def _on_worker_progress(
        self, mod_id: str, bytes_received: int, bytes_total: int, speed_bps: float
    ) -> None:
        """Handle worker progress update.

        Args:
            mod_id: Mod identifier
            bytes_received: Bytes downloaded
            bytes_total: Total bytes
            speed_bps: Download speed
        """
        try:
            if mod_id in self._active_downloads:
                _, _, progress = self._active_downloads[mod_id]
                progress.bytes_received = bytes_received
                progress.bytes_total = bytes_total
                progress.speed_bps = speed_bps
                self.download_progress.emit(mod_id, progress)
        except Exception as e:
            logger.error(f"Error in progress handler: {e}", exc_info=True)

    def _on_worker_finished(self, mod_id: str, file_path: str) -> None:
        """Handle worker completion.

        Args:
            mod_id: Mod identifier
            file_path: Path to downloaded file
        """
        try:
            logger.debug(f"Worker finished for {mod_id}")

            if mod_id in self._active_downloads:
                _, thread, _ = self._active_downloads[mod_id]
                del self._active_downloads[mod_id]
                thread.quit()
                logger.info(f"Download completed: {mod_id}")

            self.download_finished.emit(mod_id)
            self.queue_changed.emit()
            self._process_queue()

        except Exception as e:
            logger.error(f"Error in finished handler: {e}", exc_info=True)

    def _on_worker_error(self, mod_id: str, error_message: str) -> None:
        """Handle worker error.

        Args:
            mod_id: Mod identifier
            error_message: Error description
        """
        try:
            logger.debug(f"Worker error for {mod_id}: {error_message}")

            if mod_id in self._active_downloads:
                _, thread, _ = self._active_downloads[mod_id]
                del self._active_downloads[mod_id]
                thread.quit()
                logger.error(f"Download failed: {mod_id}")

            self.download_error.emit(mod_id, error_message)
            self.queue_changed.emit()
            self._process_queue()
        except Exception as e:
            logger.error(f"Error in error handler: {e}", exc_info=True)

    def _process_queue(self) -> None:
        """Process download queue to start new downloads."""
        try:
            logger.debug(
                f"Processing queue: {len(self._download_queue)} queued, "
                f"{len(self._active_downloads)} active"
            )

            while (
                len(self._active_downloads) < self.MAX_CONCURRENT_DOWNLOADS
                and self._download_queue
            ):
                archive_info = self._download_queue.pop(0)
                self._start_download_worker(archive_info)
                self.queue_changed.emit()
        except Exception as e:
            logger.error(f"Error processing queue: {e}", exc_info=True)
