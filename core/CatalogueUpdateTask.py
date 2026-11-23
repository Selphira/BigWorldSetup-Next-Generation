"""
Startup catalogue update, run in a worker thread so that the interface stays responsive.

The task can be abandoned at any time before the activation of a downloaded
catalogue: the application then continues with the current catalogue,
while the abandoned thread ends on its own without touching it.
"""

from enum import Enum
import logging

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from constants import DOWNLOAD_TIMEOUT, STARTUP_CATALOGUE_CHECK_TIMEOUT_MS
from core.DataUpdater import DataUpdater

logger = logging.getLogger(__name__)


class CatalogueUpdateOutcome(Enum):
    """How the startup catalogue update ended."""

    UPDATED = "updated"
    NOT_UPDATED = "not_updated"  # Up to date, unreachable or incompatible
    FAILED = "failed"
    CANCELLED = "cancelled"


class _CatalogueUpdateThread(QThread):
    """Check the remote catalogue, then download and activate it when newer."""

    checked = Signal(bool)  # True if an update is available
    outcome_ready = Signal(object)  # CatalogueUpdateOutcome

    def __init__(self, updater: DataUpdater) -> None:
        super().__init__()
        self._updater = updater

    def run(self) -> None:
        try:
            update_available = self._updater.check_for_updates()
            self.checked.emit(update_available)

            if not update_available:
                self.outcome_ready.emit(CatalogueUpdateOutcome.NOT_UPDATED)
                return

            logger.info("Data updates available, downloading...")
            updated = self._updater.update_data()
            self.outcome_ready.emit(
                CatalogueUpdateOutcome.UPDATED if updated else CatalogueUpdateOutcome.FAILED
            )

        except Exception:
            logger.exception("Error during data update")
            self.outcome_ready.emit(CatalogueUpdateOutcome.FAILED)


class CatalogueUpdateTask(QObject):
    """
    Drive the startup catalogue update and report a single terminal outcome.

    The availability check is abandoned once its timeout expires; the download
    of an available update is only limited by the network timeouts.

    Signals:
        finished: CatalogueUpdateOutcome, emitted exactly once
        status_changed: Status message of the update
        progress: Progress percentage of the update
    """

    finished = Signal(object)
    status_changed = Signal(str)
    progress = Signal(int)

    def __init__(
        self,
        updater: DataUpdater | None = None,
        check_timeout_ms: int = STARTUP_CATALOGUE_CHECK_TIMEOUT_MS,
    ) -> None:
        super().__init__()
        self._updater = updater or DataUpdater()
        self._thread = _CatalogueUpdateThread(self._updater)
        self._is_finished = False
        self._errors: list[str] = []

        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(check_timeout_ms)
        self._check_timer.timeout.connect(self._on_check_timeout)

        self._updater.status_changed.connect(self._on_status_changed)
        self._updater.progress.connect(self._on_progress)
        self._updater.update_error.connect(self._errors.append)
        self._thread.checked.connect(self._on_checked)
        self._thread.outcome_ready.connect(self._finish)

    @property
    def errors(self) -> list[str]:
        """Error messages reported by the update."""
        return list(self._errors)

    def start(self) -> None:
        """Start the update in its worker thread."""
        self._check_timer.start()
        self._thread.start()

    def cancel(self) -> bool:
        """
        Skip the update and keep the current catalogue.

        Returns:
            True if the task finished as cancelled, False if a catalogue
            activation is in progress and its outcome must be awaited
        """
        if self._is_finished:
            return False

        if not self._updater.request_cancel():
            logger.info("Catalogue activation in progress: cancellation refused")
            return False

        logger.info("Catalogue update skipped, keeping the current catalogue")
        self._finish(CatalogueUpdateOutcome.CANCELLED)
        return True

    def wait_for_thread(self) -> None:
        """Wait for the worker thread, which a cancellation does not interrupt."""
        if self._thread.isRunning():
            self._updater.request_cancel()
            self._thread.wait((DOWNLOAD_TIMEOUT + 1) * 1000)

    # ========================================
    # WORKER NOTIFICATIONS
    # ========================================

    def _on_checked(self, update_available: bool) -> None:
        self._check_timer.stop()

    def _on_check_timeout(self) -> None:
        logger.warning("Catalogue availability check timed out")
        self.cancel()

    def _on_status_changed(self, message: str) -> None:
        if not self._is_finished:
            self.status_changed.emit(message)

    def _on_progress(self, value: int) -> None:
        if not self._is_finished:
            self.progress.emit(value)

    def _finish(self, outcome: CatalogueUpdateOutcome) -> None:
        """Report the first outcome; the late result of an abandoned thread is ignored."""
        if self._is_finished:
            return

        self._is_finished = True
        self._check_timer.stop()
        logger.info(f"Catalogue update outcome: {outcome.value}")
        self.finished.emit(outcome)
