import logging
from pathlib import Path
import shutil

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QHelpEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidgetItem,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
)

from constants import (
    COLOR_ERROR,
    COLOR_STATUS_COMPLETE,
    COLOR_STATUS_NONE,
    COLOR_WARNING,
    MARGIN_STANDARD,
    SPACING_LARGE,
    SPACING_SMALL,
)
from core.BackupInfo import BackupInfo, BackupStatus
from core.BackupManager import BackupManager
from core.StateManager import StateManager
from core.TranslationManager import tr
from ui.pages.BasePage import BasePage
from ui.widgets.HoverTableWidget import HoverTableWidget

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

COL_BACKUP_NAME = 0
COL_GAME_CODE = 1
COL_DATE = 2
COL_SIZE = 3
COL_MODDED = 4
COL_STATUS = 5
COLUMN_COUNT = 6


# ============================================================================
# Worker Threads
# ============================================================================


class BackupSizeCalculatorWorker(QThread):
    """Worker thread for calculating backup size."""

    completed = Signal(object, bool)  # size_bytes, is_modded

    def __init__(self, manager: BackupManager, game_path: Path):
        super().__init__()
        self._manager = manager
        self._game_path = game_path

    def run(self) -> None:
        """Calculate backup size."""
        try:
            is_modded = self._manager.is_game_modded(self._game_path)
            size = self._manager.calculate_backup_size(self._game_path)
            self.completed.emit(size, is_modded)
        except Exception as e:
            logger.error(f"Error calculating backup size: {e}")
            self.completed.emit(0, False)


class BackupCreationWorker(QThread):
    """Worker thread for creating backups."""

    started = Signal()
    progress = Signal(str, int, int)  # message, current, total
    completed = Signal(bool, str)  # success, message

    def __init__(self, manager: BackupManager, game_code: str, game_name: str, game_path: Path):
        super().__init__()
        self._manager = manager
        self._game_code = game_code
        self._game_name = game_name
        self._game_path = game_path

    def run(self) -> None:
        """Run backup creation."""
        try:
            self.started.emit()

            def progress_callback(message: str, current: int, total: int):
                self.progress.emit(message, current, total)

            success, backup_info, error = self._manager.create_backup(
                self._game_code,
                self._game_name,
                self._game_path,
                progress_callback,
            )

            if success:
                self.completed.emit(True, tr("page.backup.backup_created_successfully"))
            else:
                self.completed.emit(False, error)

        except Exception as e:
            logger.error(f"Error creating backup: {e}")
            self.completed.emit(False, str(e))


class BackupRestorationWorker(QThread):
    """Worker thread for restoring backups."""

    started = Signal()
    progress = Signal(str, int, int)  # message, current, total
    completed = Signal(bool, Path, str)  # success, removed_dir, message

    def __init__(self, manager: BackupManager, backup_id: str, game_path: Path):
        super().__init__()
        self._manager = manager
        self._backup_id = backup_id
        self._game_path = game_path

    def run(self) -> None:
        """Run backup restoration."""
        try:
            self.started.emit()

            def progress_callback(message: str, current: int, total: int):
                self.progress.emit(message, current, total)

            success, removed_dir, error = self._manager.restore_backup(
                self._backup_id, self._game_path, progress_callback
            )

            if success:
                self.completed.emit(
                    True, removed_dir, tr("page.backup.backup_restored_successfully")
                )
            else:
                self.completed.emit(False, None, error)

        except Exception as e:
            logger.error(f"Error restoring backup: {e}")
            self.completed.emit(False, None, str(e))


# ============================================================================
# Backup Page
# ============================================================================


class BackupPage(BasePage):
    """Page for managing game backups."""

    STATUS_COLORS = {
        BackupStatus.VALID: QColor(COLOR_STATUS_COMPLETE),
        BackupStatus.CORRUPTED: QColor(COLOR_ERROR),
        BackupStatus.INCOMPLETE: QColor(COLOR_WARNING),
    }

    def __init__(self, state_manager: StateManager):
        """Initialize backup page.

        Args:
            state_manager: Application state manager
        """
        super().__init__(state_manager)

        self._backup_manager = BackupManager()
        self._game_manager = self.state_manager.get_game_manager()

        # Tracking
        self._backups: dict[str, BackupInfo] = {}
        self._is_operating = False
        self._worker: QThread | None = None
        self._modded_games: set[str] = set()
        self._selected_backup_id: str | None = None

        # UI components
        self._backup_table: HoverTableWidget | None = None
        self._filter_combo: QComboBox | None = None
        self._btn_create_backup: QPushButton | None = None
        self._btn_restore: QPushButton | None = None
        self._btn_edit: QPushButton | None = None
        self._btn_delete: QPushButton | None = None
        self._progress_bar: QProgressBar | None = None
        self._warning_label: QLabel | None = None

        self._create_widgets()
        self._create_additional_buttons()

        logger.info("BackupPage initialized")

    def _create_widgets(self) -> None:
        """Create page UI layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_LARGE)
        layout.setContentsMargins(
            MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD
        )

        vlayout = QVBoxLayout()
        vlayout.setSpacing(SPACING_SMALL)
        vlayout.setContentsMargins(0, 0, 0, 0)

        # Header
        header_layout = QHBoxLayout()
        header_layout.setSpacing(SPACING_SMALL)

        self._title_label = self._create_section_title()
        header_layout.addWidget(self._title_label)
        header_layout.addStretch()

        # Game filter
        filter_label = QLabel()
        header_layout.addWidget(filter_label)
        self._filter_label = filter_label

        self._filter_combo = QComboBox()
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)
        header_layout.addWidget(self._filter_combo)

        vlayout.addLayout(header_layout)

        # Warning label for modded games
        self._warning_label = QLabel()
        self._warning_label.setWordWrap(True)
        self._warning_label.setObjectName("warning")
        self._warning_label.setVisible(False)
        vlayout.addWidget(self._warning_label)

        # Backup table
        self._backup_table = HoverTableWidget()
        self._backup_table.setColumnCount(COLUMN_COUNT)
        self._backup_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._backup_table.customContextMenuRequested.connect(self._show_context_menu)
        self._backup_table.itemSelectionChanged.connect(self._on_selection_changed)
        self._backup_table.itemDoubleClicked.connect(
            lambda: self._restore_selected_backup() if self._selected_backup_id else None
        )
        # Install event filter for tooltips on entire rows
        self._backup_table.viewport().installEventFilter(self)

        header = self._backup_table.horizontalHeader()
        header.setSectionResizeMode(COL_BACKUP_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_GAME_CODE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_DATE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_SIZE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_MODDED, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)

        vlayout.addWidget(self._backup_table, stretch=1)

        layout.addLayout(vlayout)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)

    def _create_additional_buttons(self) -> None:
        """Create additional action buttons."""
        self._btn_create_backup = QPushButton()
        self._btn_create_backup.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_create_backup.clicked.connect(self._create_backup)

        self._btn_restore = QPushButton()
        self._btn_restore.setEnabled(False)
        self._btn_restore.clicked.connect(self._restore_selected_backup)

        self._btn_edit = QPushButton()
        self._btn_edit.setEnabled(False)
        self._btn_edit.clicked.connect(self._edit_selected_backup)

        self._btn_delete = QPushButton()
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._delete_selected_backup)

    # ========================================
    # Event Filter for Tooltips
    # ========================================

    def eventFilter(self, obj, event) -> bool:
        """Event filter to show tooltips on entire table rows.

        Args:
            obj: Object that triggered event
            event: Event

        Returns:
            True if event handled
        """
        if obj == self._backup_table.viewport() and event.type() == event.Type.ToolTip:
            help_event = QHelpEvent(event)
            pos = help_event.pos()
            row = self._backup_table.rowAt(pos.y())
            col = self._backup_table.columnAt(pos.x())

            # Don't show tooltip on actions column
            if row >= 0 and col >= 0:
                backup_id_item = self._backup_table.item(row, COL_BACKUP_NAME)
                if backup_id_item:
                    backup_id = backup_id_item.data(Qt.ItemDataRole.UserRole)
                    backup_info = self._backups.get(backup_id)
                    if backup_info and backup_info.notes:
                        QToolTip.showText(help_event.globalPos(), backup_info.notes)
                        return True

        return super().eventFilter(obj, event)

    # ========================================
    # Loading & Display
    # ========================================

    def _load_backups(self) -> None:
        """Load backups for selected game sequences."""
        self._backups.clear()
        self._modded_games.clear()

        self._backup_manager.set_backup_root(self.state_manager.get_backup_folder())

        selected_game = self.state_manager.get_selected_game()
        game_def = self._game_manager.get(selected_game)
        game_codes = set()
        for sequence in game_def.sequences:
            if sequence.game:
                game_codes.add(sequence.game)

        # Check which games are modded
        game_folders = self.state_manager.get_game_folders()
        for game_code in game_codes:
            game = self._game_manager.get(game_code)
            if not game:
                continue
            folder_keys = set(game.get_folder_keys())
            for key in folder_keys & game_folders.keys():
                game_path = Path(game_folders[key])
                if game_path.exists() and self._backup_manager.is_game_modded(game_path):
                    self._modded_games.add(game_code)
                break

        # Load backups
        backups = self._backup_manager.list_backups(list(game_codes))
        for backup in backups:
            self._backups[backup.backup_id] = backup

        self._refresh_backup_table()
        self._update_filter_combo(game_codes)
        self._update_modded_warning()

        logger.info(f"Loaded {len(self._backups)} backups")

    def _update_filter_combo(self, game_codes: set[str]) -> None:
        """Update filter combo with game codes.

        Args:
            game_codes: Set of game codes
        """
        if len(game_codes) > 1:
            current = self._filter_combo.currentData()
            self._filter_label.setVisible(True)
            self._filter_combo.setVisible(True)
            self._filter_combo.blockSignals(True)
            self._filter_combo.clear()
            self._filter_combo.addItem(tr("page.backup.filter.all"), None)

            for game_code in sorted(game_codes):
                game_def = self._game_manager.get(game_code)
                if game_def:
                    self._filter_combo.addItem(game_def.name, game_code)

            for i in range(self._filter_combo.count()):
                if self._filter_combo.itemData(i) == current:
                    self._filter_combo.setCurrentIndex(i)
                    break

            self._filter_combo.blockSignals(False)
        else:
            self._filter_label.setVisible(False)
            self._filter_combo.setVisible(False)

    def _update_modded_warning(self) -> None:
        """Update warning label if games are modded."""
        if self._modded_games:
            game_names = []
            for game_code in self._modded_games:
                game = self._game_manager.get(game_code)
                if game:
                    game_names.append(game.name)

            warning_text = tr("page.backup.warning_modded_games", games=", ".join(game_names))
            self._warning_label.setText(warning_text)
            self._warning_label.setVisible(True)
        else:
            self._warning_label.setVisible(False)

    def _refresh_backup_table(self) -> None:
        """Refresh the backup table display."""
        if not self._backup_table:
            return

        self._selected_backup_id = None
        self._backup_table.setSortingEnabled(False)
        self._backup_table.setRowCount(0)
        self._update_navigation_buttons()

        filter_game = self._filter_combo.currentData() if self._filter_combo else None

        row = 0
        for backup_id, backup_info in self._backups.items():
            if filter_game and backup_info.game_code != filter_game:
                continue

            self._backup_table.insertRow(row)

            # Column 0: Backup name (display name or ID)
            name_item = QTableWidgetItem(backup_info.display_name)
            name_item.setData(Qt.ItemDataRole.UserRole, backup_id)
            self._backup_table.setItem(row, COL_BACKUP_NAME, name_item)

            # Column 1: Game code
            code_item = QTableWidgetItem(backup_info.game_code)
            self._backup_table.setItem(row, COL_GAME_CODE, code_item)

            # Column 2: Date
            date_str = backup_info.creation_date.strftime("%d/%m/%Y %H:%M:%S")
            date_item = QTableWidgetItem(date_str)
            self._backup_table.setItem(row, COL_DATE, date_item)

            # Column 3: Size
            if backup_info.size_gb >= 1:
                size_str = f"{backup_info.size_gb:.2f} GB"
            else:
                size_str = f"{backup_info.size_mb:.1f} MB"
            size_item = QTableWidgetItem(size_str)
            size_item.setData(Qt.ItemDataRole.UserRole, backup_info.total_size)
            self._backup_table.setItem(row, COL_SIZE, size_item)

            # Column 4: Modded
            modded_text = (
                tr("page.backup.yes") if backup_info.is_modded else tr("page.backup.no")
            )
            modded_item = QTableWidgetItem(modded_text)
            modded_item.setData(Qt.ItemDataRole.UserRole, backup_info.is_modded)
            if backup_info.is_modded:
                modded_item.setForeground(QColor(COLOR_WARNING))
            self._backup_table.setItem(row, COL_MODDED, modded_item)

            # Column 5: Status
            status_text = tr(f"page.backup.status.{backup_info.status.value}")
            status_item = QTableWidgetItem(status_text)
            status_item.setData(Qt.ItemDataRole.UserRole, backup_info.status.value)
            color = self.STATUS_COLORS.get(backup_info.status, QColor(COLOR_STATUS_NONE))
            status_item.setForeground(color)
            self._backup_table.setItem(row, COL_STATUS, status_item)

            row += 1

        self._backup_table.setSortingEnabled(True)
        self._backup_table.sortItems(COL_DATE, Qt.SortOrder.DescendingOrder)

    # ========================================
    # Actions
    # ========================================

    def _create_backup(self) -> None:
        """Create a new backup."""
        if self._is_operating:
            return

        selected_game = self.state_manager.get_selected_game()
        game_def = self._game_manager.get(selected_game)
        game_codes = []
        for sequence in game_def.sequences:
            if sequence.game:
                game_codes.append(sequence.game)

        # Select game to backup
        selected_code = None
        if len(game_codes) == 1:
            selected_code = game_codes[0]
        else:
            # Show selection dialog
            game_names = [self._game_manager.get(code).name for code in game_codes]
            selected_name, ok = QInputDialog.getItem(
                self,
                tr("page.backup.select_game_title"),
                tr("page.backup.select_game_message"),
                game_names,
                0,
                False,
            )
            if ok:
                idx = game_names.index(selected_name)
                selected_code = game_codes[idx]

        if not selected_code:
            return

        game_folders = self.state_manager.get_game_folders()
        game = self._game_manager.get(selected_code)
        game_path = None

        for folder_key, path in game_folders.items():
            if folder_key in game.get_folder_keys():
                game_path = Path(path)
                break

        self._is_operating = True
        self._update_navigation_buttons()

        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat(tr("page.backup.calculating_size"))

        calculator = BackupSizeCalculatorWorker(self._backup_manager, game_path)
        calculator.completed.connect(
            lambda size, is_modded: self._on_size_calculated(
                size, is_modded, selected_code, game.name, game_path
            )
        )
        calculator.start()

        self._worker = calculator

    def _restore_selected_backup(self) -> None:
        """Restore the selected backup."""
        if self._selected_backup_id:
            self._restore_backup(self._selected_backup_id)

    def _edit_selected_backup(self) -> None:
        """Edit the selected backup."""
        if self._selected_backup_id:
            self._edit_backup(self._selected_backup_id)

    def _delete_selected_backup(self) -> None:
        """Delete the selected backup."""
        if self._selected_backup_id:
            self._delete_backup(self._selected_backup_id)

    def _on_size_calculated(
        self,
        backup_size: int | object,
        is_modded: bool,
        game_code: str,
        game_name: str,
        game_path: Path,
    ) -> None:
        """Handle size calculation completion.

        Args:
            backup_size: Calculated backup size in bytes
            is_modded: Whether game is modded
            game_code: Game code
            game_name: Game name
            game_path: Game path
        """
        self._worker = None
        self._progress_bar.setVisible(False)
        self._is_operating = False
        self._update_navigation_buttons()

        backup_size = int(backup_size)

        if backup_size == 0:
            QMessageBox.critical(
                self,
                tr("page.backup.error_title"),
                tr("page.backup.error_calculating_size"),
            )
            return

        free_space = self._backup_manager.get_free_space()
        free_gb = free_space / (1024**3)
        size_gb = backup_size / (1024**3)

        if free_gb <= size_gb:
            QMessageBox.warning(
                self,
                tr("page.backup.warning_title"),
                tr(
                    "page.backup.warning_low_disk_space",
                    space=f"{free_gb:.1f}",
                    size=f"{size_gb:.1f}",
                ),
            )
            return

        # Show confirmation with info
        modded_status = tr("page.backup.modded") if is_modded else tr("page.backup.vanilla")
        message = tr(
            "page.backup.confirm_create_message",
            game=game_name,
            status=modded_status,
            size=f"{size_gb:.2f}",
            free=f"{free_gb:.1f}",
        )

        response = QMessageBox.question(
            self,
            tr("page.backup.confirm_create_title"),
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if response != QMessageBox.StandardButton.Yes:
            return

        self._is_operating = True
        self._update_navigation_buttons()

        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)

        self._worker = BackupCreationWorker(
            self._backup_manager, game_code, game_name, game_path
        )
        self._worker.started.connect(self._on_operation_started)
        self._worker.progress.connect(self._on_backup_progress)
        self._worker.completed.connect(self._on_backup_created)
        self._worker.start()

        logger.info(f"Starting backup creation for {game_code}")

    def _on_selection_changed(self) -> None:
        """Handle backup selection change."""
        selected_rows = self._backup_table.selectionModel().selectedRows()

        if selected_rows and not self._is_operating:
            row = selected_rows[0].row()
            backup_id_item = self._backup_table.item(row, COL_BACKUP_NAME)
            if backup_id_item:
                self._selected_backup_id = backup_id_item.data(Qt.ItemDataRole.UserRole)
                self._update_action_buttons(True)
                return

        self._selected_backup_id = None
        self._update_action_buttons(False)

    def _update_action_buttons(self, enabled: bool) -> None:
        """Update state of action buttons.

        Args:
            enabled: Whether buttons should be enabled
        """
        self._btn_restore.setEnabled(enabled)
        self._btn_edit.setEnabled(enabled)
        self._btn_delete.setEnabled(enabled)

    def _restore_backup(self, backup_id: str) -> None:
        """Restore a backup.

        Args:
            backup_id: Backup ID
        """
        if self._is_operating:
            return

        backup_info = self._backups.get(backup_id)
        if not backup_info:
            return

        game_folders = self.state_manager.get_game_folders()
        game = self._game_manager.get(backup_info.game_code)

        response = QMessageBox.question(
            self,
            tr("page.backup.confirm_restore_title"),
            tr(
                "page.backup.confirm_restore_message",
                game=game.name,
                name=backup_info.backup_id,
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if response != QMessageBox.StandardButton.Yes:
            return

        game_path = None

        for folder_key, path in game_folders.items():
            if folder_key in game.get_folder_keys():
                game_path = Path(path)
                break

        # Start restoration
        self._is_operating = True
        self._update_navigation_buttons()

        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)

        self._worker = BackupRestorationWorker(self._backup_manager, backup_id, game_path)
        self._worker.started.connect(self._on_operation_started)
        self._worker.progress.connect(self._on_backup_progress)
        self._worker.completed.connect(self._on_backup_restored)
        self._worker.start()

        logger.info(f"Starting backup restoration: {backup_id}")

    def _delete_backup(self, backup_id: str) -> None:
        """Delete a backup.

        Args:
            backup_id: Backup ID
        """
        if self._is_operating:
            return

        backup_info = self._backups.get(backup_id)
        if not backup_info:
            return

        response = QMessageBox.question(
            self,
            tr("page.backup.confirm_delete_title"),
            tr("page.backup.confirm_delete_message", name=backup_info.backup_id),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if response != QMessageBox.StandardButton.Yes:
            return

        success, error = self._backup_manager.delete_backup(backup_id)

        if success:
            del self._backups[backup_id]
            self._refresh_backup_table()
            QMessageBox.information(
                self,
                tr("page.backup.success_title"),
                tr("page.backup.backup_deleted_successfully"),
            )
        else:
            QMessageBox.critical(self, tr("page.backup.error_title"), error)

        logger.info(f"Deleted backup: {backup_id}")

    def _edit_backup(self, backup_id: str) -> None:
        """Edit backup name and notes.

        Args:
            backup_id: Backup ID
        """
        if self._is_operating:
            return

        backup_info = self._backups.get(backup_id)
        if not backup_info:
            return

        # Create dialog
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("page.backup.edit_title"))
        dialog.setMinimumWidth(400)

        layout = QVBoxLayout(dialog)

        # Custom name
        layout.addWidget(QLabel(tr("page.backup.edit_custom_name")))
        name_edit = QLineEdit(backup_info.custom_name)
        name_edit.setPlaceholderText(backup_info.backup_id)
        layout.addWidget(name_edit)

        # Notes
        layout.addWidget(QLabel(tr("page.backup.edit_notes")))

        notes_edit = QTextEdit()
        notes_edit.setPlainText(backup_info.notes)
        notes_edit.setMaximumHeight(100)
        layout.addWidget(notes_edit)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            custom_name = name_edit.text().strip()
            notes = notes_edit.toPlainText().strip()

            success, error = self._backup_manager.update_backup_metadata(
                backup_id, custom_name, notes
            )

            if success:
                backup_info.custom_name = custom_name
                backup_info.notes = notes
                self._refresh_backup_table()
            else:
                QMessageBox.critical(self, tr("page.backup.error_title"), error)

    def _validate_backup(self, backup_id: str) -> None:
        """Validate a backup's integrity.

        Args:
            backup_id: Backup ID
        """
        is_valid, error = self._backup_manager.validate_backup(backup_id)

        if is_valid:
            QMessageBox.information(
                self,
                tr("page.backup.success_title"),
                tr("page.backup.backup_valid"),
            )
        else:
            QMessageBox.warning(
                self,
                tr("page.backup.warning_title"),
                tr("page.backup.backup_invalid", error=error),
            )

    def _open_in_explorer(self, backup_id: str) -> None:
        """Open backup directory in file explorer.

        Args:
            backup_id: Backup ID
        """
        backup_info = self._backups.get(backup_id)
        if backup_info:
            backup_root = self.state_manager.get_backup_folder()
            if backup_root:
                backup_dir = backup_info.get_backup_dir(Path(backup_root))
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(backup_dir)))

    # ========================================
    # Context Menu
    # ========================================

    def _show_context_menu(self, position) -> None:
        """Show context menu for backup row.

        Args:
            position: Menu position
        """
        if self._is_operating:
            return

        item = self._backup_table.itemAt(position)
        if not item:
            return

        row = item.row()
        backup_id_item = self._backup_table.item(row, COL_BACKUP_NAME)
        if not backup_id_item:
            return

        backup_id = backup_id_item.data(Qt.ItemDataRole.UserRole)

        menu = QMenu(self)

        action_restore = menu.addAction(tr("page.backup.context_restore"))
        action_restore.triggered.connect(lambda: self._restore_backup(backup_id))

        action_edit = menu.addAction(tr("page.backup.context_edit"))
        action_edit.triggered.connect(lambda: self._edit_backup(backup_id))

        action_validate = menu.addAction(tr("page.backup.context_validate"))
        action_validate.triggered.connect(lambda: self._validate_backup(backup_id))

        action_open = menu.addAction(tr("page.backup.context_open_folder"))
        action_open.triggered.connect(lambda: self._open_in_explorer(backup_id))

        menu.addSeparator()

        action_delete = menu.addAction(tr("page.backup.context_delete"))
        action_delete.triggered.connect(lambda: self._delete_backup(backup_id))

        menu.exec(self._backup_table.viewport().mapToGlobal(position))

    # ========================================
    # Worker Callbacks
    # ========================================

    def _on_operation_started(self) -> None:
        """Handle operation start."""
        pass

    def _on_backup_progress(self, message: str, current: int, total: int) -> None:
        """Handle backup/restore progress update.

        Args:
            message: Progress message
            current: Current progress
            total: Total items
        """
        if total > 0:
            percent = int((current / total) * 100)
            self._progress_bar.setValue(percent)
            self._progress_bar.setFormat(f"{message} ({percent}%)")
        else:
            self._progress_bar.setFormat(message)

    def _on_backup_created(self, success: bool, message: str) -> None:
        """Handle backup creation completion.

        Args:
            success: Whether operation succeeded
            message: Result message
        """
        self._is_operating = False
        self._worker = None
        self._progress_bar.setVisible(False)
        self._update_navigation_buttons()

        if success:
            self._load_backups()
            QMessageBox.information(self, tr("page.backup.success_title"), message)
        else:
            QMessageBox.critical(self, tr("page.backup.error_title"), message)

    def _on_backup_restored(
        self, success: bool, removed_dir: Path | None, message: str
    ) -> None:
        """Handle backup restoration completion.

        Args:
            success: Whether operation succeeded
            removed_dir: Path to directory with removed files
            message: Result message
        """
        self._is_operating = False
        self._worker = None
        self._progress_bar.setVisible(False)
        self._update_navigation_buttons()

        if success:
            if removed_dir and removed_dir.exists():
                response = QMessageBox.question(
                    self,
                    tr("page.backup.cleanup_title"),
                    tr("page.backup.cleanup_message", path=str(removed_dir)),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )

                if response == QMessageBox.StandardButton.Yes:
                    try:
                        shutil.rmtree(removed_dir)
                        QMessageBox.information(
                            self,
                            tr("page.backup.success_title"),
                            tr("page.backup.cleanup_success"),
                        )
                    except Exception as e:
                        QMessageBox.warning(
                            self,
                            tr("page.backup.warning_title"),
                            tr("page.backup.cleanup_failed", error=str(e)),
                        )

            QMessageBox.information(self, tr("page.backup.success_title"), message)
        else:
            QMessageBox.critical(self, tr("page.backup.error_title"), message)

    # ========================================
    # UI Updates
    # ========================================

    def _apply_filter(self) -> None:
        """Apply game filter to backup table."""
        self._refresh_backup_table()

    def _update_navigation_buttons(self) -> None:
        """Update navigation button states."""
        can_operate = not self._is_operating
        self._btn_create_backup.setEnabled(can_operate)
        self._filter_combo.setEnabled(can_operate)

        if self._is_operating:
            self._update_action_buttons(False)
        else:
            self._update_action_buttons(self._selected_backup_id is not None)

        self.notify_navigation_changed()

    # ========================================
    # BasePage Implementation
    # ========================================

    def get_page_id(self) -> str:
        """Get unique page identifier."""
        return "backup"

    def get_page_title(self) -> str:
        """Get page title for display."""
        return tr("page.backup.title")

    def get_additional_buttons(self) -> list[QPushButton]:
        """Get additional buttons."""
        return [self._btn_create_backup, self._btn_restore, self._btn_edit, self._btn_delete]

    def can_go_to_next_page(self) -> bool:
        """Check if can proceed to next page."""
        if self._is_operating:
            return False

        # Cannot proceed if any game is modded (needs clean backup)
        if self._modded_games:
            return False

        return True

    def can_go_to_previous_page(self) -> bool:
        """Check if can go to previous page."""
        return not self._is_operating

    def on_page_shown(self) -> None:
        """Called when page becomes visible."""
        super().on_page_shown()
        self._load_backups()

    def on_page_hidden(self) -> None:
        """Called when page becomes hidden."""
        super().on_page_hidden()

        if self._worker and self._worker.isRunning():
            self._worker.wait()

    def retranslate_ui(self) -> None:
        """Update UI text for language change."""
        self._title_label.setText(tr("page.backup.title"))
        self._filter_label.setText(tr("page.backup.filter_label"))
        self._btn_create_backup.setText(tr("page.backup.btn_create_backup"))
        self._btn_restore.setText(tr("page.backup.btn_restore"))
        self._btn_edit.setText(tr("page.backup.btn_edit"))
        self._btn_delete.setText(tr("page.backup.btn_delete"))

        # Update table headers
        self._backup_table.setHorizontalHeaderLabels(
            [
                tr("page.backup.col_backup_name"),
                tr("page.backup.col_game_code"),
                tr("page.backup.col_date"),
                tr("page.backup.col_size"),
                tr("page.backup.col_modded"),
                tr("page.backup.col_status"),
            ]
        )

        self._refresh_backup_table()
        self._update_modded_warning()
