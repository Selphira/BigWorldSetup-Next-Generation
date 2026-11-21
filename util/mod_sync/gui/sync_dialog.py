"""GUI for mod synchronization using PySide6."""

from __future__ import annotations

import logging
from pathlib import Path
import re

from PySide6.QtCore import QSettings, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core.enums.GameEnum import GameEnum
from util.mod_sync.models.sync_result import ModFilter, SyncConfig, SyncStats
from util.mod_sync.orchestrator import ModSyncOrchestrator

logger = logging.getLogger(__name__)


class SyncWorker(QThread):
    """Worker thread for synchronization to avoid blocking UI."""

    progress = Signal(int, int, str)  # current, total, mod_name
    completed = Signal(object)  # SyncStats
    log = Signal(str)  # Log message
    error = Signal(str)  # Error message

    def __init__(self, config: SyncConfig):
        """Initialize worker.

        Args:
            config: Synchronization configuration
        """
        super().__init__()
        self.config = config

    def run(self) -> None:
        """Run synchronization in background thread."""
        try:
            self.log.emit("Starting synchronization...")

            orchestrator = ModSyncOrchestrator(self.config)

            def progress_callback(current: int, total: int, mod_name: str) -> None:
                self.progress.emit(current, total, mod_name)

            stats = orchestrator.run(progress_callback)
            self.completed.emit(stats)

        except Exception as e:
            logger.error(f"Synchronization error: {e}", exc_info=True)
            self.error.emit(str(e))


class ModSyncDialog(QDialog):
    """GUI dialog for mod definition synchronization."""

    def __init__(self, parent=None):
        """Initialize dialog.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)
        self.setWindowTitle("Mod Definition Synchronization")
        self.resize(900, 700)
        self.worker: SyncWorker | None = None
        self._close_pending = False
        self.settings = QSettings("BWS-EE", "ModSync")

        self._setup_ui()
        self._load_settings()

    def _setup_ui(self) -> None:
        """Setup user interface."""
        layout = QVBoxLayout(self)

        # === Options Group ===
        options_group = QGroupBox("Options")
        options_layout = QVBoxLayout()

        self.force_lcc_checkbox = QCheckBox("Force LCC refresh (ignore cache)")
        options_layout.addWidget(self.force_lcc_checkbox)

        options_group.setLayout(options_layout)
        layout.addWidget(options_group)

        # === Paths Group ===
        paths_group = QGroupBox("Paths")
        paths_layout = QVBoxLayout()

        # Definitions directory
        def_layout = QHBoxLayout()
        def_layout.addWidget(QLabel("Definitions:"))
        self.def_dir_edit = QLineEdit("data/mods")
        def_layout.addWidget(self.def_dir_edit)
        browse_def = QPushButton("Browse...")
        browse_def.clicked.connect(lambda: self._browse_dir(self.def_dir_edit))
        def_layout.addWidget(browse_def)
        paths_layout.addLayout(def_layout)

        # Archives directory
        arch_layout = QHBoxLayout()
        arch_layout.addWidget(QLabel("Archives:"))
        self.arch_dir_edit = QLineEdit()
        arch_layout.addWidget(self.arch_dir_edit)
        browse_arch = QPushButton("Browse...")
        browse_arch.clicked.connect(lambda: self._browse_dir(self.arch_dir_edit))
        arch_layout.addWidget(browse_arch)
        paths_layout.addLayout(arch_layout)

        # Mods directory (extracted)
        mods_layout = QHBoxLayout()
        mods_layout.addWidget(QLabel("Mods (extracted):"))
        self.mods_dir_edit = QLineEdit()
        mods_layout.addWidget(self.mods_dir_edit)
        browse_mods = QPushButton("Browse...")
        browse_mods.clicked.connect(lambda: self._browse_dir(self.mods_dir_edit))
        mods_layout.addWidget(browse_mods)
        paths_layout.addLayout(mods_layout)

        # GitHub token
        token_layout = QHBoxLayout()
        token_layout.addWidget(QLabel("GitHub Token:"))
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_edit.setPlaceholderText("Optional - for higher API rate limit")
        token_layout.addWidget(self.token_edit)
        paths_layout.addLayout(token_layout)

        paths_group.setLayout(paths_layout)
        layout.addWidget(paths_group)

        # === Filters Group ===
        # Filters are deliberately not persisted: a forgotten filter would
        # silently restrict the next run.
        filters_group = QGroupBox("Filters (empty = all)")
        filters_layout = QVBoxLayout()

        only_layout = QHBoxLayout()
        only_layout.addWidget(QLabel("Only mods:"))
        self.only_mods_edit = QLineEdit()
        self.only_mods_edit.setPlaceholderText("tp2 names separated by spaces or commas")
        only_layout.addWidget(self.only_mods_edit)
        filters_layout.addLayout(only_layout)

        games_layout = QHBoxLayout()
        games_layout.addWidget(QLabel("Games:"))
        self.game_checkboxes: dict[str, QCheckBox] = {}
        for game in GameEnum:
            checkbox = QCheckBox(game.value)
            self.game_checkboxes[game.value] = checkbox
            games_layout.addWidget(checkbox)
        games_layout.addStretch()
        filters_layout.addLayout(games_layout)

        filters_group.setLayout(filters_layout)
        layout.addWidget(filters_group)

        # === Actions Group ===
        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout()

        self.extract_archives_checkbox = QCheckBox("Extract archives for TP2/INI parsing")
        actions_layout.addWidget(self.extract_archives_checkbox)

        self.create_new_checkbox = QCheckBox("Create new definitions from LCC")
        self.create_new_checkbox.setChecked(True)
        actions_layout.addWidget(self.create_new_checkbox)

        self.update_existing_checkbox = QCheckBox("Update existing definitions")
        self.update_existing_checkbox.setChecked(True)
        actions_layout.addWidget(self.update_existing_checkbox)

        self.extract_tp2_checkbox = QCheckBox("Extract data from .tp2 files")
        self.extract_tp2_checkbox.setChecked(True)
        actions_layout.addWidget(self.extract_tp2_checkbox)

        self.extract_ini_checkbox = QCheckBox("Extract data from .ini files")
        self.extract_ini_checkbox.setChecked(True)
        actions_layout.addWidget(self.extract_ini_checkbox)

        self.calc_hash_checkbox = QCheckBox("Calculate archive hashes")
        actions_layout.addWidget(self.calc_hash_checkbox)

        self.check_github_checkbox = QCheckBox("Check GitHub versions")
        actions_layout.addWidget(self.check_github_checkbox)

        actions_group.setLayout(actions_layout)
        layout.addWidget(actions_group)

        # === Start Button ===
        self.start_button = QPushButton("Start Synchronization")
        self.start_button.clicked.connect(self._start_sync)
        self.start_button.setMinimumHeight(40)
        layout.addWidget(self.start_button)

        # === Progress ===
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        layout.addWidget(self.progress_bar)

        self.current_label = QLabel("")
        self.current_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.current_label)

        # === Log ===
        log_label = QLabel("Log:")
        layout.addWidget(log_label)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(200)
        layout.addWidget(self.log_text)

    def _browse_dir(self, line_edit: QLineEdit) -> None:
        """Open directory browser dialog.

        Args:
            line_edit: Line edit to update with selected path
        """
        current_path = line_edit.text()
        dir_path = QFileDialog.getExistingDirectory(self, "Select Directory", current_path)
        if dir_path:
            line_edit.setText(dir_path)

    def _load_settings(self) -> None:
        """Load settings from QSettings."""
        self.def_dir_edit.setText(self.settings.value("definitions_dir", "data/mods"))
        self.arch_dir_edit.setText(self.settings.value("archives_dir", ""))
        self.mods_dir_edit.setText(self.settings.value("mods_dir", ""))
        self.token_edit.setText(self.settings.value("github_token", ""))

        self.force_lcc_checkbox.setChecked(
            self.settings.value("force_lcc_refresh", False, type=bool)
        )
        self.create_new_checkbox.setChecked(self.settings.value("create_new", True, type=bool))
        self.update_existing_checkbox.setChecked(
            self.settings.value("update_existing", True, type=bool)
        )
        self.extract_tp2_checkbox.setChecked(
            self.settings.value("extract_tp2", True, type=bool)
        )
        self.extract_ini_checkbox.setChecked(
            self.settings.value("extract_ini", True, type=bool)
        )
        self.extract_archives_checkbox.setChecked(
            self.settings.value("extract_archives", False, type=bool)
        )
        self.calc_hash_checkbox.setChecked(self.settings.value("calc_hash", False, type=bool))
        self.check_github_checkbox.setChecked(
            self.settings.value("check_github", False, type=bool)
        )

    def _save_settings(self) -> None:
        """Save settings to QSettings."""
        self.settings.setValue("definitions_dir", self.def_dir_edit.text())
        self.settings.setValue("archives_dir", self.arch_dir_edit.text())
        self.settings.setValue("mods_dir", self.mods_dir_edit.text())
        self.settings.setValue("github_token", self.token_edit.text())

        self.settings.setValue("force_lcc_refresh", self.force_lcc_checkbox.isChecked())
        self.settings.setValue("create_new", self.create_new_checkbox.isChecked())
        self.settings.setValue("update_existing", self.update_existing_checkbox.isChecked())
        self.settings.setValue("extract_tp2", self.extract_tp2_checkbox.isChecked())
        self.settings.setValue("extract_ini", self.extract_ini_checkbox.isChecked())
        self.settings.setValue("extract_archives", self.extract_archives_checkbox.isChecked())
        self.settings.setValue("calc_hash", self.calc_hash_checkbox.isChecked())
        self.settings.setValue("check_github", self.check_github_checkbox.isChecked())

    def _start_sync(self) -> None:
        """Start synchronization process."""
        # Save settings
        self._save_settings()

        # Build configuration
        config = SyncConfig(
            definitions_dir=Path(self.def_dir_edit.text()),
            archives_dir=Path(self.arch_dir_edit.text()) if self.arch_dir_edit.text() else None,
            mods_dir=Path(self.mods_dir_edit.text()) if self.mods_dir_edit.text() else None,
            force_lcc_refresh=self.force_lcc_checkbox.isChecked(),
            github_token=self.token_edit.text() or None,
            create_new=self.create_new_checkbox.isChecked(),
            update_existing=self.update_existing_checkbox.isChecked(),
            extract_tp2=self.extract_tp2_checkbox.isChecked(),
            extract_ini=self.extract_ini_checkbox.isChecked(),
            extract_archives=self.extract_archives_checkbox.isChecked(),
            calculate_hashes=self.calc_hash_checkbox.isChecked(),
            check_github_versions=self.check_github_checkbox.isChecked(),
            mod_filter=self._build_mod_filter(),
        )

        # Validate paths
        if not config.definitions_dir.exists():
            self._log_error(f"Definitions directory does not exist: {config.definitions_dir}")
            return

        # Disable UI during sync
        self._set_ui_enabled(False)
        self.log_text.clear()
        self.progress_bar.setValue(0)
        self.current_label.setText("")

        # Start worker thread
        self._close_pending = False
        self.worker = SyncWorker(config)
        self.worker.progress.connect(self._on_progress)
        self.worker.log.connect(self._on_log)
        self.worker.completed.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _build_mod_filter(self) -> ModFilter:
        """Build the mod filter from the filter widgets."""
        tp2_names = re.split(r"[\s,;]+", self.only_mods_edit.text().strip())
        games = [game for game, checkbox in self.game_checkboxes.items() if checkbox.isChecked()]

        return ModFilter(tp2_names=frozenset(filter(None, tp2_names)), games=frozenset(games))

    def _set_ui_enabled(self, enabled: bool) -> None:
        """Enable or disable UI controls.

        Args:
            enabled: Whether to enable controls
        """
        self.start_button.setEnabled(enabled)
        self.force_lcc_checkbox.setEnabled(enabled)
        self.create_new_checkbox.setEnabled(enabled)
        self.update_existing_checkbox.setEnabled(enabled)
        self.extract_tp2_checkbox.setEnabled(enabled)
        self.extract_ini_checkbox.setEnabled(enabled)
        self.calc_hash_checkbox.setEnabled(enabled)
        self.check_github_checkbox.setEnabled(enabled)
        self.only_mods_edit.setEnabled(enabled)
        for checkbox in self.game_checkboxes.values():
            checkbox.setEnabled(enabled)

    def _on_progress(self, current: int, total: int, mod_name: str) -> None:
        """Handle progress update.

        Args:
            current: Current mod number
            total: Total number of mods
            mod_name: Name of current mod
        """
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(current)
        self.current_label.setText(f"Processing: {mod_name} ({current}/{total})")

    def _on_log(self, message: str) -> None:
        """Handle log message.

        Args:
            message: Log message to display
        """
        self.log_text.append(message)

    def _on_error(self, error: str) -> None:
        """Handle error.

        Args:
            error: Error message
        """
        self._log_error(f"Synchronization failed: {error}")
        self._set_ui_enabled(True)

    def _log_error(self, message: str) -> None:
        """Log error message with formatting.

        Args:
            message: Error message
        """
        self.log_text.append(f"<span style='color: red;'>✗ {message}</span>")

    def _on_finished(self, stats: SyncStats) -> None:
        """Handle synchronization completion.

        Args:
            stats: Synchronization statistics
        """
        self._set_ui_enabled(True)
        self.current_label.setText("Synchronization complete!")

        # Display summary
        summary = stats.get_summary()
        self.log_text.append(f"<pre>{summary}</pre>")

        # Display GitHub report if any
        if github_report := stats.get_github_report():
            self.log_text.append(f"<pre>{github_report}</pre>")

        # Display detailed results
        if stats.results:
            self.log_text.append("\n<b>Detailed Results:</b>")
            for result in stats.results:
                color = {
                    "created": "green",
                    "updated": "blue",
                    "unchanged": "gray",
                    "error": "red",
                    "skipped": "orange",
                }.get(result.status.value, "black")

                msg = str(result)
                self.log_text.append(f"<span style='color: {color};'>{msg}</span>")

    def reject(self) -> None:
        if self.worker and self.worker.isRunning():
            self.close()
        else:
            super().reject()

    def closeEvent(self, event) -> None:
        """Handle dialog close event.

        Args:
            event: Close event
        """
        # Wait for worker to finish if running
        if self.worker and self.worker.isRunning():
            event.ignore()
            if not self._close_pending:
                self._close_pending = True
                self.worker.finished.connect(self.close)
            return
        event.accept()
