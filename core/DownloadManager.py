from collections import deque
from dataclasses import astuple, dataclass
from enum import Enum
import hashlib
import logging
from pathlib import Path
import time

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from constants import (
    DOWNLOAD_MAX_ATTEMPTS,
    DOWNLOAD_RETRY_BASE_DELAY_MS,
    DOWNLOAD_TRANSFER_TIMEOUT_MS,
)
from core.TranslationManager import tr

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
        DOWNLOADING: Archive is currently being downloaded
        ERROR: Download or verification error
        MANUAL_EXPECTED: No automatic download; the user must associate an archive
        NO_REFERENCE: Archive present but the catalogue publishes no hash to prove it
        MISMATCH_ACCEPTED: Hash differs from the catalogue and the user accepted it
    """

    VALID = "valid"
    MANUAL_EXPECTED = "manual_expected"
    NO_REFERENCE = "no_reference"
    MISMATCH_ACCEPTED = "mismatch_accepted"
    INVALID_HASH = "invalid_hash"
    INVALID_SIZE = "invalid_size"
    MISSING = "missing"
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
    def is_usable(self) -> bool:
        """Check if the archive may be extracted, with a proof of variable strength.

        Returns:
            True if the hash matches, cannot be compared, or was explicitly accepted
        """
        return self in (
            ArchiveStatus.VALID,
            ArchiveStatus.NO_REFERENCE,
            ArchiveStatus.MISMATCH_ACCEPTED,
        )


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

    def __str__(self) -> str:
        """String representation for logging."""
        return f"{self.mod_id}: {self.filename}"


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

    The body is written to a `.part` file. It replaces the final archive only
    when the hash matches the catalogue, or when there is no previous archive
    to preserve; the hash is computed here, never on the UI thread.

    Signals:
        progress: Emitted on download progress (bytes_received, bytes_total, speed_bps);
            byte counts are Python ints so that archives above 2 GiB are reported
        finished: Emitted when download completes successfully (file_path)
        error: Emitted on download error (error_message)
    """

    # Signals
    cancel_requested = Signal()
    progress = Signal(object, object, float)
    finished = Signal(str)
    error = Signal(str)

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
    RETRYABLE_ERRORS = frozenset(
        {
            QNetworkReply.NetworkError.TimeoutError,
            QNetworkReply.NetworkError.OperationCanceledError,
            QNetworkReply.NetworkError.RemoteHostClosedError,
            QNetworkReply.NetworkError.TemporaryNetworkFailureError,
            QNetworkReply.NetworkError.NetworkSessionFailedError,
            QNetworkReply.NetworkError.ProxyTimeoutError,
            QNetworkReply.NetworkError.UnknownNetworkError,
        }
    )

    def __init__(self, archive_info: ArchiveInfo, download_path: Path):
        """Initialize download worker.

        Args:
            archive_info: Archive to download
            download_path: Directory to save the file
        """
        super().__init__()
        self.cancel_requested.connect(self.cancel, Qt.ConnectionType.QueuedConnection)
        self.archive_info = archive_info
        self.download_path = download_path
        self._network_manager: QNetworkAccessManager | None = None
        self._reply: QNetworkReply | None = None
        self._start_time = 0.0
        self._output_file = None
        self._temp_path: Path | None = None
        self._attempt = 0
        self._failure: str | None = None
        self._cancelled = False
        self._done = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @Slot()
    def start_download(self) -> None:
        """Start the first attempt."""
        if not self.archive_info.url or not self.archive_info.filename:
            self._conclude_error(tr("page.download.error.no_url"))
            return

        self._temp_path = self.download_path / (self.archive_info.filename + ".part")
        self._start_attempt()

    @Slot()
    def cancel(self) -> None:
        """Cancel the download and delete partial file."""
        logger.info(f"Cancelled download: {self.archive_info.filename}")
        self._cancelled = True
        if self._reply:
            # The reply then finishes and `_on_reply_finished` concludes.
            self._reply.abort()
            return
        # Waiting between two attempts: nothing else will conclude.
        self._conclude_error("cancelled")

    def _start_attempt(self) -> None:
        if self._cancelled or self._done:
            return

        self._attempt += 1
        self._failure = None
        try:
            # Kept open across network callbacks, closed when the attempt concludes.
            self._output_file = open(self._temp_path, "wb")  # noqa: SIM115
        except OSError as error:
            self._conclude_error(tr("page.download.error.disk", error=str(error)))
            return

        if self._network_manager is None:
            self._network_manager = QNetworkAccessManager(self)
        self._reply = self._send_request(self._build_request())
        self._reply.readyRead.connect(self._on_ready_read)
        self._reply.downloadProgress.connect(self._on_download_progress)
        self._reply.finished.connect(self._on_reply_finished)
        self._start_time = time.monotonic()

        logger.info(
            "Started download: %s (attempt %d/%d)",
            self.archive_info.filename,
            self._attempt,
            DOWNLOAD_MAX_ATTEMPTS,
        )

    def _build_request(self) -> QNetworkRequest:
        request = QNetworkRequest(self.archive_info.url)
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, self.USER_AGENT)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        request.setTransferTimeout(DOWNLOAD_TRANSFER_TIMEOUT_MS)
        return request

    def _send_request(self, request: QNetworkRequest) -> QNetworkReply:
        """Issue the request; separated so tests can substitute a local reply."""
        return self._network_manager.get(request)

    # ------------------------------------------------------------------
    # Reply handling
    # ------------------------------------------------------------------

    def _on_ready_read(self) -> None:
        """Write the received data, refusing a body that cannot be the archive."""
        if not self._reply or not self._output_file or self._failure:
            return

        self._failure = self._unexpected_response()
        if self._failure:
            self._reply.abort()
            return

        try:
            self._output_file.write(self._reply.readAll().data())
        except OSError as error:
            self._failure = tr("page.download.error.disk", error=str(error))
            self._reply.abort()

    def _on_download_progress(self, bytes_received: int, bytes_total: int) -> None:
        elapsed = time.monotonic() - self._start_time
        speed_bps = bytes_received / elapsed if elapsed > 0 else 0.0

        if bytes_total < 0:
            bytes_total = self.archive_info.file_size

        self.progress.emit(int(bytes_received), int(bytes_total), speed_bps)

    def _on_reply_finished(self) -> None:
        """Conclude the attempt exactly once, whatever ended it."""
        reply, self._reply = self._reply, None
        if reply is None:
            return
        self._close_output()

        network_error = reply.error()
        failure = self._failure or self._unexpected_response(reply)
        error_string = reply.errorString()
        reply.deleteLater()

        if self._cancelled:
            self._discard_partial()
            self._conclude_error("cancelled")
            return

        if failure is None and network_error == QNetworkReply.NetworkError.NoError:
            self._install_downloaded_file()
            return

        self._discard_partial()
        if failure is None and self._should_retry(network_error):
            delay = DOWNLOAD_RETRY_BASE_DELAY_MS * 2 ** (self._attempt - 1)
            logger.warning(
                "Download of %s failed (%s), retry in %d ms",
                self.archive_info.filename,
                error_string,
                delay,
            )
            QTimer.singleShot(delay, self._start_attempt)
            return

        self._conclude_error(failure or error_string)

    def _unexpected_response(self, reply: QNetworkReply | None = None) -> str | None:
        """Explain why the response cannot be the expected archive, if so."""
        reply = reply or self._reply
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status in (403, 404, 410):
            return tr("page.download.error.http_status", status=status)

        content_type = reply.header(QNetworkRequest.KnownHeaders.ContentTypeHeader) or ""
        if str(content_type).lower().startswith("text/html") and not self._expects_html():
            return tr("page.download.error.html")
        return None

    def _expects_html(self) -> bool:
        return self.archive_info.filename.lower().endswith((".htm", ".html"))

    def _should_retry(self, network_error: QNetworkReply.NetworkError) -> bool:
        if self._attempt >= DOWNLOAD_MAX_ATTEMPTS:
            return False
        if network_error in self.RETRYABLE_ERRORS:
            return True
        # Server-side 5xx errors are reported as InternalServerError & co.
        return network_error in (
            QNetworkReply.NetworkError.InternalServerError,
            QNetworkReply.NetworkError.ServiceUnavailableError,
        )

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def _install_downloaded_file(self) -> None:
        """Replace the final archive unless that would destroy a better one.

        A previous archive is preserved when the new body does not match the
        catalogue hash: the catalogue may lag behind the mod, but the user then
        decides, and an archive they may have accepted is never lost. Without a
        reference hash nothing distinguishes them, and the requested download wins.
        """
        final_path = self.download_path / self.archive_info.filename
        try:
            if (
                final_path.exists()
                and self.archive_info.expected_hash
                and not self._matches_expected_hash(self._temp_path)
            ):
                self._discard_partial()
                self._conclude_error(tr("page.download.error.kept_previous"))
                return
            self._temp_path.replace(final_path)
        except OSError as error:
            logger.error(f"Failed to install downloaded file: {error}")
            self._discard_partial()
            self._conclude_error(tr("page.download.error.disk", error=str(error)))
            return

        logger.info(f"Download completed: {self.archive_info.filename}")
        self._done = True
        self._release_network()
        self.finished.emit(str(final_path))

    def _matches_expected_hash(self, path: Path) -> bool:
        actual = ArchiveVerifier().calculate_hash(path, self.archive_info.hash_algorithm)
        return actual.lower() == self.archive_info.expected_hash.lower()

    def _close_output(self) -> None:
        if self._output_file:
            try:
                self._output_file.close()
            except OSError as error:
                self._failure = self._failure or tr(
                    "page.download.error.disk", error=str(error)
                )
            self._output_file = None

    def _discard_partial(self) -> None:
        try:
            if self._temp_path:
                self._temp_path.unlink(missing_ok=True)
        except OSError as error:
            logger.warning(f"Failed to delete partial file: {error}")

    def _conclude_error(self, message: str) -> None:
        if self._done:
            return
        self._done = True
        self._close_output()
        self._release_network()
        if message != "cancelled":
            logger.error(f"Download error for {self.archive_info.filename}: {message}")
        self.error.emit(message)

    def _release_network(self) -> None:
        if self._network_manager:
            self._network_manager.deleteLater()
            self._network_manager = None


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
    all_completed = Signal()

    MAX_CONCURRENT_DOWNLOADS = 5

    def __init__(self):
        """Initialize download manager."""
        super().__init__()
        self.download_path: Path | None = None

        self._cancelling: set[str] = set()
        self._active_downloads: dict[str, tuple[DownloadWorker, QThread, DownloadProgress]] = {}
        self._download_queue: deque[ArchiveInfo] = deque()
        self._queued_keys: set[tuple] = set()

    def set_download_path(self, download_path: Path):
        """Update download path."""
        self.download_path = download_path

    def add_to_queue(self, archive_info: ArchiveInfo) -> None:
        """Add archive to download queue.

        Args:
            archive_info: Archive to download
        """
        if (
            archive_info.mod_id in self._active_downloads
            or self._queue_key(archive_info) in self._queued_keys
        ):
            return

        self._download_queue.append(archive_info)
        self._queued_keys.add(self._queue_key(archive_info))
        logger.info(f"Added to queue: {archive_info.filename}")
        self.queue_changed.emit()
        self._process_queue()

    def start_download(self, archive_info: ArchiveInfo) -> None:
        """Start downloading an archive immediately.

        Args:
            archive_info: Archive to download
        """
        if self.download_path is None:
            logger.error("No download path")
            return

        if archive_info.mod_id in self._active_downloads:
            logger.warning(f"Already downloading: {archive_info.filename}")
            return

        if len(self._active_downloads) >= self.MAX_CONCURRENT_DOWNLOADS:
            self.add_to_queue(archive_info)
            return

        self._start_download_worker(archive_info)

    def cancel_all(self) -> None:
        """Drop queued downloads and cancel the active ones."""
        self._download_queue.clear()
        self._queued_keys.clear()
        for mod_id in list(self._active_downloads):
            self.cancel_download(mod_id)

    def cancel_download(self, mod_id: str) -> None:
        """Cancel an active download inside its own thread.

        The worker reports the cancellation through its error signal, which
        is then emitted as `download_canceled`.

        Args:
            mod_id: Mod identifier
        """
        if mod_id in self._active_downloads:
            self._cancelling.add(mod_id)
            worker, _, _ = self._active_downloads[mod_id]
            worker.cancel_requested.emit()

    def get_active_downloads(self) -> list[DownloadProgress]:
        """Get list of active downloads.

        Returns:
            List of download progress objects
        """
        return [progress for _, _, progress in self._active_downloads.values()]

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
                self._on_worker_progress, Qt.ConnectionType.QueuedConnection
            )
            worker.finished.connect(
                self._on_worker_finished, Qt.ConnectionType.QueuedConnection
            )
            worker.error.connect(self._on_worker_error, Qt.ConnectionType.QueuedConnection)

            thread.started.connect(worker.start_download)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(worker.deleteLater)

            self._active_downloads[mod_id] = (worker, thread, progress)

            thread.start()

            logger.info(f"Started download worker for {archive_info.filename}")
            self.download_started.emit(mod_id)
            self.queue_changed.emit()

        except Exception as e:
            logger.error(f"Failed to start download worker: {e}", exc_info=True)
            self.download_error.emit(archive_info.mod_id, str(e))

    @Slot(object, object, float)
    def _on_worker_progress(
        self, bytes_received: int, bytes_total: int, speed_bps: float
    ) -> None:
        """Handle worker progress update.

        Args:
            bytes_received: Bytes downloaded
            bytes_total: Total bytes
            speed_bps: Download speed
        """
        mod_id = self.sender().archive_info.mod_id
        try:
            if mod_id in self._active_downloads:
                _, _, progress = self._active_downloads[mod_id]
                progress.bytes_received = bytes_received
                progress.bytes_total = bytes_total
                progress.speed_bps = speed_bps
                self.download_progress.emit(mod_id, progress)
        except Exception as e:
            logger.error(f"Error in progress handler: {e}", exc_info=True)

    @Slot(str)
    def _on_worker_finished(self, file_path: str) -> None:
        """Handle worker completion.

        Args:
            file_path: Path to downloaded file
        """
        mod_id = self.sender().archive_info.mod_id
        try:
            logger.debug(f"Worker finished for {mod_id}")

            if mod_id in self._active_downloads:
                _, thread, _ = self._active_downloads[mod_id]
                del self._active_downloads[mod_id]
                thread.quit()
                logger.info(f"Download completed: {mod_id}")

            self._cancelling.discard(mod_id)
            self.download_finished.emit(mod_id)
            self.queue_changed.emit()
            self._process_queue()
            self._check_completion()

        except Exception as e:
            logger.error(f"Error in finished handler: {e}", exc_info=True)

    @Slot(str)
    def _on_worker_error(self, error_message: str) -> None:
        """Handle worker error.

        Args:
            error_message: Error description
        """
        mod_id = self.sender().archive_info.mod_id
        try:
            logger.debug(f"Worker error for {mod_id}: {error_message}")

            if mod_id in self._active_downloads:
                _, thread, _ = self._active_downloads[mod_id]
                del self._active_downloads[mod_id]
                thread.quit()
                logger.error(f"Download failed: {mod_id}")

            if mod_id in self._cancelling:
                self._cancelling.remove(mod_id)
                self.download_canceled.emit(mod_id)
            else:
                self.download_error.emit(mod_id, error_message)
            self.queue_changed.emit()
            self._process_queue()
            self._check_completion()
        except Exception as e:
            logger.error(f"Error in error handler: {e}", exc_info=True)

    def _check_completion(self) -> None:
        """Check if all downloads are complete and emit signal if so."""
        if not self._active_downloads and not self._download_queue:
            logger.info("All downloads completed")
            self.all_completed.emit()

    @staticmethod
    def _queue_key(archive_info: ArchiveInfo) -> tuple:
        """Return a hashable key equal for archives that compare equal.

        `ArchiveInfo` is a mutable dataclass, hence unhashable; its field
        tuple reproduces the dataclass equality used to reject duplicates.
        """
        return astuple(archive_info)

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
                archive_info = self._download_queue.popleft()
                self._queued_keys.discard(self._queue_key(archive_info))
                self._start_download_worker(archive_info)
                self.queue_changed.emit()
        except Exception as e:
            logger.error(f"Error processing queue: {e}", exc_info=True)
