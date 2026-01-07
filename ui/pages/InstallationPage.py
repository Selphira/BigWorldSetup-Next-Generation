"""
Installation page for mod installation process.
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
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
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_STATUS_COMPLETE,
    COLOR_WARNING,
    MARGIN_STANDARD,
    SPACING_LARGE,
    SPACING_SMALL,
)
from core.ComponentReference import IndexManager
from core.GameModels import GameDefinition
from core.InstallationWorker import InstallationState, InstallationWorker, UserDecision
from core.models.PauseEntry import PAUSE_PREFIX, PauseEntry
from core.StateManager import StateManager
from core.TranslationManager import tr
from core.weidu_types import ComponentInfo, ComponentStatus, InstallResult
from core.WeiDUDebugParser import WeiDUDebugParser
from core.WeiDUInstallerEngine import WeiDUInstallerEngine
from core.WeiDULogParser import WeiDULogParser
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


class ErrorDecisionDialog(QDialog):
    """Dialog for user decision after installation error."""

    def __init__(self, component_id: str, errors: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("page.installation.error_title"))
        if len(errors) > 0:
            self.setMinimumWidth(500)
        self.decision = UserDecision.STOP

        self._create_widgets(component_id, errors)

    def _create_widgets(self, component_id: str, errors: list[str]):
        """Create dialog layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_LARGE)

        # Error message
        msg = QLabel(tr("page.installation.error_message", component=component_id))
        msg.setWordWrap(True)
        layout.addWidget(msg)

        if len(errors) > 0:
            # Error details
            error_text = QTextEdit()
            error_text.setReadOnly(True)
            error_text.setMaximumHeight(150)
            error_text.setPlainText("\n".join(errors))
            layout.addWidget(error_text)

        # Buttons
        button_layout = QHBoxLayout()

        btn_retry = QPushButton(tr("page.installation.btn_retry"))
        btn_retry.clicked.connect(lambda: self._make_decision(UserDecision.RETRY))
        btn_retry.setCursor(Qt.CursorShape.PointingHandCursor)
        button_layout.addWidget(btn_retry)

        btn_continue = QPushButton(tr("page.installation.btn_continue"))
        btn_continue.clicked.connect(lambda: self._make_decision(UserDecision.SKIP))
        btn_continue.setCursor(Qt.CursorShape.PointingHandCursor)
        button_layout.addWidget(btn_continue)

        btn_pause = QPushButton(tr("page.installation.btn_pause"))
        btn_pause.clicked.connect(lambda: self._make_decision(UserDecision.PAUSE))
        btn_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        button_layout.addWidget(btn_pause)

        btn_stop = QPushButton(tr("page.installation.btn_stop"))
        btn_stop.clicked.connect(lambda: self._make_decision(UserDecision.STOP))
        btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        button_layout.addWidget(btn_stop)

        layout.addLayout(button_layout)

    def _make_decision(self, decision: UserDecision):
        """Record decision and close."""
        self.decision = decision
        self.accept()


class WarningDecisionDialog(QDialog):
    """Dialog for user decision after installation warning."""

    def __init__(self, component_id: str, warnings: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("page.installation.warning_title"))
        self.setMinimumWidth(500)
        self.decision = UserDecision.SKIP

        self._create_widgets(component_id, warnings)

    def _create_widgets(self, component_id: str, warnings: list[str]):
        """Create dialog layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_LARGE)

        # Warning message
        msg = QLabel(tr("page.installation.warning_message", component=component_id))
        msg.setWordWrap(True)
        layout.addWidget(msg)

        # Warning details
        warning_text = QTextEdit()
        warning_text.setReadOnly(True)
        warning_text.setMaximumHeight(150)
        warning_text.setPlainText("\n".join(warnings))
        layout.addWidget(warning_text)

        # Buttons
        button_layout = QHBoxLayout()

        btn_continue = QPushButton(tr("page.installation.btn_continue"))
        btn_continue.clicked.connect(lambda: self._make_decision(UserDecision.SKIP))
        button_layout.addWidget(btn_continue)

        btn_pause = QPushButton(tr("page.installation.btn_pause"))
        btn_pause.clicked.connect(lambda: self._make_decision(UserDecision.PAUSE))
        button_layout.addWidget(btn_pause)

        btn_stop = QPushButton(tr("page.installation.btn_stop"))
        btn_stop.clicked.connect(lambda: self._make_decision(UserDecision.STOP))
        button_layout.addWidget(btn_stop)

        layout.addLayout(button_layout)

    def _make_decision(self, decision: UserDecision):
        """Record decision and close."""
        self.decision = decision
        self.accept()


# ============================================================================
# Installation Page
# ============================================================================


class InstallationPage(BasePage):
    """Page for installing mods with full workflow management."""

    def __init__(self, state_manager: StateManager):
        super().__init__(state_manager)

        self._mod_manager = self.state_manager.get_mod_manager()
        self._engine: WeiDUInstallerEngine | None = None
        self._worker: InstallationWorker | None = None
        self._log_parser: WeiDULogParser = WeiDULogParser()
        self._debug_parser: WeiDUDebugParser = WeiDUDebugParser()

        # Installation data
        self._components: list[ComponentInfo] = []
        self._batches: list[list[ComponentInfo]] = []
        self._installation_state: InstallationState | None = None
        self._batch_install: bool = True
        self._pause_on_error: bool = True
        self._pause_on_warning: bool = True

        # State tracking
        self._is_installing = False
        self._is_paused = False
        self._stats = {"success": 0, "warnings": 0, "errors": 0, "skipped": 0}

        # UI components
        self._lbl_log: QLabel | None = None
        self._output_text: QTextEdit | None = None
        self._input_text: QLineEdit | None = None
        self._progress_bar: QProgressBar | None = None
        self._stats_widget: InstallationStatsWidget | None = None
        self._btn_start_pause: QPushButton | None = None
        self._btn_stop: QPushButton | None = None
        self._btn_cancel: QPushButton | None = None

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

        hlayout = QHBoxLayout(self)
        hlayout.setSpacing(SPACING_LARGE)
        hlayout.setContentsMargins(0, 0, 0, 0)

        hlayout.addWidget(self._create_left_panel())
        hlayout.addWidget(self._create_right_panel())

        layout.addLayout(hlayout)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%v / %m - %p%")
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

        hlayout = QHBoxLayout(self)
        self._input_text = QLineEdit()
        self._input_text.setEnabled(False)
        self._input_text.setMaximumWidth(150)
        hlayout.addWidget(self._input_text)

        self._btn_send_input = QPushButton()
        self._btn_send_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_send_input.setEnabled(False)
        self._btn_send_input.clicked.connect(self._send_input_text)
        hlayout.addWidget(self._btn_send_input)

        layout.addLayout(hlayout)

        return panel

    def _create_additional_buttons(self):
        """Create action buttons."""
        self._btn_start_pause = QPushButton()
        self._btn_start_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start_pause.clicked.connect(self._toggle_start_pause)

        self._btn_stop = QPushButton()
        self._btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_stop.setEnabled(False)
        self._btn_stop.clicked.connect(self._stop_installation)
        self._btn_cancel = QPushButton()
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self._cancel_installation)

    # ========================================
    # Installation Workflow
    # ========================================

    def _prepare_installation(
        self, game_def: GameDefinition, install_order_data: dict[int, list[str]]
    ) -> bool:
        """
        Prepare multi-sequence installation data.

        Args:
            game_def: Game definition with sequences
            install_order_data: Installation order per sequence

        Returns:
            True if preparation successful
        """
        self._sequence_installations = []

        # Build installation data for each sequence
        for seq_idx in range(game_def.sequence_count):
            sequence = game_def.get_sequence(seq_idx)
            if not sequence:
                continue

            # Get component order for this sequence
            order_list = install_order_data.get(seq_idx, [])
            if not order_list:
                logger.warning("No components for sequence %d", seq_idx)
                continue

            # Get game folder for this sequence
            game_folder = self.state_manager.get_game_folders().get(sequence.game)

            if not game_folder:
                QMessageBox.critical(
                    self,
                    tr("page.installation.error_no_folder_title"),
                    tr("page.installation.error_no_folder_message", sequence=seq_idx),
                )
                return False

            # Store sequence installation data
            self._sequence_installations.append(
                {
                    "seq_idx": seq_idx,
                    "game_folder": Path(game_folder),
                    "sequence": sequence,
                    "order": order_list,
                }
            )

        logger.info("Prepared %d sequences for installation", len(self._sequence_installations))
        return True

    def _start_next_sequence(self):
        """Start next sequence installation."""
        if self._installation_state.current_sequence >= len(self._sequence_installations):
            # All sequences complete
            self._on_all_sequences_complete()
            return

        seq_install = self._sequence_installations[self._installation_state.current_sequence]
        seq_idx = seq_install["seq_idx"]
        game_folder = seq_install["game_folder"]
        order_list = seq_install["order"]

        logger.info(
            "Starting sequence %d/%d in folder: %s",
            self._installation_state.current_sequence + 1,
            len(self._sequence_installations),
            game_folder,
        )

        # Log sequence start in output
        self._append_output(
            f"\n{'#' * 80}\n"
            f"# {tr('page.installation.sequence.started', current=self._installation_state.current_sequence + 1, total=len(self._sequence_installations))}\n"
            f"# {tr('page.installation.sequence.folder', folder=str(game_folder))}\n"
            f"{'#' * 80}\n",
            color=COLOR_INFO,
        )

        self._engine = WeiDUInstallerEngine(game_folder, self._log_parser, self._debug_parser)

        if not self._engine.locate_weidu():
            QMessageBox.critical(
                self,
                tr("page.installation.error_no_weidu_title"),
                tr(
                    "page.installation.error_no_weidu_message",
                    sequence=seq_idx,
                    folder=str(game_folder),
                ),
            )
            self._on_installation_stopped(0)
            return

        self._engine.init_weidu_conf(self.state_manager.get_languages_order()[0])
        self._engine.init_weidu_log()

        # Convert order to ComponentInfo
        self._components = self._convert_order_to_component_info(order_list)
        self._batches = self._prepare_batches(self._components)

        # Determine start batch index (for resume within current sequence)
        self._start_batch_index = 0
        if (
            self._installation_state
            and self._installation_state.last_installed_batch_index >= 0
        ):
            self._start_batch_index = self._installation_state.last_installed_batch_index + 1
            logger.info("Resuming sequence %d from batch %d", seq_idx, self._start_batch_index)

        # Update progress bar for this sequence
        self._progress_bar.setMaximum(len(self._components))

        # Calculate how many components already done in this sequence
        components_done_in_sequence = 0
        if self._start_batch_index > 0:
            for i in range(self._start_batch_index):
                components_done_in_sequence += len(self._batches[i])

        self._progress_bar.setValue(components_done_in_sequence)

        languages_order = self.state_manager.get_languages_order()
        languages = self.state_manager.get_page_option(
            "mod_selection", "selected_languages", []
        )
        # Create worker for this sequence
        self._worker = InstallationWorker(
            engine=self._engine,
            batches=self._batches,
            start_index=self._start_batch_index,
            languages_order=[language for language in languages_order if language in languages],
            mod_manager=self._mod_manager,
            pause_on_error=self._pause_on_error,
            pause_on_warning=self._pause_on_warning,
        )

        # Connect signals
        self._worker.batch_started.connect(self._on_batch_started)
        self._worker.component_started.connect(self._on_component_started)
        self._worker.component_finished.connect(self._on_component_finished)
        self._worker.output_received.connect(self._on_output_received)
        self._worker.error_occurred.connect(self._on_error_occurred)
        self._worker.warning_occurred.connect(self._on_warning_occurred)
        self._worker.installation_complete.connect(self._on_sequence_complete)
        self._worker.installation_stopped.connect(self._on_installation_stopped)
        self._worker.installation_paused.connect(self._on_installation_paused)
        self._worker.installation_retryed.connect(self._on_installation_retryed)
        self._worker.command_created.connect(self._on_command_created)

        # Start worker
        self._worker.start()

    def _convert_order_to_component_info(self, order_list: list[str]) -> list[ComponentInfo]:
        """
        Convert order list to ComponentInfo objects.

        Args:
            order_list: List of component IDs ("mod:comp")

        Returns:
            List of ComponentInfo objects
        """
        components = []

        for idx, comp_id in enumerate(order_list):
            parts = comp_id.split(":", 1)
            if len(parts) != 2:
                logger.warning("Invalid component ID: %s", comp_id)
                continue

            mod_id, comp_key = parts

            mod = self._mod_manager.get_mod_by_id(mod_id)
            if not mod:
                if PauseEntry.is_pause(comp_id):
                    comp_info = ComponentInfo(
                        tp2_name=PAUSE_PREFIX,
                        comp_id=comp_id,
                        mod=None,
                        component=None,
                        sequence_idx=idx,
                        requirements=set(),
                        subcomponent_answers=[],
                        extra_args=[],
                    )

                    components.append(comp_info)
                    continue
                logger.warning("Mod not found: %s", mod_id)
                continue

            component = mod.get_component(comp_key)
            if not component:
                logger.warning("Component not found: %s:%s", mod_id, comp_key)
                continue

            subcomponent_answers = []
            extra_args = []

            if component.is_sub():
                selected_components = self.state_manager.get_selected_components()
                subcomponent_answers = [
                    component.split(".")[2]
                    for component in selected_components
                    if component.startswith(f"{mod_id}:") and component.count(".") == 2
                ]

            if mod_id.lower() == "eet":
                extra_args = [
                    "--args-list",
                    "p",
                    f'"{self.state_manager.get_game_folders().get("sod")}"',
                ]

            comp_info = ComponentInfo(
                tp2_name=mod.tp2,
                comp_id=comp_id,
                mod=mod,
                component=component,
                sequence_idx=idx,
                requirements=self.state_manager.get_rule_manager().get_requirements(
                    mod_id, comp_key, True
                ),
                subcomponent_answers=subcomponent_answers,
                extra_args=extra_args,
            )

            components.append(comp_info)

        return components

    def _prepare_batches(self, components: list[ComponentInfo]) -> list[list[ComponentInfo]]:
        """Prepare batches (SUB components always alone)."""
        batches = []
        current_batch = []

        if not self._batch_install:
            for comp in components:
                batches.append([comp])
            return batches

        for comp in components:
            is_sub = bool(comp.subcomponent_answers)

            if is_sub or comp.component is None:
                if current_batch:
                    batches.append(current_batch)
                    current_batch = []
                batches.append([comp])

            elif not current_batch or current_batch[0].mod.id == comp.mod.id:
                current_batch.append(comp)

            else:
                batches.append(current_batch)
                current_batch = [comp]

        if current_batch:
            batches.append(current_batch)

        return batches

    # ========================================
    # Installation Control
    # ========================================

    def _toggle_start_pause(self):
        """Toggle between start/pause."""
        if not self._is_installing:
            self._start_installation()
        elif self._is_paused:
            self._resume_installation()
        else:
            self._pause_installation()

    def _start_installation(self):
        """Start installation."""
        if self._is_installing:
            return

        selected_game = self.state_manager.get_selected_game()
        game_manager = self.state_manager.get_game_manager()
        game_def = game_manager.get(selected_game)
        install_order_data = self.state_manager.get_install_order()

        if not self._prepare_installation(game_def, install_order_data):
            return

        # Initialize installation state if not resuming
        if not self._installation_state:
            for seq_install in self._sequence_installations:
                game_folder = seq_install["game_folder"]
                weidu_log = game_folder / "WeiDU.log"
                if weidu_log.exists() and weidu_log.stat().st_size > 0:
                    QMessageBox.critical(
                        self,
                        tr("page.installation.error_already_modded_title"),
                        tr(
                            "page.installation.error_already_modded_message",
                            sequence=seq_install["seq_idx"],
                            game_folder=game_folder,
                            folder=str(game_folder),
                        ),
                    )
                    return

            # Calculate total components across all sequences
            total_components = sum(
                len(seq_install["order"]) for seq_install in self._sequence_installations
            )

            self._installation_state = InstallationState(
                last_installed_component_index=-1,
                last_installed_batch_index=-1,
                current_sequence=0,
            )

            # Save initial state
            self.save_state()

            logger.info(
                "Initialized installation state: %d total components across %d sequences",
                total_components,
                len(self._sequence_installations),
            )

        # Update UI
        self._is_installing = True
        self._is_paused = False
        self._btn_start_pause.setText(tr("page.installation.btn_pause"))
        self._btn_stop.setEnabled(True)
        self._btn_send_input.setEnabled(True)
        self._input_text.setEnabled(True)
        self._update_navigation_buttons()

        # Disable checkboxes during installation
        self._cb_batch_install.setEnabled(False)

        # Start first sequence
        logger.info("Installation started")
        self._start_next_sequence()

    def _pause_installation(self):
        """Pause installation."""
        if not self._is_installing or self._is_paused or not self._worker:
            return

        self._worker.pause()
        self._is_paused = True
        self._btn_start_pause.setText(tr("page.installation.btn_resume"))
        logger.info("Installation pause requested")

    def _resume_installation(self):
        """Resume installation."""
        if not self._is_paused or not self._worker:
            return

        self._is_paused = False
        self._btn_start_pause.setText(tr("page.installation.btn_pause"))

        # Simply resume the worker (works for both manual pause and popup pause)
        self._worker.resume()

        logger.info("Installation resumed")

    def _stop_installation(self):
        """Stop installation."""
        if not self._is_installing or not self._worker:
            return

        reply = QMessageBox.question(
            self,
            tr("page.installation.stop_title"),
            tr("page.installation.stop_message"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._worker.stop()
            logger.info("Installation stop requested")

    def _cancel_installation(self):
        """Cancel installation."""
        reply = QMessageBox.question(
            self,
            tr("page.installation.cancel_title"),
            tr("page.installation.cancel_message"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        logger.info("Installation cancellation confirmed")

        if reply != QMessageBox.StandardButton.Yes:
            return

        # Reset UI state
        self._is_installing = False
        self._is_paused = False
        self._btn_start_pause.setText(tr("page.installation.btn_start"))
        self._btn_start_pause.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_send_input.setEnabled(False)
        self._input_text.setEnabled(False)
        self._cb_batch_install.setEnabled(True)

        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait()

        indexes = IndexManager.get_indexes()
        indexes.clear_selection()
        indexes.clear_selection_violations()
        indexes.clear_order_violations()
        self.state_manager.reset_workflow()

        logger.info("Installation cancelled, navigating to installation_type page")

    def _send_input_text(self):
        """Send input text."""
        text = self._input_text.text()
        if not text or not self._worker:
            return
        self._worker.send_input(text)

    # ========================================
    # Worker Signal Handlers
    # ========================================

    def _on_batch_started(self, current: int, total: int, mod_id: str):
        """Handle batch start."""
        # Update batch index in state
        self._btn_send_input.setEnabled(True)
        self._input_text.setEnabled(True)
        self._installation_state.last_installed_batch_index = current - 1
        mod = self._mod_manager.get_mod_by_id(mod_id)

        logger.info("Batch %d/%d : %s", current, total, mod.name)
        self._append_output(
            f"\n{'=' * 80}\n{tr('page.installation.batch_started', current=current, total=total, mod=mod.name)}\n{'=' * 80}\n"
        )

    def _on_component_started(self, component_id: str, mod_name: str):
        """Handle component start."""
        logger.info("Installing: %s", component_id)
        self._append_output(
            f"\n>>> {tr('page.installation.component_started', component=component_id, mod=mod_name)}\n",
            color=COLOR_INFO,
        )

    def _on_component_finished(self, component_id: str, result: InstallResult):
        """Handle component completion."""
        # Update progress
        current = self._progress_bar.value() + 1

        # Update stats
        if result.status == ComponentStatus.SUCCESS:
            self._stats["success"] += 1
        elif result.status == ComponentStatus.WARNING:
            self._stats["warnings"] += 1
        elif result.status == ComponentStatus.ERROR:
            self._stats["errors"] += 1
        elif result.status in (ComponentStatus.SKIPPED, ComponentStatus.ALREADY_INSTALLED):
            self._stats["skipped"] += 1
        elif result.status == ComponentStatus.STOPPED:
            self._installation_state.last_installed_batch_index -= 1
            self._installation_state.last_installed_component_index -= 1
            current -= 1

        self._progress_bar.setValue(current)
        self._stats_widget.update_stats(**self._stats)

        # Update installation state with global component index
        self._installation_state.last_installed_component_index += 1

        # Save state periodically
        self.save_state()

        # Log result
        status_color = {
            ComponentStatus.SUCCESS: COLOR_STATUS_COMPLETE,
            ComponentStatus.WARNING: COLOR_WARNING,
            ComponentStatus.ERROR: COLOR_ERROR,
            ComponentStatus.STOPPED: COLOR_ERROR,
            ComponentStatus.ALREADY_INSTALLED: COLOR_INFO,
        }.get(result.status)

        status_text = tr(f"page.installation.status.{result.status.value}")
        self._append_output(f"\n<<< {status_text}: {component_id}\n", color=status_color)

        if result.warnings:
            for warning in result.warnings[:5]:
                self._append_output(f"  ⚠ {warning}", COLOR_WARNING)

        if result.errors:
            for error in result.errors[:5]:
                self._append_output(f"  ✖ {error}", COLOR_ERROR)

    def _on_output_received(self, text: str, stream_type: str):
        """Handle real-time output (stdout/stderr)."""
        # Color stderr differently
        if stream_type == "stderr":
            self._append_output(text, COLOR_ERROR)
        else:
            self._append_output(text)

    def _on_error_occurred(self, component_id: str, errors: list[str]):
        """Handle error - ask user."""
        dialog = ErrorDecisionDialog(component_id, errors, self)
        dialog.exec()

        if dialog.decision == UserDecision.SKIP:
            self._check_and_handle_dependents(component_id)

        self._worker.set_user_decision(dialog.decision)

    def _on_warning_occurred(self, component_id: str, warnings: list[str]):
        """Handle warning - ask user."""
        dialog = WarningDecisionDialog(component_id, warnings, self)
        dialog.exec()

        self._worker.set_user_decision(dialog.decision)

    def _on_sequence_complete(self, results: dict):
        """
        Handle single sequence completion.

        Args:
            results: Results for this sequence
        """
        logger.info("Sequence %d complete", self._installation_state.current_sequence)

        self._append_output(
            f"\n{'#' * 80}\n"
            f"# {tr('page.installation.sequence.complete', sequence=self._installation_state.current_sequence + 1)}\n"
            f"{'#' * 80}\n",
            color=COLOR_STATUS_COMPLETE,
        )

        # Move to next sequence
        self._installation_state.current_sequence += 1
        self._installation_state.last_installed_component_index = -1
        self._installation_state.last_installed_batch_index = -1

        # Start next sequence or finalize
        if self._installation_state.current_sequence < len(self._sequence_installations):
            # More sequences to install
            self._start_next_sequence()
        else:
            # All sequences done
            self._on_all_sequences_complete()

    def _on_all_sequences_complete(self):
        """Handle completion of all sequences."""
        self._is_installing = False
        self._is_paused = False
        self._btn_start_pause.setText(tr("page.installation.btn_start"))
        self._btn_start_pause.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_send_input.setEnabled(False)
        self._input_text.setEnabled(False)
        self._cb_batch_install.setEnabled(True)
        self._update_navigation_buttons()

        self.clear_installation_state()
        self._show_installation_summary()

        self.state_manager.set_ui_current_page("installation_type")

        logger.info("All sequences installation complete")

    def _on_installation_stopped(self, last_index: int):
        """Handle stop."""
        self._is_installing = False
        self._is_paused = False
        self._btn_start_pause.setText(tr("page.installation.btn_start"))
        self._btn_start_pause.setEnabled(True)
        self._btn_stop.setEnabled(False)
        self._btn_send_input.setEnabled(False)
        self._input_text.setEnabled(False)
        self._cb_batch_install.setEnabled(True)
        self._update_navigation_buttons()

        QMessageBox.information(
            self,
            tr("page.installation.stopped_title"),
            tr("page.installation.stopped_message", index=last_index),
        )

        logger.info("Installation stopped at %d", last_index)

    def _on_installation_paused(self, last_index: int, description: str):
        """Handle pause."""
        self._is_paused = True
        self._btn_start_pause.setText(tr("page.installation.btn_resume"))
        self._btn_send_input.setEnabled(False)
        self._input_text.setEnabled(False)

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
        self._progress_bar.setValue(self._progress_bar.value() - count_components)

    def _on_command_created(self, command: str) -> None:
        self._append_output(
            f"\n{command}\n\n",
            color=COLOR_INFO,
        )

    # ========================================
    # Dependency Management
    # ========================================

    def _check_and_handle_dependents(self, failed_component_id: str):
        """Check and handle dependent components."""
        dependents = self._find_dependent_components(failed_component_id)

        if not dependents:
            return

        reply = QMessageBox.question(
            self,
            tr("page.install.dependents.title"),
            tr(
                "page.install.dependents.message",
                count=len(dependents),
                components=", ".join(dependents),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._remove_components_from_batches(dependents)
            logger.info("Removed %d dependents", len(dependents))

    def _find_dependent_components(self, failed_id: str) -> list[str]:
        """Find dependent components."""
        dependents = []
        current = self._progress_bar.value()
        remaining = self._components[current:]

        for comp in remaining:
            if self._component_depends_on(comp, failed_id):
                dependents.append(f"{comp.mod.id}:{comp.component.key}")

        return dependents

    def _component_depends_on(self, comp: ComponentInfo, target_id: str) -> bool:
        """Check if component depends on target."""
        return target_id in comp.requirements

    def _remove_components_from_batches(self, component_ids: list[str]):
        """Remove components from batches."""
        ids_set = set(component_ids)
        current_batch = self._progress_bar.value() // len(self._batches)

        for i in range(current_batch + 1, len(self._batches)):
            self._batches[i] = [
                c for c in self._batches[i] if f"{c.mod.id}:{c.component.key}" not in ids_set
            ]

        self._batches = [b for b in self._batches if b]

    # ========================================
    # UI Helpers
    # ========================================

    def _append_output(self, text: str, color: str = None):
        """Append text to output."""
        cursor = self._output_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        if color:
            fmt.setForeground(QColor(color))

        cursor.setCharFormat(fmt)
        cursor.insertText(text)

        if not text.endswith("\n"):
            cursor.insertText("\n")

        self._output_text.setTextCursor(cursor)
        self._output_text.ensureCursorVisible()

    def _show_installation_summary(self):
        """Show summary dialog."""
        summary_text = tr("page.installation.summary.message")

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(tr("page.installation.summary.title"))
        msg_box.setText(summary_text)
        msg_box.setIcon(
            QMessageBox.Icon.Information
            if self._stats["errors"] == 0
            else QMessageBox.Icon.Warning
        )
        msg_box.exec()

    def _update_navigation_buttons(self):
        """Update navigation buttons."""
        self.notify_navigation_changed()

    def is_resume(self) -> bool:
        is_resume = self._installation_state and (
            self._installation_state.last_installed_component_index >= 0
            or self._installation_state.current_sequence > 0
        )
        return is_resume

    def _display_installation_summary(self):
        """Display installation summary on page load."""
        selected_game = self.state_manager.get_selected_game()
        game_manager = self.state_manager.get_game_manager()
        game_def = game_manager.get(selected_game)
        install_order_data = self.state_manager.get_install_order()

        if not game_def or not install_order_data:
            self._append_output(tr("page.installation.summary.no_data"), COLOR_WARNING)
            return

        # Check if resuming
        is_resume = self.is_resume()

        # Build summary
        self._append_output("=" * 80 + "\n", COLOR_INFO)

        if is_resume:
            self._append_output(
                tr("page.installation.summary.resume_title") + "\n", COLOR_WARNING
            )
        else:
            self._append_output(tr("page.installation.summary.new_title") + "\n", COLOR_INFO)

        self._append_output("=" * 80 + "\n\n", COLOR_INFO)

        # Count sequences and components
        total_sequences = game_def.sequence_count
        sequence_details = []
        total_components = 0

        for seq_idx in range(total_sequences):
            sequence = game_def.get_sequence(seq_idx)
            if not sequence:
                continue

            order_list = install_order_data.get(seq_idx, [])
            count_components = len(order_list)
            total_components += count_components

            game_folder = self.state_manager.get_game_folders().get(sequence.game)

            sequence_details.append(
                {
                    "idx": seq_idx,
                    "name": sequence.name,
                    "game": sequence.game,
                    "folder": game_folder,
                    "count_components": count_components,
                }
            )

        # Display general info
        self._append_output(
            tr("page.installation.summary.total_sequences", count=total_sequences) + "\n",
            COLOR_INFO,
        )
        self._append_output(
            tr("page.installation.summary.total_components", count=total_components) + "\n\n",
            COLOR_INFO,
        )

        # Display sequence details
        for seq_info in sequence_details:
            self._append_output(
                f"  {tr('page.installation.summary.sequence', number=seq_info['idx'] + 1)}: "
                f"{seq_info['name']} ({seq_info['game'].upper()})\n"
            )
            self._append_output(
                f"    {tr('page.installation.summary.folder')}: {seq_info['folder']}\n"
            )
            self._append_output(
                f"    {tr('page.installation.summary.components', count=seq_info['count_components'])}\n\n"
            )

        # If resuming, show progress
        if is_resume:
            self._append_output("-" * 80 + "\n", COLOR_WARNING)
            self._append_output(
                tr("page.installation.summary.resume_info") + "\n", COLOR_WARNING
            )
            self._append_output("-" * 80 + "\n\n", COLOR_WARNING)

            current_seq = self._installation_state.current_sequence
            last_comp_idx = self._installation_state.last_installed_component_index
            installed_components = last_comp_idx + 1

            for seq_idx in range(total_sequences):
                if seq_idx < current_seq:
                    sequence = game_def.get_sequence(seq_idx)
                    if not sequence:
                        continue

                    order_list = install_order_data.get(seq_idx, [])
                    installed_components += len(order_list)

            # Calculate remaining components
            remaining = total_components - last_comp_idx - 1
            progress_pct = (
                int(installed_components / total_components * 100)
                if total_components > 0
                else 0
            )

            self._append_output(
                f"  {tr('page.installation.summary.current_sequence', number=current_seq + 1, total=total_sequences)}\n",
                COLOR_WARNING,
            )
            self._append_output(
                f"  {tr('page.installation.summary.installed_components', index=installed_components, total=total_components)}\n",
                COLOR_WARNING,
            )
            self._append_output(
                f"  {tr('page.installation.summary.remaining', count=remaining)}\n",
                COLOR_WARNING,
            )
            self._append_output(
                f"  {tr('page.installation.summary.progress', percent=progress_pct)}\n\n",
                COLOR_WARNING,
            )

            order_list = install_order_data.get(current_seq, [])
            next_comp = order_list[last_comp_idx + 1]
            mod_id, comp_key = next_comp.split(":", 1)
            mod = self._mod_manager.get_mod_by_id(mod_id)
            if mod:
                component = mod.get_component(comp_key)
                if component:
                    self._append_output(
                        f"  {tr('page.installation.summary.next_component', mod=mod.name, component=component.get_name(), comp_key=component.key)}\n\n",
                        COLOR_WARNING,
                    )

            self._progress_bar.setMaximum(sequence_details[current_seq].get("count_components"))
            self._progress_bar.setValue(last_comp_idx + 1)
            self._append_output("=" * 80 + "\n\n", COLOR_INFO)
            self._append_output(
                tr("page.installation.summary.ready_to_resume") + "\n\n", COLOR_STATUS_COMPLETE
            )
        else:
            self._append_output("=" * 80 + "\n\n", COLOR_INFO)
            self._append_output(
                tr("page.installation.summary.ready") + "\n\n", COLOR_STATUS_COMPLETE
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

    def clear_installation_state(self):
        """Clear installation state."""
        self._installation_state = None
        # Also clear from state manager
        self.state_manager.set_page_option(
            self.get_page_id(), "last_installed_component_index", None
        )
        self.state_manager.set_page_option(
            self.get_page_id(), "last_installed_batch_index", None
        )
        self.state_manager.set_page_option(self.get_page_id(), "current_sequence", None)
        logger.info("Installation state cleared")

    # ========================================
    # BasePage Implementation
    # ========================================

    def get_page_id(self) -> str:
        return "installation"

    def get_page_title(self) -> str:
        return tr("page.installation.title")

    def get_additional_buttons(self) -> list[QPushButton]:
        return [self._btn_start_pause, self._btn_stop, self._btn_cancel]

    def get_next_button_config(self) -> ButtonConfig:
        return ButtonConfig(
            visible=False, enabled=self.can_go_to_next_page(), text=tr("button.finish")
        )

    def can_go_to_next_page(self) -> bool:
        """Can proceed if complete with no errors."""
        return not self._is_installing and self._stats["errors"] == 0

    def can_go_to_previous_page(self) -> bool:
        """Cannot go back during installation."""
        return not self._is_installing and not self.is_resume()

    def on_page_shown(self):
        """Called when page shown."""
        super().on_page_shown()

        self._output_text.clear()
        self._stats_widget.reset()
        self._progress_bar.setValue(0)
        self._stats = {"success": 0, "warnings": 0, "errors": 0, "skipped": 0}

        self._display_installation_summary()

    def on_page_hidden(self):
        """Called when page hidden."""
        super().on_page_hidden()

        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait()

    def retranslate_ui(self):
        """Update UI text."""
        if self._is_installing:
            if self._is_paused:
                self._btn_start_pause.setText(tr("page.installation.btn_resume"))
            else:
                self._btn_start_pause.setText(tr("page.installation.btn_pause"))
        else:
            self._btn_start_pause.setText(tr("page.installation.btn_start"))

        self._btn_stop.setText(tr("page.installation.btn_stop"))
        self._btn_cancel.setText(tr("page.installation.btn_cancel"))
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
        """Load state from state manager."""
        super().load_state()

        page_id = self.get_page_id()

        self._cb_batch_install.setChecked(
            self.state_manager.get_page_option(page_id, "batch_install", self._batch_install)
        )
        self._cb_pause_on_error.setChecked(
            self.state_manager.get_page_option(page_id, "pause_on_error", self._pause_on_error)
        )
        self._cb_pause_on_warning.setChecked(
            self.state_manager.get_page_option(
                page_id, "pause_on_warning", self._pause_on_warning
            )
        )

        # Load installation state
        last_comp_idx = self.state_manager.get_page_option(
            page_id, "last_installed_component_index", -1
        )
        last_batch_idx = self.state_manager.get_page_option(
            page_id, "last_installed_batch_index", -1
        )
        current_seq = self.state_manager.get_page_option(page_id, "current_sequence", 0)

        # Only create installation state if there's a valid saved state
        if last_comp_idx >= 0 or current_seq > 0:
            self._installation_state = InstallationState(
                last_installed_component_index=last_comp_idx,
                last_installed_batch_index=last_batch_idx,
                current_sequence=current_seq,
            )
            logger.info(
                "Loaded installation state: sequence=%d, component=%d, batch=%d",
                current_seq,
                last_comp_idx,
                last_batch_idx,
            )
        else:
            self._installation_state = None

    def save_state(self) -> None:
        """Save page data to state manager."""
        super().save_state()

        page_id = self.get_page_id()

        if self._installation_state:
            self.state_manager.set_page_option(
                page_id,
                "last_installed_component_index",
                self._installation_state.last_installed_component_index,
            )
            self.state_manager.set_page_option(
                page_id,
                "last_installed_batch_index",
                self._installation_state.last_installed_batch_index,
            )
            self.state_manager.set_page_option(
                page_id, "current_sequence", self._installation_state.current_sequence
            )
        else:
            self.state_manager.set_page_option(page_id, "last_installed_component_index", -1)
            self.state_manager.set_page_option(page_id, "last_installed_batch_index", -1)
            self.state_manager.set_page_option(page_id, "current_sequence", 0)

        self.state_manager.set_page_option(page_id, "batch_install", self._batch_install)
        self.state_manager.set_page_option(page_id, "pause_on_error", self._pause_on_error)
        self.state_manager.set_page_option(page_id, "pause_on_warning", self._pause_on_warning)
