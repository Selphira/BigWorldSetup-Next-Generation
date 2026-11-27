"""Wait for a resolution search run off the interface thread, with a cancel button."""

from PySide6.QtCore import QEventLoop, Qt, QThread, Signal
from PySide6.QtWidgets import QProgressDialog

from constants import RESOLUTION_SEARCH_DIALOG_DELAY_MS
from core.TranslationManager import tr
from core.ValidationOrchestrator import (
    ResolutionCancelled,
    ResolutionProposal,
    ResolutionRequest,
    ValidationOrchestrator,
)


class ResolutionSearchWorker(QThread):
    """Runs `ValidationOrchestrator.search_resolution` on a frozen request."""

    resolved = Signal(object)  # ResolutionProposal | None
    failed = Signal(object)  # Exception

    def __init__(
        self, orchestrator: ValidationOrchestrator, request: ResolutionRequest
    ) -> None:
        super().__init__()
        self._orchestrator = orchestrator
        self._request = request

    def run(self) -> None:
        try:
            proposal = self._orchestrator.search_resolution(
                self._request, self.isInterruptionRequested
            )
        except ResolutionCancelled:
            return
        except Exception as error:
            self.failed.emit(error)
            return
        self.resolved.emit(proposal)


class ResolutionSearchDialog(QProgressDialog):
    """Modal wait shown once a search lasts; cancelling interrupts the worker.

    The dialog has no parent window: deleting the window that was active when
    the search started must not delete the dialog during the wait.
    """

    def __init__(
        self, orchestrator: ValidationOrchestrator, request: ResolutionRequest
    ) -> None:
        super().__init__(tr("page.selection.resolution_searching"), tr("button.cancel"), 0, 0)
        self.setWindowTitle(tr("page.selection.violation.keep_this_component"))
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        self._proposal: ResolutionProposal | None = None
        self._error: Exception | None = None
        self._loop = QEventLoop(self)

        self._worker = ResolutionSearchWorker(orchestrator, request)
        self._worker.resolved.connect(self._on_resolved)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._loop.quit)
        self.canceled.connect(self._worker.requestInterruption)

    def search(self) -> ResolutionProposal | None:
        """Run the search until its end; ``None`` when no transition keeps the component.

        Raises:
            ResolutionCancelled: the user cancelled the search.
        """
        # A fast search ends before the dialog shows: setValue(0) starts its delay
        self.setMinimumDuration(RESOLUTION_SEARCH_DIALOG_DELAY_MS)
        self.setValue(0)

        self._worker.start()
        self._loop.exec()
        self._worker.wait()

        cancelled = self.wasCanceled()
        self.reset()
        self._worker.deleteLater()

        if cancelled:
            raise ResolutionCancelled()
        if self._error is not None:
            raise self._error
        return self._proposal

    def reject(self) -> None:
        """Escape cancels the search like the cancel button."""
        self.canceled.emit()

    def _on_resolved(self, proposal: ResolutionProposal | None) -> None:
        self._proposal = proposal

    def _on_failed(self, error: Exception) -> None:
        self._error = error
