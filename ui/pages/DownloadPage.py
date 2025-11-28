import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal, QThread, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel,
    QMessageBox, QProgressBar, QPushButton, QSplitter,
    QVBoxLayout, QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QScrollArea, QTextEdit
)

from constants import (
    COLOR_STATUS_COMPLETE, COLOR_ERROR, COLOR_STATUS_NONE,
    COLOR_STATUS_PARTIAL, COLOR_WARNING, MARGIN_SMALL, MARGIN_STANDARD,
    COLOR_BACKGROUND_SECONDARY, COLOR_TEXT, SPACING_SMALL, SPACING_LARGE
)
from core.DownloadManager import (
    ArchiveInfo, ArchiveStatus, ArchiveVerifier, DownloadManager,
    DownloadProgress, HashAlgorithm
)
from core.File import format_size
from core.StateManager import StateManager
from core.TranslationManager import tr
from ui.pages.BasePage import BasePage

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

# Table columns
COL_MOD_NAME = 0
COL_FILENAME = 1
COL_STATUS = 2
COLUMN_COUNT = 3


# ============================================================================
# Verification Worker Thread
# ============================================================================

class VerificationWorker(QThread):
    """Worker thread for verifying archives without blocking UI."""

    verification_started = Signal(str)  # mod_id
    verification_completed = Signal(str, object)  # mod_id, ArchiveStatus
    all_completed = Signal(dict)  # {mod_id: status}
    error_occurred = Signal(str, str)  # mod_id, error_message

    def __init__(self, archives: dict, download_path: Path, verifier: ArchiveVerifier):
        super().__init__()
        self.archives = archives
        self.download_path = download_path
        self.verifier = verifier
        self._is_cancelled = False
        self.results = {}

    def run(self):
        """Run verification process."""
        try:
            for mod_id, archive_info in self.archives.items():
                if self._is_cancelled:
                    break

                try:
                    self.verification_started.emit(mod_id)

                    status = self._verify_archive_status(archive_info)
                    self.results[mod_id] = status

                    self.verification_completed.emit(mod_id, status)
                except Exception as e:
                    logger.error(f"Error verifying {mod_id}: {e}")
                    self.error_occurred.emit(mod_id, str(e))
                    self.results[mod_id] = ArchiveStatus.ERROR

            self.all_completed.emit(self.results)
        except Exception as e:
            logger.error(f"Critical error in verification thread: {e}")
            self.all_completed.emit(self.results)

    def _verify_archive_status(self, archive_info: ArchiveInfo) -> ArchiveStatus:
        """Verify status of an archive."""
        if archive_info.requires_manual_download:
            return ArchiveStatus.MANUAL

        file_path = self.download_path / archive_info.filename

        return self.verifier.verify_archive(
            file_path,
            archive_info
        )

    def cancel(self):
        """Cancel verification process."""
        self._is_cancelled = True


# ============================================================================
# Progress Item Widget
# ============================================================================

class ProgressItemWidget(QWidget):
    """Widget displaying an active download or verification with progress."""

    def __init__(self, item_name: str, parent=None):
        super().__init__(parent)
        self.item_name = item_name
        self._create_widgets()

    def _create_widgets(self) -> None:
        """Create widget layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(0, 0, 0, 0)

        # Filename
        self._lbl_filename = QLabel(self.item_name)
        self._lbl_filename.setStyleSheet("font-weight: bold;")
        self._lbl_filename.setWordWrap(True)
        layout.addWidget(self._lbl_filename)

        # Progress bar
        hlayout = QHBoxLayout(self)
        hlayout.setSpacing(SPACING_SMALL)
        hlayout.setContentsMargins(0, 0, 0, 0)

        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(100)
        self._progress_bar.setValue(0)
        hlayout.addWidget(self._progress_bar)

        self._btn_cancel = QPushButton("X")
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.setStyleSheet(f"padding: 2px 10px;min-height: 15px;")
        hlayout.addWidget(self._btn_cancel)

        layout.addLayout(hlayout)

        # Stats and controls
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(SPACING_SMALL)

        self._lbl_stats = QLabel()
        self._lbl_stats.setStyleSheet(f"color: {COLOR_STATUS_NONE};")
        bottom_layout.addWidget(self._lbl_stats, stretch=1)

        layout.addLayout(bottom_layout)
        self.retranslate_ui()

    def update_progress(self, progress: DownloadProgress) -> None:
        """Update widget with download progress data."""
        self._progress_bar.setValue(int(progress.progress_percent))

        if progress.has_error:
            stats = tr("page.download.error_status", error=progress.error_message)
            self._lbl_stats.setStyleSheet(f"color: {COLOR_ERROR};")
        else:
            speed = self._format_speed(progress.speed_bps)
            time_left = self._format_time(progress.time_remaining_seconds)
            downloaded = format_size(progress.bytes_received)
            total = format_size(progress.bytes_total)

            stats = tr("page.download.stats", downloaded=downloaded, total=total,
                       speed=speed, time=time_left)
            self._lbl_stats.setStyleSheet("")

        self._lbl_stats.setText(stats)

    def set_verification_status(self, status: str):
        """Update widget with verification status."""
        self._lbl_stats.setText(status)
        self._progress_bar.setMaximum(0)

    def _format_speed(self, speed_bps: float) -> str:
        """Format download speed for display."""
        if speed_bps <= 0:
            return "-- MB/s"
        return f"{format_size(int(speed_bps))}/s"

    def _format_time(self, seconds: float) -> str:
        """Format time duration for display."""
        if seconds <= 0:
            return "--"

        minutes, secs = divmod(int(seconds), 60)
        hours, minutes = divmod(minutes, 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"

    def retranslate_ui(self) -> None:
        """Update UI text for language change."""
        self._btn_cancel.setToolTip(tr("page.download.btn_cancel"))


# ============================================================================
# Archive Details Panel
# ============================================================================

class ArchiveDetailsPanel(QFrame):
    """Panel showing detailed information about selected archive."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._create_widgets()
        self.clear()

    def _create_widgets(self) -> None:
        """Create widget layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)

        # Details text
        self._details_text = QTextEdit()
        self._details_text.setReadOnly(True)
        self._details_text.setMaximumHeight(160)
        layout.addWidget(self._details_text)

        self._link_label = QLabel()
        self._link_label.setOpenExternalLinks(False)
        self._link_label.linkActivated.connect(lambda u: QDesktopServices.openUrl(QUrl(u)))
        layout.addWidget(self._link_label)

        layout.addStretch()

    def set_archive_info(
            self,
            mod_name: str,
            filename: str,
            file_size: int | None,
            expected_hash: str | None,
            url: str | None,
            status: ArchiveStatus
    ) -> None:
        """Update panel with archive information.

        Args:
            mod_name: Name of the mod
            filename: Archive filename
            file_size: File size in bytes
            expected_hash: Expected hash value
            url: Download URL
            status: Current archive status
        """

        details = []

        details.append(f"<b>{tr('page.download.details.mod')}:</b> {mod_name}")
        details.append(f"<b>{tr('page.download.details.filename')}:</b> {filename}")

        if file_size:
            size_formatted = format_size(file_size)
            details.append(f"<b>{tr('page.download.details.size')}:</b> {size_formatted}")

        if expected_hash:
            details.append(f"<b>{tr('page.download.details.hash')}:</b> <code>{expected_hash}</code>")

        status_text = tr(f"page.download.status.{status.value}")
        details.append(f"<b>{tr('page.download.details.status')}:</b> {status_text}")

        self._details_text.setHtml("<br>".join(details))

        if url:
            link_text = tr("widget.mod_details.link.download")
            self._link_label.setText(f'📦 <a href="{url}" style="color: {COLOR_TEXT};">{link_text}</a>')
            self._link_label.setToolTip(url)
            self._link_label.setVisible(True)

    def clear(self) -> None:
        """Clear panel content."""
        self._details_text.setHtml(
            f"<i>{tr('page.download.details.empty_message')}</i>"
        )
        self._link_label.setVisible(False)


# ============================================================================
# Sortable Table Widget with Row Hover
# ============================================================================

class ArchiveTableWidget(QTableWidget):
    """Custom table widget with row hover highlighting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_row = -1
        self.setMouseTracking(True)
        self.setSortingEnabled(True)
        self._setup_hover_style()

    def _setup_hover_style(self) -> None:
        """Setup hover highlighting style."""
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def mouseMoveEvent(self, event) -> None:
        """Handle mouse move for row hover effect."""
        row = self.rowAt(event.pos().y())

        if row != self._hover_row:
            # Clear previous hover
            if self._hover_row >= 0:
                self._clear_row_hover(self._hover_row)

            # Set new hover
            self._hover_row = row
            if self._hover_row >= 0:
                self._set_row_hover(self._hover_row)

        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        """Clear hover when mouse leaves table."""
        if self._hover_row >= 0:
            self._clear_row_hover(self._hover_row)
            self._hover_row = -1
        super().leaveEvent(event)

    def _set_row_hover(self, row: int) -> None:
        """Apply hover style to row."""
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                # Store original background
                if not item.data(Qt.ItemDataRole.UserRole + 1):
                    original_bg = item.background()
                    item.setData(Qt.ItemDataRole.UserRole + 1, original_bg)

                item.setBackground(QColor(COLOR_BACKGROUND_SECONDARY))

    def _clear_row_hover(self, row: int) -> None:
        """Clear hover style from row."""
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                # Restore original background
                original_bg = item.data(Qt.ItemDataRole.UserRole + 1)
                if original_bg:
                    item.setBackground(original_bg)
                    item.setData(Qt.ItemDataRole.UserRole + 1, None)


# ============================================================================
# Download Page
# ============================================================================

class DownloadPage(BasePage):
    """Page for managing mod archive downloads."""

    STATUS_COLORS = {
        ArchiveStatus.VALID: QColor(COLOR_STATUS_COMPLETE),
        ArchiveStatus.INVALID_HASH: QColor(COLOR_ERROR),
        ArchiveStatus.INVALID_SIZE: QColor(COLOR_ERROR),
        ArchiveStatus.MISSING: QColor(COLOR_STATUS_NONE),
        ArchiveStatus.UNKNOWN: QColor(COLOR_STATUS_NONE),
        ArchiveStatus.MANUAL: QColor(COLOR_STATUS_PARTIAL),
        ArchiveStatus.DOWNLOADING: QColor(COLOR_WARNING),
        ArchiveStatus.VERIFYING: QColor(COLOR_WARNING),
        ArchiveStatus.ERROR: QColor(COLOR_ERROR),
    }

    def __init__(self, state_manager: StateManager):
        super().__init__(state_manager)

        self._mod_manager = self.state_manager.get_mod_manager()
        self._download_path = Path(self.state_manager.get_download_folder())

        # Core components
        self._verifier = ArchiveVerifier()
        self._download_manager = DownloadManager(self._download_path)

        # Archive tracking
        self._archives: dict[str, ArchiveInfo] = {}
        self._archive_status: dict[str, ArchiveStatus] = {}
        self._verified_archives: set[str] = set()

        # Operation tracking
        self._is_verifying = False
        self._is_downloading = False
        self._verification_worker: VerificationWorker | None = None
        self._progress_widgets: dict[str, ProgressItemWidget] = {}

        # UI components
        self._archive_table: ArchiveTableWidget | None = None
        self._filter_combo: QComboBox | None = None
        self._progress_container: QWidget | None = None
        self._progress_layout: QVBoxLayout | None = None
        self._details_panel: ArchiveDetailsPanel | None = None
        self._btn_download_all: QPushButton | None = None
        self._btn_open_folder: QPushButton | None = None

        self._connect_signals()
        self._create_widgets()

        # Update timer for download progress
        self._update_timer = QTimer()
        self._update_timer.setInterval(100)
        self._update_timer.timeout.connect(self._update_active_downloads)

        logger.info("DownloadPage initialized")

    def _create_widgets(self) -> None:
        """Create page UI layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._create_main_splitter(), stretch=1)
        layout.addWidget(self._create_action_buttons())

    def _create_main_splitter(self) -> QWidget:
        """Create main splitter with table and operations panels."""
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._create_archive_table_panel())
        splitter.addWidget(self._create_right_panel())
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 2)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        return splitter

    def _create_archive_table_panel(self) -> QWidget:
        """Create archive table panel with filter."""
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setMinimumWidth(600)

        layout = QVBoxLayout(panel)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(
            MARGIN_STANDARD,
            MARGIN_STANDARD,
            MARGIN_SMALL,
            0
        )

        # Header with filter
        header_layout = QHBoxLayout()
        header_layout.setSpacing(SPACING_SMALL)
        header_layout.setContentsMargins(0, 0, 0, 0)

        self._archives_title = self._create_section_title()
        header_layout.addWidget(self._archives_title)
        header_layout.addStretch()

        filter_label = QLabel(tr("page.download.filter_label"))
        header_layout.addWidget(filter_label)

        self._filter_combo = QComboBox()
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)
        header_layout.addWidget(self._filter_combo)

        layout.addLayout(header_layout)

        # Archive table with custom hover
        self._archive_table = ArchiveTableWidget()
        self._archive_table.setColumnCount(COLUMN_COUNT)
        self._archive_table.setHorizontalHeaderLabels([
            tr("page.download.col_mod_name"),
            tr("page.download.col_filename"),
            tr("page.download.col_status")
        ])

        self._archive_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._archive_table.verticalHeader().setVisible(False)

        # Column resize modes
        header = self._archive_table.horizontalHeader()
        header.setSectionResizeMode(COL_MOD_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_FILENAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)

        # Make columns sortable
        header.setSortIndicatorShown(True)
        header.setSectionsClickable(True)

        # Status column not sortable
        self._archive_table.horizontalHeaderItem(COL_STATUS).setData(
            Qt.ItemDataRole.UserRole,
            False  # Not sortable
        )

        # Connect selection change
        self._archive_table.itemSelectionChanged.connect(self._on_selection_changed)
        self._archive_table.itemDoubleClicked.connect(self._on_archive_double_click)

        layout.addWidget(self._archive_table)

        return panel

    def _create_right_panel(self) -> QWidget:
        """Create right panel with operations and details."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(
            MARGIN_SMALL,
            MARGIN_STANDARD,
            MARGIN_STANDARD,
            MARGIN_SMALL
        )

        self._downloads_title = self._create_section_title()
        layout.addWidget(self._downloads_title)

        # Scrollable container for progress widgets
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        self._progress_container = QWidget()
        self._progress_layout = QVBoxLayout(self._progress_container)
        self._progress_layout.setSpacing(SPACING_LARGE)
        self._progress_layout.setContentsMargins(0, 0, 10, 0)
        self._progress_layout.addStretch()

        scroll.setWidget(self._progress_container)
        layout.addWidget(scroll, stretch=3)

        # Title
        self._details_title = self._create_section_title()
        layout.addWidget(self._details_title)

        # Details panel
        self._details_panel = ArchiveDetailsPanel()
        layout.addWidget(self._details_panel)

        return panel

    def _create_action_buttons(self) -> QWidget:
        """Create action buttons bar."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(
            MARGIN_STANDARD, MARGIN_STANDARD,
            MARGIN_STANDARD, MARGIN_STANDARD
        )
        layout.setSpacing(SPACING_SMALL)

        self._btn_download_all = QPushButton()
        self._btn_download_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_download_all.clicked.connect(self._download_all_missing)
        layout.addWidget(self._btn_download_all)

        self._btn_open_folder = QPushButton()
        self._btn_open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open_folder.clicked.connect(self._open_download_folder)
        layout.addWidget(self._btn_open_folder)

        layout.addStretch()

        return container

    def _connect_signals(self) -> None:
        """Connect download manager signals."""
        self._download_manager.download_started.connect(self._on_download_started)
        self._download_manager.download_progress.connect(self._on_download_progress)
        self._download_manager.download_finished.connect(self._on_download_finished)
        self._download_manager.download_canceled.connect(self._on_download_canceled)
        self._download_manager.download_error.connect(self._on_download_error)

    # ========================================
    # Archive Loading
    # ========================================

    def _load_archives(self) -> None:
        """Load archive information from selected mods."""
        self._archives.clear()

        selected = self.state_manager.get_selected_components()
        if not selected:
            logger.warning("No components selected")
            return

        unique_mods = set(selected.keys())

        for mod_id in unique_mods:
            mod = self._mod_manager.get_mod_by_id(mod_id)
            if not mod:
                continue

            archive_info = self._get_archive_info_from_mod(mod)
            if archive_info:
                self._archives[mod_id] = archive_info

                # Set initial status if not already verified
                if mod_id not in self._verified_archives:
                    self._archive_status[mod_id] = ArchiveStatus.UNKNOWN

        self._refresh_archive_table()
        self._update_navigation_buttons()
        logger.info(f"Archives loaded: {len(self._archives)}")

    def _get_archive_info_from_mod(self, mod) -> ArchiveInfo | None:
        """Extract archive information from mod object."""
        if not hasattr(mod, 'download'):
            return None

        download_info = mod.download

        return ArchiveInfo(
            mod_id=mod.id,
            filename=mod.file.filename if mod.file else None,
            url=download_info,
            expected_hash=mod.file.sha256 if mod.file else None,
            hash_algorithm=HashAlgorithm.SHA256,
            file_size=mod.file.size if mod.file else None
        )

    def _refresh_archive_table(self) -> None:
        """Refresh the archive table display."""
        if not self._archive_table:
            return

        # Disable sorting during update
        self._archive_table.setSortingEnabled(False)
        self._archive_table.setRowCount(0)

        filter_status = self._filter_combo.currentData() if self._filter_combo else None

        row = 0
        for mod_id, archive_info in self._archives.items():
            status = self._archive_status.get(mod_id, ArchiveStatus.MISSING)

            if filter_status and status != filter_status:
                continue

            self._archive_table.insertRow(row)

            mod = self._mod_manager.get_mod_by_id(mod_id)
            mod_name = mod.name if mod else mod_id

            # Column 0: Mod name (sortable)
            name_item = QTableWidgetItem(mod_name)
            name_item.setData(Qt.ItemDataRole.UserRole, mod_id)
            self._archive_table.setItem(row, COL_MOD_NAME, name_item)

            # Column 1: Filename (sortable)
            filename_item = QTableWidgetItem(archive_info.filename)
            self._archive_table.setItem(row, COL_FILENAME, filename_item)

            # Column 2: Status (not sortable, displayed with color)
            status_text = tr(f"page.download.status.{status.value}")
            status_item = QTableWidgetItem(status_text)
            color = self.STATUS_COLORS.get(status, QColor("#000000"))
            status_item.setForeground(color)
            self._archive_table.setItem(row, COL_STATUS, status_item)

            row += 1

        self._archive_table.setSortingEnabled(True)

    # ========================================
    # Selection Management
    # ========================================

    def _on_selection_changed(self) -> None:
        """Handle table selection change to update details panel."""
        selected_items = self._archive_table.selectedItems()

        if not selected_items:
            self._details_panel.clear()
            return

        # Get mod_id from first column
        row = selected_items[0].row()
        mod_id_item = self._archive_table.item(row, COL_MOD_NAME)

        if not mod_id_item:
            self._details_panel.clear()
            return

        mod_id = mod_id_item.data(Qt.ItemDataRole.UserRole)
        archive_info = self._archives.get(mod_id)

        if not archive_info:
            self._details_panel.clear()
            return

        # Get mod details
        mod = self._mod_manager.get_mod_by_id(mod_id)
        mod_name = mod.name if mod else mod_id
        status = self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN)

        # Update details panel
        self._details_panel.set_archive_info(
            mod_name=mod_name,
            filename=archive_info.filename,
            file_size=archive_info.file_size,
            expected_hash=archive_info.expected_hash,
            url=archive_info.url,
            status=status
        )

    # ========================================
    # Verification Management
    # ========================================

    def _on_verification_started(self, mod_id: str) -> None:
        """Handle verification start for a mod."""
        self._archive_status[mod_id] = ArchiveStatus.VERIFYING
        self._refresh_archive_table()
        logger.debug(f"Started verification for {mod_id}")

    def _on_verification_completed(self, mod_id: str, status: ArchiveStatus) -> None:
        """Handle verification completion for a mod."""
        self._archive_status[mod_id] = status
        self._verified_archives.add(mod_id)
        self._refresh_archive_table()
        logger.debug(f"Completed verification for {mod_id}: {status}")

    def _on_verification_error(self, mod_id: str, error_message: str) -> None:
        """Handle verification error."""
        logger.error(f"Verification error for {mod_id}: {error_message}")
        self._archive_status[mod_id] = ArchiveStatus.ERROR
        self._refresh_archive_table()

    # ========================================
    # Download Management
    # ========================================

    def _download_all_missing(self) -> None:
        """Start downloading all missing archives (with pre-verification)."""
        if self._is_verifying or self._is_downloading:
            QMessageBox.warning(
                self,
                tr("page.download.operation_in_progress_title"),
                tr("page.download.operation_in_progress_message")
            )
            return

        # Get non-valid, non-manual archives
        to_check = {
            mod_id: archive_info
            for mod_id, archive_info in self._archives.items()
            if (self._archive_status.get(mod_id, ArchiveStatus.MISSING) != ArchiveStatus.VALID
                and not archive_info.requires_manual_download
                and mod_id not in self._verified_archives)
        }

        if not to_check:
            # All verified, start downloads
            self._start_downloads()
            return

        # Verify first, then download
        self._is_verifying = True
        self._update_navigation_buttons()

        self._verification_worker = VerificationWorker(
            to_check,
            self._download_path,
            self._verifier
        )

        self._verification_worker.verification_started.connect(
            self._on_verification_started
        )
        self._verification_worker.verification_completed.connect(
            self._on_verification_completed
        )
        self._verification_worker.all_completed.connect(
            self._on_verification_completed_then_download
        )
        self._verification_worker.error_occurred.connect(
            self._on_verification_error
        )

        self._verification_worker.start()

    def _on_verification_completed_then_download(self, results: dict) -> None:
        """Handle verification completion, then start downloads."""
        self._is_verifying = False
        self._verification_worker = None

        logger.info("Verification completed, starting downloads")
        self._start_downloads()

    def _start_downloads(self) -> None:
        """Start downloading all archives that need downloading."""
        to_download = [
            archive_info
            for mod_id, archive_info in self._archives.items()
            if self._archive_status.get(mod_id, ArchiveStatus.MISSING).needs_download
        ]

        if not to_download:
            self._update_navigation_buttons()
            QMessageBox.information(
                self,
                tr("page.download.no_downloads_title"),
                tr("page.download.no_downloads_message")
            )
            return

        self._is_downloading = True
        self._update_navigation_buttons()

        for archive_info in to_download:
            self._download_manager.add_to_queue(archive_info)

        logger.info(f"Downloads queued: {len(to_download)}")

        if not self._update_timer.isActive():
            self._update_timer.start()

    def _on_download_started(self, mod_id: str) -> None:
        """Handle download start."""
        self._archive_status[mod_id] = ArchiveStatus.DOWNLOADING
        self._refresh_archive_table()

        # Create download widget
        progress = next(
            (p for p in self._download_manager.get_active_downloads()
             if p.archive_info.mod_id == mod_id),
            None
        )

        if progress:
            widget = ProgressItemWidget(progress.archive_info.filename)
            widget._btn_cancel.clicked.connect(
                lambda: self._cancel_download(mod_id)
            )

            self._progress_widgets[mod_id] = widget
            self._progress_layout.insertWidget(
                self._progress_layout.count() - 1,
                widget
            )

    def _on_download_progress(self, mod_id: str, progress: DownloadProgress) -> None:
        """Handle download progress update."""
        pass  # Handled by timer

    def _on_download_canceled(self, mod_id: str, file_path: str) -> None:
        """Handle download cancelation."""
        logger.info(f"Download canceled: {mod_id}")
        self._update_after_download(mod_id)

    def _on_download_finished(self, mod_id: str) -> None:
        """Handle download completion."""
        logger.info(f"Download completed: {mod_id}")
        self._update_after_download(mod_id)

    def _update_after_download(self, mod_id: str):
        # Remove widget
        if mod_id in self._progress_widgets:
            widget = self._progress_widgets[mod_id]
            self._progress_layout.removeWidget(widget)
            widget.deleteLater()
            del self._progress_widgets[mod_id]

        # Verify downloaded file
        if mod_id in self._archives:
            archive_info = self._archives[mod_id]
            file_path = self._download_path / archive_info.filename
            status = self._verifier.verify_archive(
                file_path,
                archive_info
            )

            self._archive_status[mod_id] = status
            self._verified_archives.add(mod_id)

        self._refresh_archive_table()

        # Check if all downloads finished
        if not self._progress_widgets and not self._download_manager.get_queue_size():
            self._update_timer.stop()
            self._is_downloading = False
            self._update_navigation_buttons()

    def _on_download_error(self, mod_id: str, error_message: str) -> None:
        """Handle download error."""
        logger.error(f"Download error for {mod_id}: {error_message}")

        if mod_id in self._progress_widgets:
            widget = self._progress_widgets[mod_id]
            self._progress_layout.removeWidget(widget)
            widget.deleteLater()
            del self._progress_widgets[mod_id]

        self._archive_status[mod_id] = ArchiveStatus.ERROR
        self._refresh_archive_table()

        if not self._progress_widgets and not self._download_manager.get_queue_size():
            self._update_timer.stop()
            self._is_downloading = False
            self._update_navigation_buttons()

    def _update_active_downloads(self) -> None:
        """Update all active download widgets."""
        for progress in self._download_manager.get_active_downloads():
            mod_id = progress.archive_info.mod_id
            if mod_id in self._progress_widgets:
                self._progress_widgets[mod_id].update_progress(progress)

    def _cancel_download(self, mod_id: str) -> None:
        """Cancel an active download."""
        self._download_manager.cancel_download(mod_id)
        self._archive_status[mod_id] = ArchiveStatus.MISSING

    # ========================================
    # UI Actions
    # ========================================

    def _open_download_folder(self) -> None:
        """Open download folder in file manager."""
        import subprocess
        import platform

        try:
            if platform.system() == "Windows":
                subprocess.run(["explorer", str(self._download_path)])
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(self._download_path)])
            else:
                subprocess.run(["xdg-open", str(self._download_path)])
        except Exception as e:
            logger.error(f"Error opening folder: {e}")
            QMessageBox.warning(
                self,
                tr("page.download.open_folder_error_title"),
                tr("page.download.open_folder_error_message", error=str(e))
            )

    def _on_archive_double_click(self, item: QTableWidgetItem) -> None:
        """Handle double-click on archive to start download."""
        row = item.row()
        mod_id_item = self._archive_table.item(row, COL_MOD_NAME)

        if not mod_id_item:
            return

        mod_id = mod_id_item.data(Qt.ItemDataRole.UserRole)
        if not mod_id or mod_id not in self._archives:
            return

        archive_info = self._archives[mod_id]
        status = self._archive_status.get(mod_id)

        if status and status.needs_download and not archive_info.requires_manual_download:
            self._download_manager.start_download(archive_info)

            if not self._update_timer.isActive():
                self._update_timer.start()

    def _apply_filter(self) -> None:
        """Apply status filter to archive table."""
        self._refresh_archive_table()

    def _update_navigation_buttons(self) -> None:
        """Update navigation button states."""
        can_navigate = not (self._is_verifying or self._is_downloading)
        self._btn_download_all.setEnabled(can_navigate)
        self.notify_navigation_changed()

    # ========================================
    # BasePage Implementation
    # ========================================

    def get_page_id(self) -> str:
        return "download"

    def get_page_title(self) -> str:
        return tr("page.download.title")

    def can_proceed(self) -> bool:
        """Check if can proceed to next page."""
        if self._is_verifying or self._is_downloading:
            return False

        return all(status.is_available for _, status in self._archive_status.items())

    def on_page_shown(self) -> None:
        """Called when page becomes visible."""
        super().on_page_shown()
        self._verified_archives.clear()
        self._download_path = Path(self.state_manager.get_download_folder())
        self._download_manager.set_download_path(self._download_path)
        # TODO: Si le dossier de téléchargement est le même, garder le statut déjà calculé des archives qui n'ont pas changé
        self._load_archives()

    def on_page_hidden(self) -> None:
        """Called when page becomes hidden."""
        super().on_page_hidden()

        if self._update_timer.isActive():
            self._update_timer.stop()

    def retranslate_ui(self) -> None:
        """Update UI text for language change."""
        # Update buttons
        self._btn_download_all.setText(tr("page.download.btn_download_all"))
        self._btn_open_folder.setText(tr("page.download.btn_open_folder"))

        self._archives_title.setText(tr("page.download.archives_title"))
        self._downloads_title.setText(tr("page.download.downloads_title"))
        self._details_title.setText(tr("page.download.details.title"))

        # Update filter combo
        current = self._filter_combo.currentData()
        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()
        self._filter_combo.addItem(tr("page.download.filter.all"), None)
        self._filter_combo.addItem(tr("page.download.filter.missing"), ArchiveStatus.MISSING)
        self._filter_combo.addItem(tr("page.download.filter.invalid"), ArchiveStatus.INVALID_HASH)
        self._filter_combo.addItem(tr("page.download.filter.manual"), ArchiveStatus.MANUAL)
        self._filter_combo.addItem(tr("page.download.filter.valid"), ArchiveStatus.VALID)
        self._filter_combo.addItem(tr("page.download.filter.unknown"), ArchiveStatus.UNKNOWN)

        for i in range(self._filter_combo.count()):
            if self._filter_combo.itemData(i) == current:
                self._filter_combo.setCurrentIndex(i)
                break
        self._filter_combo.blockSignals(False)

        # Update table headers
        self._archive_table.setHorizontalHeaderLabels([
            tr("page.download.col_mod_name"),
            tr("page.download.col_filename"),
            tr("page.download.col_status")
        ])

        self._refresh_archive_table()

        for widget in self._progress_widgets.values():
            widget.retranslate_ui()
