"""
Installation page for mod installation process.
"""

from collections.abc import Callable
from enum import Enum, auto
import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from constants import (
    BUTTON_ICON_CANCEL,
    BUTTON_ICON_DIAGNOSTIC,
    BUTTON_ICON_FINISH,
    BUTTON_ICON_PAUSE,
    BUTTON_ICON_PAUSE_PENDING,
    BUTTON_ICON_START,
    BUTTON_ICON_STOP,
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_STATUS_COMPLETE,
    COLOR_WARNING,
    DECISION_DETAILS_MIN_HEIGHT,
    DECISION_DIALOG_MIN_WIDTH,
    DIAGNOSTIC_FILE_FILTER,
    DIAGNOSTIC_FILE_SUFFIX,
    INSTALL_OUTPUT_RULE_WIDTH,
    MARGIN_STANDARD,
    PROCESS_STOP_GRACE_MS,
    PROGRESS_IDLE_FORMAT,
    SPACING_LARGE,
    SPACING_SMALL,
)
from core.ComponentReference import IndexManager
from core.GameModels import GameDefinition
from core.InstallationDiagnostic import (
    InstallationDiagnostic,
    describe_evidence,
    describe_log_presence,
)
from core.InstallationExecutor import InstallationError, InstallationExecutor
from core.InstallationJournal import InstallationJournal, OperationDecision, RunOutcome
from core.InstallationReconciler import ReconciliationReport
from core.InstallationWorker import InstallationWorker, UserDecision
from core.StateManager import StateManager
from core.TranslationManager import tr
from core.weidu_types import ComponentStatus, InstallResult
from ui.ButtonIcons import button_icon
from ui.pages.BasePage import BasePage, ButtonConfig

logger = logging.getLogger(__name__)


# ============================================================================
# Installation Statistics Widget
# ============================================================================


class InstallationStatsWidget(QFrame):
    """Widget displaying installation statistics."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._create_widgets()
        self.reset()

    def _create_widgets(self):
        """Create widget layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_LARGE)
        layout.setContentsMargins(0, 0, 0, 0)

        self._lbl_success = self._create_stat_label(COLOR_STATUS_COMPLETE)
        layout.addWidget(self._lbl_success)

        self._lbl_warnings = self._create_stat_label(COLOR_WARNING)
        layout.addWidget(self._lbl_warnings)

        self._lbl_errors = self._create_stat_label(COLOR_ERROR)
        layout.addWidget(self._lbl_errors)

        self._lbl_skipped = QLabel()
        layout.addWidget(self._lbl_skipped)

    @staticmethod
    def _create_stat_label(color: str) -> QLabel:
        """Create a styled statistics label."""
        label = QLabel()
        label.setStyleSheet(f"color: {color};")
        return label

    def update_stats(
        self, success: int = 0, warnings: int = 0, errors: int = 0, skipped: int = 0
    ):
        """Update statistics display."""
        self._lbl_success.setText(tr("page.installation.stats.success", count=success))
        self._lbl_warnings.setText(tr("page.installation.stats.warnings", count=warnings))
        self._lbl_errors.setText(tr("page.installation.stats.errors", count=errors))
        self._lbl_skipped.setText(tr("page.installation.stats.skipped", count=skipped))

    def reset(self):
        """Reset all statistics to zero."""
        self.update_stats(0, 0, 0, 0)


# ============================================================================
# Dialogs
# ============================================================================


class DecisionDialog(QDialog):
    """Ask the user to choose how an installation step goes on.

    ``decision`` keeps ``default`` when the dialog is closed without a choice.
    ``on_diagnostic`` adds a button exporting the diagnostic without closing the dialog.
    """

    def __init__(
        self,
        title: str,
        message: str,
        details: list[str],
        choices: tuple[tuple[str, Enum], ...],
        default: Enum | None,
        cancellable: bool = False,
        on_diagnostic: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        if details:
            self.setMinimumWidth(DECISION_DIALOG_MIN_WIDTH)
        self.decision: Enum | None = default

        self._create_widgets(message, details, choices, cancellable, on_diagnostic)

    def _create_widgets(
        self,
        message: str,
        details: list[str],
        choices: tuple[tuple[str, Enum], ...],
        cancellable: bool,
        on_diagnostic: Callable[[], None] | None,
    ) -> None:
        """Create dialog layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_LARGE)

        msg = QLabel(message)
        msg.setWordWrap(True)
        layout.addWidget(msg)

        if details:
            details_text = QTextEdit()
            details_text.setReadOnly(True)
            details_text.setMinimumHeight(DECISION_DETAILS_MIN_HEIGHT)
            details_text.setPlainText("\n".join(details))
            layout.addWidget(details_text)

        button_layout = QHBoxLayout()
        for label, decision in choices:
            button = self._create_button(label)
            button.clicked.connect(lambda _=False, choice=decision: self._make_decision(choice))
            button_layout.addWidget(button)

        if cancellable:
            btn_cancel = self._create_button("page.installation.btn_cancel")
            btn_cancel.clicked.connect(self.reject)
            button_layout.addWidget(btn_cancel)

        if on_diagnostic:
            button_layout.addStretch()
            btn_diagnostic = self._create_button("page.installation.btn_diagnostic")
            btn_diagnostic.clicked.connect(on_diagnostic)
            button_layout.addWidget(btn_diagnostic)

        layout.addLayout(button_layout)

    @staticmethod
    def _create_button(label: str) -> QPushButton:
        button = QPushButton(tr(label))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _make_decision(self, decision: Enum) -> None:
        """Record decision and close."""
        self.decision = decision
        self.accept()


ERROR_CHOICES: tuple[tuple[str, UserDecision], ...] = (
    ("page.installation.btn_retry", UserDecision.RETRY),
    ("page.installation.btn_continue", UserDecision.SKIP),
    ("page.installation.btn_pause", UserDecision.PAUSE),
    ("page.installation.btn_stop", UserDecision.STOP),
)
WARNING_CHOICES: tuple[tuple[str, UserDecision], ...] = (
    ("page.installation.btn_continue", UserDecision.SKIP),
    ("page.installation.btn_pause", UserDecision.PAUSE),
    ("page.installation.btn_stop", UserDecision.STOP),
)
OPERATION_CHOICES: tuple[tuple[str, OperationDecision], ...] = (
    ("page.installation.decision_installed", OperationDecision.CONFIRM_INSTALLED),
    ("page.installation.decision_retry", OperationDecision.RETRY),
    ("page.installation.decision_failed", OperationDecision.ACCEPT_FAILURE),
)


class RunAction(Enum):
    """Action of the main button, which depends on the state of the run."""

    START = auto()
    RESUME = auto()
    PAUSE = auto()
    PAUSE_PENDING = auto()
    FINISH = auto()


# Label, tooltip and icon of the main button for each action
RUN_ACTION_BUTTONS: dict[RunAction, tuple[str, str, str]] = {
    RunAction.START: (
        "page.installation.btn_start",
        "page.installation.btn_start_tooltip",
        BUTTON_ICON_START,
    ),
    RunAction.RESUME: (
        "page.installation.btn_resume",
        "page.installation.btn_resume_tooltip",
        BUTTON_ICON_START,
    ),
    RunAction.PAUSE: (
        "page.installation.btn_pause",
        "page.installation.btn_pause_tooltip",
        BUTTON_ICON_PAUSE,
    ),
    RunAction.PAUSE_PENDING: (
        "page.installation.btn_pause_pending",
        "page.installation.btn_pause_pending_tooltip",
        BUTTON_ICON_PAUSE_PENDING,
    ),
    RunAction.FINISH: (
        "page.installation.btn_finish",
        "page.installation.btn_finish_tooltip",
        BUTTON_ICON_FINISH,
    ),
}


# ============================================================================
# Installation Page
# ============================================================================


class InstallationPage(BasePage):
    """Page presenting an installation run; InstallationExecutor makes the decisions."""

    def __init__(self, state_manager: StateManager):
        super().__init__(state_manager)

        self._mod_manager = self.state_manager.get_mod_manager()
        self._executor = InstallationExecutor(state_manager)
        self._worker: InstallationWorker | None = None
        self._current_sequence = 0
        self._cancelling = False

        # Preferences
        self._batch_install: bool = True
        self._pause_on_error: bool = True
        self._pause_on_warning: bool = True

        # State tracking
        self._is_installing = False
        self._is_paused = False
        # Pause requested; the worker honours it once the current batch ends
        self._pause_pending = False
        self._stats = {"success": 0, "warnings": 0, "errors": 0, "skipped": 0}

        # UI components
        self._lbl_log: QLabel | None = None
        self._output_text: QTextEdit | None = None
        self._input_text: QLineEdit | None = None
        self._progress_bar: QProgressBar | None = None
        self._stats_widget: InstallationStatsWidget | None = None
        self._btn_run_action: QPushButton | None = None
        self._btn_stop: QPushButton | None = None
        self._btn_cancel: QPushButton | None = None
        self._btn_diagnostic: QPushButton | None = None

        self._create_widgets()
        self._create_additional_buttons()

        logger.info("InstallPage initialized")

    # ========================================
    # UI Creation
    # ========================================

    def _create_widgets(self):
        """Create page UI layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(
            MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD
        )

        hlayout = QHBoxLayout()
        hlayout.setSpacing(SPACING_LARGE)
        hlayout.setContentsMargins(0, 0, 0, 0)

        hlayout.addWidget(self._create_left_panel())
        hlayout.addWidget(self._create_right_panel())

        layout.addLayout(hlayout)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat(PROGRESS_IDLE_FORMAT)
        layout.addWidget(self._progress_bar)

    def _create_left_panel(self) -> QWidget:
        """Create left panel."""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(0, 0, 0, 0)

        self._lbl_log = self._create_section_title()
        layout.addWidget(self._lbl_log)

        self._output_text = QTextEdit()
        self._output_text.setReadOnly(True)
        self._output_text.setFontFamily("Consolas, Monaco, monospace")
        self._output_text.setStyleSheet("QTextEdit { font-size: 10pt; }")
        layout.addWidget(self._output_text, stretch=1)

        return panel

    def _create_right_panel(self) -> QWidget:
        """Create right panel."""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setMaximumWidth(200)

        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(0, 0, 0, 0)

        # Stats widget
        self._stats_widget = InstallationStatsWidget()
        layout.addWidget(self._stats_widget)

        layout.addStretch()

        self._cb_batch_install = QCheckBox()
        self._cb_batch_install.stateChanged.connect(self._on_batch_install_changed)
        self._cb_batch_install.setChecked(True)
        layout.addWidget(self._cb_batch_install)

        self._cb_pause_on_warning = QCheckBox()
        self._cb_pause_on_warning.stateChanged.connect(self._on_pause_on_warning_changed)
        self._cb_pause_on_warning.setChecked(True)
        layout.addWidget(self._cb_pause_on_warning)

        self._cb_pause_on_error = QCheckBox()
        self._cb_pause_on_error.stateChanged.connect(self._on_pause_on_error_changed)
        self._cb_pause_on_error.setChecked(True)
        layout.addWidget(self._cb_pause_on_error)

        hlayout = QHBoxLayout()
        self._input_text = QLineEdit()
        self._input_text.setEnabled(False)
        self._input_text.setMaximumWidth(150)
        hlayout.addWidget(self._input_text)

        self._btn_send_input = self._create_action_button(self._send_input_text, enabled=False)
        hlayout.addWidget(self._btn_send_input)

        layout.addLayout(hlayout)

        return panel

    def _create_additional_buttons(self):
        """Create action buttons."""
        self._btn_run_action = self._create_action_button(self._on_run_action)

        self._btn_stop = self._create_action_button(self._stop_installation, enabled=False)
        self._btn_stop.setIcon(button_icon(BUTTON_ICON_STOP))

        self._btn_cancel = self._create_action_button(self._cancel_installation, enabled=False)
        self._btn_cancel.setIcon(button_icon(BUTTON_ICON_CANCEL))

        self._btn_diagnostic = self._create_action_button(self._export_diagnostic)
        self._btn_diagnostic.setIcon(button_icon(BUTTON_ICON_DIAGNOSTIC))

    # ========================================
    # Installation Control
    # ========================================

    def _run_action(self) -> RunAction:
        """Action offered by the main button in the current state of the run."""
        if self._is_installing:
            if self._is_paused:
                return RunAction.RESUME
            return RunAction.PAUSE_PENDING if self._pause_pending else RunAction.PAUSE
        if self._executor.is_played:
            return RunAction.FINISH
        return RunAction.RESUME if self.is_resume() else RunAction.START

    def _on_run_action(self) -> None:
        """Start, pause, resume or finish the run, depending on its state."""
        action = self._run_action()
        if not self._is_installing and action in (RunAction.START, RunAction.RESUME):
            self._start_installation()
        elif action == RunAction.RESUME:
            self._resume_installation()
        elif action == RunAction.PAUSE:
            self._pause_installation()
        elif action == RunAction.FINISH:
            self._finish_run()

    def _start_installation(self):
        """Start a new run, or resume the active one once its state is decided."""
        if self._is_installing:
            return

        self._executor.load()
        if not self._ensure_readable_journal():
            return
        try:
            locked = self._executor.acquire()
        except OSError as error:
            self._report_problem(str(error))
            return
        if not locked:
            self._report_problem(tr("page.installation.active_run"))
            return

        try:
            report = self._executor.start(self._batch_install)
        except InstallationError as error:
            self._executor.release()
            self._report_problem(str(error))
            return

        self._show_catalogue_changes(report.catalogue_changes)
        self._show_reconciliation(report.reconciliation)
        if not self._resolve_undetermined(report.reconciliation.undetermined):
            self._executor.release()
            return

        self._stats = self._journal.stats()
        self._stats_widget.update_stats(**self._stats)
        self._is_installing = True
        self._is_paused = False
        self._pause_pending = False
        self._refresh_controls()

        logger.info("Installation started (resumed: %s)", report.resumed)
        self._start_next_sequence(0)

    def _start_next_sequence(self, start: int) -> None:
        """Start the first sequence from `start` that still has work."""
        if self._closing or self._cancelling:
            return

        try:
            prepared = self._executor.next_sequence(start)
            if prepared is None:
                self._on_all_sequences_complete()
                return
            engine = self._executor.create_engine(prepared)
        except InstallationError as error:
            self._report_problem(str(error))
            self._on_installation_stopped(0)
            return

        self._current_sequence = prepared.number
        total = len(self._journal.sequences)
        logger.info(
            "Starting sequence %d/%d in folder: %s",
            prepared.number + 1,
            total,
            prepared.folder,
        )
        self._append_output(
            f"\n{'#' * 80}\n"
            f"# {tr('page.installation.sequence.started', current=prepared.number + 1, total=total)}\n"
            f"# {tr('page.installation.sequence.folder', folder=str(prepared.folder))}\n"
            f"{'#' * 80}\n",
            color=COLOR_INFO,
        )
        self._refresh_progress()

        self._worker = InstallationWorker(
            engine=engine,
            batches=prepared.batches,
            journal=self._journal,
            rules=self.state_manager.get_rule_manager(),
            sequence=prepared.number,
            pause_on_error=self._pause_on_error,
            pause_on_warning=self._pause_on_warning,
        )
        self._worker.setParent(self)
        self._worker.finished.connect(self._worker_finished)
        self._worker.diagnostic.connect(self._report_problem)
        self._worker.dependents_blocked.connect(self._show_blocked)
        self._worker.batch_started.connect(self._on_batch_started)
        self._worker.component_started.connect(self._on_component_started)
        self._worker.component_finished.connect(self._on_component_finished)
        self._worker.output_received.connect(self._on_output_received)
        self._worker.error_occurred.connect(self._on_error_occurred)
        self._worker.warning_occurred.connect(self._on_warning_occurred)
        self._worker.installation_paused.connect(self._on_installation_paused)
        self._worker.installation_retryed.connect(self._on_installation_retryed)
        self._worker.command_created.connect(self._on_command_created)
        if self._pause_pending:
            # A pause requested at the end of the previous sequence applies to this one.
            self._worker.pause()
        self._worker.start()

    def _pause_installation(self):
        """Ask the worker to pause once the current batch is over; WeiDU is not interrupted."""
        if not self._is_installing or self._is_paused or not self._worker:
            return

        self._worker.pause()
        self._pause_pending = True
        self._refresh_controls()
        logger.info("Installation pause requested")

    def _resume_installation(self):
        """Resume installation."""
        if not self._is_paused or not self._worker:
            return

        self._is_paused = False
        # Simply resume the worker (works for both manual pause and popup pause)
        self._worker.resume()
        self._refresh_controls()

        logger.info("Installation resumed")

    def _stop_installation(self):
        """Interrupt WeiDU at once, after a single confirmation; the run stays resumable."""
        if not self._is_installing or not self._worker:
            return

        reply = QMessageBox.question(
            self,
            tr("page.installation.stop_title"),
            tr("page.installation.stop_message"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._interrupt_worker()
            logger.info("Installation stop requested, WeiDU terminated")

    def _cancel_installation(self):
        """Abandon the run after a single confirmation; a running WeiDU is interrupted at once."""
        if not self._can_cancel():
            return

        reply = QMessageBox.question(
            self,
            tr("page.installation.cancel_title"),
            tr("page.installation.cancel_message"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        logger.info("Installation cancellation confirmed")
        self._cancelling = True
        if self._worker and self._worker.isRunning():
            self._interrupt_worker()
            return
        self._finish_cancellation()

    def _interrupt_worker(self) -> None:
        """Stop the worker and terminate WeiDU now: the user already confirmed."""
        self._worker.stop()
        self._worker.force_stop()

    def _finish_cancellation(self) -> None:
        self._cancelling = False
        if self._executor.is_active:
            try:
                if not self._executor.acquire():
                    self._report_problem(tr("page.installation.active_run"))
                    return
                self._executor.cancel()
            except OSError as error:
                self._executor.release()
                self._report_problem(str(error))
                return
        self._executor.release()

        self._set_idle()
        self._reset_workflow()

        logger.info("Installation cancelled, navigating to installation_type page")

    def _can_cancel(self) -> bool:
        """A run can be cancelled while it is running, interrupted or suspended."""
        return self.is_resume() and not self._executor.is_played

    def _finish_run(self) -> None:
        """Close the played run, reset the workflow, then quit the application."""
        if not self._executor.is_played or not self._confirm_finish():
            return

        try:
            if not self._executor.acquire():
                self._report_problem(tr("page.installation.active_run"))
                return
            self._executor.finish()
        except OSError as error:
            self._executor.release()
            self._report_problem(str(error))
            return
        self._executor.release()

        self._reset_workflow()
        logger.info("Installation run finished, closing the application")
        self.window().close()

    def _confirm_finish(self) -> bool:
        """Ask to finish the run, recalling how many components it leaves out."""
        stats = self._journal.stats()
        missing = stats["errors"] + stats["skipped"]
        message = (
            tr("page.installation.finish_incomplete_message", count=missing)
            if self._journal.outcome() == RunOutcome.INCOMPLETE
            else tr("page.installation.finish_message")
        )
        reply = QMessageBox.question(
            self,
            tr("page.installation.finish_title"),
            message,
            defaultButton=QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _reset_workflow(self) -> None:
        """Forget the selection and the order, then go back to the first step."""
        indexes = IndexManager.get_indexes()
        indexes.clear_selection()
        indexes.clear_selection_violations()
        indexes.clear_order_violations()
        self.state_manager.reset_workflow()

    def _send_input_text(self):
        """Send input text."""
        text = self._input_text.text()
        if not text or not self._worker:
            return
        self._worker.send_input(text)

    # ========================================
    # Shutdown
    # ========================================

    def has_critical_task(self) -> bool:
        """An installation in progress must not be abandoned silently."""
        return bool(self._worker and self._worker.isRunning())

    def request_shutdown(self) -> None:
        """Ask WeiDU to finish; a forced stop is offered if it does not exit."""
        super().request_shutdown()
        if self._worker and self._worker.isRunning():
            self._request_worker_stop()

    def _request_worker_stop(self) -> None:
        worker = self._worker
        worker.stop()

        def offer_force() -> None:
            if self._worker is not worker or not worker.isRunning():
                return
            if not worker.has_live_process():
                return
            reply = QMessageBox.question(
                self,
                tr("page.installation.stop_title"),
                tr("page.installation.force_stop"),
                defaultButton=QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                worker.force_stop()

        QTimer.singleShot(PROCESS_STOP_GRACE_MS, self, offer_force)

    # ========================================
    # Journal and decisions
    # ========================================

    @property
    def _journal(self) -> InstallationJournal | None:
        return self._executor.journal

    def is_resume(self) -> bool:
        """Whether a run is open: running, interrupted, suspended or played."""
        return self._executor.is_active

    def _ensure_readable_journal(self) -> bool:
        """Offer to set an unreadable journal aside; False keeps the page blocked."""
        if not self._executor.load_error:
            return True

        reply = QMessageBox.question(
            self,
            tr("page.installation.journal_unreadable_title"),
            tr(
                "page.installation.journal_unreadable",
                path=str(self._executor.journal_path),
                error=self._executor.load_error,
            ),
            defaultButton=QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        try:
            target = self._executor.set_aside_unreadable_journal()
        except OSError as error:
            self._report_problem(str(error))
            return False
        self._append_output(
            tr("page.installation.journal_set_aside", path=str(target)), COLOR_WARNING
        )
        return True

    def _resolve_undetermined(self, positions: list[tuple[int, int]]) -> bool:
        """Ask the user what each unproven operation became; False when they cancel."""
        for sequence, position in positions:
            operation = self._journal.sequences[sequence].operations[position]
            details = self._describe_operation(sequence, position)
            dialog = DecisionDialog(
                tr("page.installation.decision_title"),
                tr("page.installation.decision_message", component=operation.reference),
                details,
                OPERATION_CHOICES,
                default=None,
                cancellable=True,
                on_diagnostic=self._export_diagnostic,
                parent=self,
            )
            dialog.exec()
            if dialog.decision is None:
                return False
            try:
                self._executor.decide(sequence, position, dialog.decision)
            except OSError as error:
                self._report_problem(str(error))
                return False
        return True

    def _describe_operation(self, sequence: int, position: int) -> list[str]:
        operation = self._journal.sequences[sequence].operations[position]
        lines = [
            f"{tr('page.installation.summary.sequence', number=sequence + 1)} — "
            f"{tr(f'page.installation.status.{operation.status.value}')}"
        ]
        if operation.evidence:
            lines.append(describe_evidence(operation.evidence))
        lines.append(
            tr(
                "page.installation.log_now",
                log=describe_log_presence(self._executor.is_logged(sequence, position)),
            )
        )
        return lines + operation.messages

    def _show_catalogue_changes(self, references: list[str]) -> None:
        if references:
            self._append_output(
                tr("page.installation.catalogue_changed", components=", ".join(references)),
                COLOR_WARNING,
            )

    def _show_reconciliation(self, report: ReconciliationReport) -> None:
        if report.reopened:
            self._append_output(
                tr("page.installation.log_reopened", components=", ".join(report.reopened)),
                COLOR_WARNING,
            )
        if report.unexpected:
            self._append_output(
                tr("page.installation.log_unexpected", entries="\n".join(report.unexpected)),
                COLOR_WARNING,
            )

    def _export_diagnostic(self) -> None:
        """Ask where to save the diagnostic archive of the run, then write it."""
        default_path = Path.home() / InstallationDiagnostic.default_file_name()
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            tr("page.installation.diagnostic.dialog_title"),
            str(default_path),
            DIAGNOSTIC_FILE_FILTER,
        )
        if not file_path:
            return
        target = Path(file_path)
        if target.suffix.lower() != DIAGNOSTIC_FILE_SUFFIX:
            target = target.with_name(target.name + DIAGNOSTIC_FILE_SUFFIX)

        try:
            InstallationDiagnostic(self._executor.journal_path).export(target)
        except OSError as error:
            logger.error("Diagnostic export failed: %s", error)
            QMessageBox.critical(
                self,
                tr("page.installation.diagnostic.dialog_title"),
                tr("page.installation.diagnostic.failed", path=str(target), error=str(error)),
            )
            return
        QMessageBox.information(
            self,
            tr("page.installation.diagnostic.dialog_title"),
            tr("page.installation.diagnostic.exported", path=str(target)),
        )

    def _report_problem(self, detail: str) -> None:
        message = tr("page.installation.recovery", detail=detail)
        self._append_output(message, COLOR_ERROR)
        if self._closing and not (self._worker and self._worker.has_live_process()):
            return

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle(tr("page.installation.recovery_title"))
        msg_box.setText(message)
        self._exec_offering_diagnostic(msg_box)

    def _exec_offering_diagnostic(self, msg_box: QMessageBox) -> None:
        """Show a message box with an export button while a journal exists to export."""
        msg_box.addButton(QMessageBox.StandardButton.Ok)
        export_button = None
        if self._executor.journal_path.exists():
            export_button = msg_box.addButton(
                tr("page.installation.btn_diagnostic"), QMessageBox.ButtonRole.ActionRole
            )
        msg_box.exec()
        if export_button is not None and msg_box.clickedButton() is export_button:
            self._export_diagnostic()

    # ========================================
    # Worker Signal Handlers
    # ========================================

    def _worker_finished(self) -> None:
        worker = self.sender()
        worker.deleteLater()
        if self._worker is not worker:
            return
        self._worker = None
        if self._cancelling:
            self._finish_cancellation()
        elif self._closing or worker.is_stopped:
            self._on_installation_stopped(worker.last_batch)
        else:
            self._on_sequence_complete()

    def _on_batch_started(self, current: int, total: int, mod_id: str):
        """Handle batch start."""
        self._btn_send_input.setEnabled(True)
        self._input_text.setEnabled(True)
        mod = self._mod_manager.get_mod_by_id(mod_id)

        logger.info("Batch %d/%d : %s", current, total, mod.name)
        self._append_output(
            f"\n{'=' * 80}\n{tr('page.installation.batch_started', current=current, total=total, mod=mod.name)}\n{'=' * 80}\n"
        )

    def _on_component_started(self, component_id: str, mod_name: str, component_name: str):
        """Show the component WeiDU is installing in the progress bar and the output."""
        logger.info("Installing: %s", component_id)
        # QProgressBar expands %v, %m and %p in its format.
        label = f"{mod_name} — {component_name}".replace("%", "%%")
        self._progress_bar.setFormat(f"{label} - %p%")
        self._append_output(
            f"\n>>> {tr('page.installation.component_started', component=component_id, mod=mod_name)}\n",
            color=COLOR_INFO,
        )

    def _on_component_finished(self, component_id: str, result: InstallResult):
        """Display a recorded result and refresh the counters."""
        self._stats = self._journal.stats()
        self._stats_widget.update_stats(**self._stats)
        self._refresh_progress()

        status_color = {
            ComponentStatus.SUCCESS: COLOR_STATUS_COMPLETE,
            ComponentStatus.WARNING: COLOR_WARNING,
            ComponentStatus.ERROR: COLOR_ERROR,
            ComponentStatus.STOPPED: COLOR_ERROR,
            ComponentStatus.UNKNOWN: COLOR_ERROR,
            ComponentStatus.BLOCKED: COLOR_WARNING,
            ComponentStatus.ALREADY_INSTALLED: COLOR_INFO,
        }.get(result.status)

        status_text = tr(f"page.installation.status.{result.status.value}")
        self._append_output(f"\n<<< {status_text}: {component_id}\n", color=status_color)

        for warning in result.warnings[:5]:
            self._append_output(f"  ⚠ {warning}", COLOR_WARNING)
        for error in result.errors[:5]:
            self._append_output(f"  ✖ {error}", COLOR_ERROR)

    def _on_output_received(self, text: str, stream_type: str):
        """Handle real-time output (stdout/stderr)."""
        color = COLOR_ERROR if stream_type == "stderr" else None
        self._append_output(text, color, newline=False)

    def _on_error_occurred(self, component_id: str, errors: list[str]):
        """Handle error - ask user."""
        if self._closing or self._cancelling:
            return
        dialog = DecisionDialog(
            tr("page.installation.error_title"),
            tr("page.installation.error_message", component=component_id),
            errors,
            ERROR_CHOICES,
            default=UserDecision.STOP,
            on_diagnostic=self._export_diagnostic,
            parent=self,
        )
        dialog.exec()

        if self._worker:
            self._worker.set_user_decision(dialog.decision)

    def _on_warning_occurred(self, component_id: str, warnings: list[str]):
        """Handle warning - ask user."""
        if self._closing or self._cancelling:
            return
        dialog = DecisionDialog(
            tr("page.installation.warning_title"),
            tr("page.installation.warning_message", component=component_id),
            warnings,
            WARNING_CHOICES,
            default=UserDecision.SKIP,
            parent=self,
        )
        dialog.exec()

        if self._worker:
            self._worker.set_user_decision(dialog.decision)

    def _on_sequence_complete(self) -> None:
        """Move on to the next sequence that still has work."""
        logger.info("Sequence %d complete", self._current_sequence)
        self._append_output(
            f"\n{'#' * 80}\n"
            f"# {tr('page.installation.sequence.complete', sequence=self._current_sequence + 1)}\n"
            f"{'#' * 80}\n",
            color=COLOR_STATUS_COMPLETE,
        )
        self._start_next_sequence(self._current_sequence + 1)

    def _on_all_sequences_complete(self):
        """Check the games and show the result; the run stays open until Finish."""
        try:
            report = self._executor.verify_played()
        except InstallationError as error:
            self._report_problem(str(error))
            self._on_installation_stopped(0)
            return

        self._executor.release()
        self._set_idle()
        self._show_reconciliation(report)
        if self._executor.is_played:
            self._append_output(
                tr("page.installation.summary.ready_to_finish"), COLOR_STATUS_COMPLETE
            )

        self.save_state()
        self._show_installation_summary()

        logger.info("All sequences installation complete")

    def _on_installation_stopped(self, last_index: int):
        """Handle stop."""
        self._executor.release()
        self._set_idle()

        if self._closing or self._cancelling:
            return
        self._append_output(
            f"\n{tr('page.installation.stopped_message', index=last_index)}\n",
            color=COLOR_WARNING,
        )

        logger.info("Installation stopped at %d", last_index)

    def _on_installation_paused(self, last_index: int, description: str):
        """Handle pause."""
        self._is_paused = True
        self._pause_pending = False
        self._refresh_controls()

        logger.info("Installation paused at batch %d", last_index)

        if description:
            self._append_output(
                f"\n{tr('page.installation.paused_with_description', description=description)}\n",
                color=COLOR_WARNING,
            )
        else:
            self._append_output(f"\n{tr('page.installation.paused')}\n", color=COLOR_WARNING)

    def _on_installation_retryed(self, count_components: int):
        """Handle retry."""
        self._btn_send_input.setEnabled(True)
        self._input_text.setEnabled(True)
        self._refresh_progress()

    def _on_command_created(self, command: str) -> None:
        self._append_output(
            f"\n{command}\n\n",
            color=COLOR_INFO,
        )

    def _show_blocked(self, components: list[str]) -> None:
        for reference in components:
            self._append_output(
                f"{tr('page.installation.status.blocked')}: {reference}", COLOR_ERROR
            )

    # ========================================
    # UI Helpers
    # ========================================

    def _set_idle(self) -> None:
        """No worker runs any more: forget the pause states, then refresh the controls."""
        self._is_installing = False
        self._is_paused = False
        self._pause_pending = False
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        """Enable the controls matching the run state."""
        running = self._is_installing
        if not running:
            self._progress_bar.setFormat(PROGRESS_IDLE_FORMAT)
        self._refresh_run_action()
        self._btn_stop.setEnabled(running)
        self._btn_cancel.setEnabled(self._can_cancel())
        self._btn_send_input.setEnabled(running and not self._is_paused)
        self._input_text.setEnabled(running and not self._is_paused)
        # Batching is part of the frozen plan; pause preferences stay adjustable.
        self._cb_batch_install.setEnabled(not running and not self.is_resume())
        self._btn_diagnostic.setEnabled(self._executor.journal_path.exists())
        self._update_navigation_buttons()

    def _refresh_run_action(self) -> None:
        """Label, icon and state of the main button."""
        action = self._run_action()
        label, tooltip, icon = RUN_ACTION_BUTTONS[action]
        self._btn_run_action.setText(tr(label))
        self._btn_run_action.setToolTip(tr(tooltip))
        self._btn_run_action.setIcon(button_icon(icon))
        self._btn_run_action.setEnabled(action != RunAction.PAUSE_PENDING)

    def _refresh_progress(self) -> None:
        operations = self._journal.sequences[self._current_sequence].operations
        self._progress_bar.setMaximum(len(operations))
        self._progress_bar.setValue(sum(not op.needs_work for op in operations))

    def _append_output(self, text: str, color: str | None = None, newline: bool = True):
        """Append text to output."""
        cursor = self._output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        if color:
            fmt.setForeground(QColor(color))

        cursor.setCharFormat(fmt)
        cursor.insertText(text)

        if newline and not text.endswith("\n"):
            cursor.insertText("\n")

        self._output_text.setTextCursor(cursor)
        self._output_text.ensureCursorVisible()

    def _show_installation_summary(self):
        """Show the outcome and the components that prevent a complete installation."""
        outcome = self._journal.outcome()

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(tr("page.installation.summary.title"))
        msg_box.setText(tr(f"page.installation.{outcome.value}"))
        details = [
            f"{op.reference} — {tr(f'page.installation.status.{op.status.value}')}"
            + ("\n" + "\n".join(op.messages) if op.messages else "")
            for seq in self._journal.sequences
            for op in seq.operations
            if not op.is_pause and (not op.installed or op.status == ComponentStatus.WARNING)
        ]
        if details:
            msg_box.setDetailedText("\n".join(details))
        msg_box.setIcon(
            QMessageBox.Icon.Information
            if outcome == RunOutcome.COMPLETE
            else QMessageBox.Icon.Warning
        )
        self._exec_offering_diagnostic(msg_box)

    def _update_navigation_buttons(self):
        """Update navigation buttons."""
        self.notify_navigation_changed()

    def _display_installation_summary(self):
        """Display the frozen plan when a run is active."""
        plan = self._journal.plan if self.is_resume() else None
        game_manager = self.state_manager.get_game_manager()
        game_def = game_manager.get(
            plan.game if plan else self.state_manager.get_selected_game()
        )
        install_order_data = plan.orders if plan else self.state_manager.get_install_order()

        if not game_def or not install_order_data:
            self._append_output(tr("page.installation.summary.no_data"), COLOR_WARNING)
            return

        is_resume = plan is not None
        folders = plan.folders if plan else self.state_manager.get_game_folders()
        total_components = sum(
            len(install_order_data.get(index, [])) for index in range(game_def.sequence_count)
        )

        self._append_summary_header(is_resume)
        self._append_sequences(game_def, install_order_data, folders, total_components)

        if is_resume:
            self._append_resume_progress(total_components)

        if self._executor.is_played:
            ready_key = "ready_to_finish"
        else:
            ready_key = "ready_to_resume" if is_resume else "ready"
        self._append_rule("=", COLOR_INFO)
        self._append_output(
            tr(f"page.installation.summary.{ready_key}") + "\n\n", COLOR_STATUS_COMPLETE
        )

    def _append_rule(self, char: str, color: str, *, blank_after: bool = True) -> None:
        """Append a horizontal rule separating sections of the summary."""
        trailing = "\n\n" if blank_after else "\n"
        self._append_output(char * INSTALL_OUTPUT_RULE_WIDTH + trailing, color)

    def _append_summary_header(self, is_resume: bool) -> None:
        """Append the title framing the summary, depending on a resumed or new run."""
        self._append_rule("=", COLOR_INFO, blank_after=False)

        if self._executor.is_played:
            self._append_output(
                tr("page.installation.summary.played_title") + "\n", COLOR_STATUS_COMPLETE
            )
        elif is_resume:
            self._append_output(
                tr("page.installation.summary.resume_title") + "\n", COLOR_WARNING
            )
        else:
            self._append_output(tr("page.installation.summary.new_title") + "\n", COLOR_INFO)

        self._append_rule("=", COLOR_INFO)

    def _append_sequences(
        self,
        game_def: GameDefinition,
        install_order_data: dict[int, list[str]],
        folders: dict[str, str],
        total_components: int,
    ) -> None:
        """Append the totals, then the folder and component count of each sequence."""
        self._append_output(
            tr("page.installation.summary.total_sequences", count=game_def.sequence_count)
            + "\n",
            COLOR_INFO,
        )
        self._append_output(
            tr("page.installation.summary.total_components", count=total_components) + "\n\n",
            COLOR_INFO,
        )

        for index, sequence in enumerate(game_def.sequences):
            component_count = len(install_order_data.get(index, []))

            self._append_output(
                f"  {tr('page.installation.summary.sequence', number=index + 1)}: "
                f"{sequence.name} ({sequence.game.upper()})\n"
            )
            self._append_output(
                f"    {tr('page.installation.summary.folder')}: {folders.get(sequence.game)}\n"
            )
            self._append_output(
                f"    {tr('page.installation.summary.components', count=component_count)}\n\n"
            )

    def _append_resume_progress(self, total_components: int) -> None:
        """Append how many components the interrupted run already installed."""
        self._append_rule("-", COLOR_WARNING, blank_after=False)
        self._append_output(tr("page.installation.summary.resume_info") + "\n", COLOR_WARNING)
        self._append_rule("-", COLOR_WARNING)

        installed = sum(
            op.installed for seq in self._journal.sequences for op in seq.operations
        )
        self._append_output(
            tr(
                "page.installation.summary.installed_components",
                index=installed,
                total=total_components,
            )
            + "\n",
            COLOR_INFO,
        )

    def _on_batch_install_changed(self, state: int) -> None:
        """Handle batch install checkbox change."""
        self._batch_install = state == Qt.CheckState.Checked.value
        logger.debug(f"Batch install: {self._batch_install}")

    def _on_pause_on_warning_changed(self, state: int) -> None:
        """Handle pause on warning checkbox change."""
        self._pause_on_warning = state == Qt.CheckState.Checked.value
        if self._worker and self._worker.isRunning():
            self._worker.update_pause_settings(self._pause_on_error, self._pause_on_warning)
        logger.debug(f"Pause on warning: {self._pause_on_warning}")

    def _on_pause_on_error_changed(self, state: int) -> None:
        """Handle pause on error checkbox change."""
        self._pause_on_error = state == Qt.CheckState.Checked.value
        if self._worker and self._worker.isRunning():
            self._worker.update_pause_settings(self._pause_on_error, self._pause_on_warning)
        logger.debug(f"Pause on error: {self._pause_on_error}")

    def get_page_id(self) -> str:
        return "installation"

    def get_page_title(self) -> str:
        return tr("page.installation.title")

    def get_additional_buttons(self) -> list[QPushButton]:
        return [self._btn_run_action, self._btn_stop, self._btn_cancel, self._btn_diagnostic]

    def get_next_button_config(self) -> ButtonConfig:
        return ButtonConfig(
            visible=False, enabled=self.can_go_to_next_page(), text=tr("button.finish")
        )

    def can_go_to_next_page(self) -> bool:
        """Can proceed once the run is closed and complete."""
        return bool(
            self._journal
            and not self._journal.active
            and self._journal.outcome() != RunOutcome.INCOMPLETE
        )

    def can_go_to_previous_page(self) -> bool:
        """Cannot go back once a run started, until it is finished or cancelled."""
        return not self._is_installing and not self.is_resume()

    def on_page_shown(self):
        """Called when page shown."""
        super().on_page_shown()

        self._output_text.clear()
        self._stats_widget.reset()
        self._progress_bar.setValue(0)
        self._stats = {"success": 0, "warnings": 0, "errors": 0, "skipped": 0}

        self._ensure_readable_journal()
        self._display_installation_summary()
        self._refresh_controls()

    def on_page_hidden(self):
        """Called when page hidden."""
        super().on_page_hidden()

        if self._worker and self._worker.isRunning():
            self._request_worker_stop()

    def retranslate_ui(self):
        """Update UI text."""
        self._refresh_controls()

        self._btn_stop.setText(tr("page.installation.btn_stop"))
        self._btn_stop.setToolTip(tr("page.installation.btn_stop_tooltip"))
        self._btn_cancel.setText(tr("page.installation.btn_cancel"))
        self._btn_cancel.setToolTip(tr("page.installation.btn_cancel_tooltip"))
        self._btn_diagnostic.setText(tr("page.installation.btn_diagnostic"))
        self._btn_diagnostic.setToolTip(tr("page.installation.btn_diagnostic_tooltip"))
        self._btn_send_input.setText(tr("page.installation.btn_send_input"))
        self._lbl_log.setText(tr("page.installation.lbl_log"))

        self._cb_batch_install.setText(tr("page.installation.batch_install"))
        self._cb_batch_install.setToolTip(tr("page.installation.batch_install_tooltip"))
        self._cb_pause_on_warning.setText(tr("page.installation.pause_on_warning"))
        self._cb_pause_on_warning.setToolTip(tr("page.installation.pause_on_warning_tooltip"))
        self._cb_pause_on_error.setText(tr("page.installation.pause_on_error"))
        self._cb_pause_on_error.setToolTip(tr("page.installation.pause_on_error_tooltip"))

        self._stats_widget.update_stats(**self._stats)

    def load_state(self) -> None:
        """Load preferences; batching comes from the plan of an active run."""
        super().load_state()

        page_id = self.get_page_id()
        self._executor.load()
        plan = self._journal.plan if self.is_resume() else None

        self._cb_batch_install.setChecked(
            plan.batch
            if plan
            else self.state_manager.get_page_option(
                page_id, "batch_install", self._batch_install
            )
        )
        self._cb_pause_on_error.setChecked(
            self.state_manager.get_page_option(page_id, "pause_on_error", self._pause_on_error)
        )
        self._cb_pause_on_warning.setChecked(
            self.state_manager.get_page_option(
                page_id, "pause_on_warning", self._pause_on_warning
            )
        )
        self._cb_batch_install.setEnabled(plan is None)

    def save_state(self) -> None:
        """Save page data to state manager."""
        super().save_state()

        page_id = self.get_page_id()

        self.state_manager.set_page_option(page_id, "batch_install", self._batch_install)
        self.state_manager.set_page_option(page_id, "pause_on_error", self._pause_on_error)
        self.state_manager.set_page_option(page_id, "pause_on_warning", self._pause_on_warning)
        if not self.state_manager.save_state():
            QMessageBox.critical(
                self, tr("error.critical_title"), tr("error.state_save_failed")
            )
