import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCursor, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from constants import (
    COLOR_ERROR,
    COLOR_STATUS_COMPLETE,
    COLOR_STATUS_NONE,
    COLOR_TEXT,
    COLOR_WARNING,
    MARGIN_SMALL,
    MARGIN_STANDARD,
    SPACING_LARGE,
    SPACING_SMALL,
)
from core.DownloadManager import (
    ArchiveInfo,
    ArchiveStatus,
    ArchiveVerifier,
    DownloadManager,
    DownloadProgress,
    HashAlgorithm,
)
from core.File import format_size
from core.StateManager import StateManager
from core.TranslationManager import tr
from ui.pages.BasePage import BasePage
from ui.widgets.HoverTableWidget import HoverTableWidget

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
        file_path = self.download_path / archive_info.filename

        return self.verifier.verify_archive(file_path, archive_info)

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
        self._btn_cancel.setStyleSheet("padding: 2px 10px;min-height: 15px;")
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

            stats = tr(
                "page.download.stats",
                downloaded=downloaded,
                total=total,
                speed=speed,
                time=time_left,
            )
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

        self._links_container = QWidget()
        self._links_layout = QHBoxLayout(self._links_container)
        self._links_layout.setContentsMargins(8, 0, 0, 0)
        self._links_layout.setSpacing(SPACING_SMALL)
        layout.addWidget(self._links_container)

        layout.addStretch()

    def set_archive_info(
        self,
        mod_name: str,
        filename: str,
        file_size: int | None,
        expected_hash: str | None,
        url: str | None,
        homepage: str | None,
        status: ArchiveStatus,
    ) -> None:
        """Update panel with archive information.

        Args:
            mod_name: Name of the mod
            filename: Archive filename
            file_size: File size in bytes
            expected_hash: Expected hash value
            url: Download URL
            homepage: Homepage URL
            status: Current archive status
        """

        details = [
            f"<b>{tr('page.download.details.mod')}:</b> {mod_name}",
            f"<b>{tr('page.download.details.filename')}:</b> {filename}",
        ]

        if file_size:
            size_formatted = format_size(file_size)
            details.append(f"<b>{tr('page.download.details.size')}:</b> {size_formatted}")

        if expected_hash:
            details.append(
                f"<b>{tr('page.download.details.hash')}:</b> <code>{expected_hash}</code>"
            )

        status_text = tr(f"page.download.status.{status.value}")
        details.append(f"<b>{tr('page.download.details.status')}:</b> {status_text}")

        self._details_text.setHtml("<br>".join(details))
        self._update_links(url, homepage)

    def _update_links(self, url_download: str | None, url_homepage: str | None) -> None:
        """Populate the links section dynamically."""
        self._clear_layout(self._links_layout)

        links = {
            url_homepage: ("🏠", tr("widget.mod_details.link.homepage")),
            url_download: ("📦", tr("widget.mod_details.link.download")),
        }

        for url, (icon, label_text) in links.items():
            if url:
                self._links_layout.addWidget(self._create_link_label(icon, label_text, url))
        self._links_layout.addStretch()

    @staticmethod
    def _create_link_label(icon: str, text: str, url: str) -> QLabel:
        """Create a clickable link label."""
        label = QLabel(f'{icon} <a href="{url}" style="color: {COLOR_TEXT};">{text}</a>')
        label.setOpenExternalLinks(False)
        label.linkActivated.connect(lambda u: QDesktopServices.openUrl(QUrl(u)))
        label.setToolTip(url)
        return label

    def clear(self) -> None:
        """Clear panel content."""
        self._details_text.setHtml(f"<i>{tr('page.download.details.empty_message')}</i>")

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()


# ============================================================================
# Download Page
# ============================================================================


class DownloadPage(BasePage):
    """Page for managing mod archive downloads."""

    STATUS_COLORS = {
        ArchiveStatus.VALID: QColor(COLOR_STATUS_COMPLETE),
        ArchiveStatus.INVALID_HASH: QColor(COLOR_WARNING),
        ArchiveStatus.INVALID_SIZE: QColor(COLOR_WARNING),
        ArchiveStatus.MISSING: QColor(COLOR_STATUS_NONE),
        ArchiveStatus.UNKNOWN: QColor(COLOR_STATUS_NONE),
        ArchiveStatus.DOWNLOADING: QColor(COLOR_WARNING),
        ArchiveStatus.VERIFYING: QColor(COLOR_WARNING),
        ArchiveStatus.ERROR: QColor(COLOR_ERROR),
    }

    def __init__(self, state_manager: StateManager):
        super().__init__(state_manager)

        self._mod_manager = self.state_manager.get_mod_manager()
        self._download_path: Path | None = None

        # Core components
        self._verifier = ArchiveVerifier()
        self._download_manager = DownloadManager()

        # Archive tracking
        self._archives: dict[str, ArchiveInfo] = {}
        self._archive_status: dict[str, ArchiveStatus] = {}
        self._archive_cache_keys: dict[str, str] = {}
        self._cached_download_path: Path | None = None

        # Operation tracking
        self._is_verifying = False
        self._is_downloading = False
        self._verification_worker: VerificationWorker | None = None
        self._progress_widgets: dict[str, ProgressItemWidget] = {}

        # UI components
        self._archive_table: HoverTableWidget | None = None
        self._filter_combo: QComboBox | None = None
        self._progress_container: QWidget | None = None
        self._progress_layout: QVBoxLayout | None = None
        self._details_panel: ArchiveDetailsPanel | None = None
        self._btn_download_all: QPushButton | None = None
        self._btn_open_folder: QPushButton | None = None
        self._chk_ignore_warnings: QCheckBox | None = None
        self._chk_ignore_errors: QCheckBox | None = None

        self._connect_signals()
        self._create_widgets()
        self._create_additional_buttons()

        # Update timer for download progress
        self._update_timer = QTimer()
        self._update_timer.setInterval(100)
        self._update_timer.timeout.connect(self._update_active_downloads)

        logger.info("DownloadPage initialized")

    def _create_widgets(self) -> None:
        """Create page UI layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_LARGE)
        layout.setContentsMargins(
            MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD
        )

        layout.addWidget(self._create_main_splitter(), stretch=1)

        self._global_progress = QProgressBar()
        layout.addWidget(self._global_progress)

    def _create_main_splitter(self) -> QSplitter:
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
        layout.setContentsMargins(0, 0, 10, 0)

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
        self._archive_table = HoverTableWidget()
        self._archive_table.setColumnCount(COLUMN_COUNT)
        self._archive_table.setHorizontalHeaderLabels(
            [
                tr("page.download.col_mod_name"),
                tr("page.download.col_filename"),
                tr("page.download.col_status"),
            ]
        )

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
            False,  # Not sortable
        )

        self._archive_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._archive_table.customContextMenuRequested.connect(self._show_context_menu)
        self._archive_table.itemSelectionChanged.connect(self._on_selection_changed)
        self._archive_table.itemDoubleClicked.connect(self._on_archive_double_click)

        layout.addWidget(self._archive_table)

        return panel

    def _create_right_panel(self) -> QWidget:
        """Create right panel with operations and details."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(MARGIN_SMALL, 0, 0, 0)

        checkbox_widget = self._create_checkboxs_widget()
        checkbox_widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        layout.addWidget(checkbox_widget)

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

    def _create_checkboxs_widget(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_SMALL)
        layout.addStretch()

        self._chk_ignore_warnings = QCheckBox()
        self._chk_ignore_warnings.stateChanged.connect(self._on_ignore_settings_changed)
        layout.addWidget(self._chk_ignore_warnings)

        self._chk_ignore_errors = QCheckBox()
        self._chk_ignore_errors.stateChanged.connect(self._on_ignore_settings_changed)
        layout.addWidget(self._chk_ignore_errors)

        return container

    def _create_additional_buttons(self):
        """Create action buttons bar."""
        self._btn_download_all = QPushButton()
        self._btn_download_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_download_all.clicked.connect(self._download_all_missing)

        self._btn_open_folder = QPushButton()
        self._btn_open_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open_folder.clicked.connect(self._open_download_folder)

    def _connect_signals(self) -> None:
        """Connect download manager signals."""
        self._download_manager.download_started.connect(self._on_download_started)
        self._download_manager.download_progress.connect(self._on_download_progress)
        self._download_manager.download_finished.connect(self._on_download_finished)
        self._download_manager.download_canceled.connect(self._on_download_canceled)
        self._download_manager.download_error.connect(self._on_download_error)
        self._download_manager.all_completed.connect(self._on_all_downloads_completed)

    # ========================================
    # Archive Loading
    # ========================================

    def _load_archives(self) -> None:
        """Load archive information from selected mods with cache preservation."""
        self._archives.clear()

        selected = self.state_manager.get_selected_components()
        if not selected:
            logger.warning("No components selected")
            return

        unique_mods = {reference.partition(":")[0] for reference in selected if reference}

        # Invalidate cache for removed mods
        self._invalidate_removed_archives(unique_mods)

        for mod_id in unique_mods:
            mod = self._mod_manager.get_mod_by_id(mod_id)
            if not mod:
                continue

            archive_info = self._get_archive_info_from_mod(mod)
            if archive_info and archive_info.filename:
                self._archives[mod_id] = archive_info

                # Only set UNKNOWN status if revalidation needed
                if self._should_revalidate_archive(mod_id, archive_info):
                    self._archive_status[mod_id] = ArchiveStatus.UNKNOWN
                    logger.debug("Archive needs revalidation: %s", mod_id)
                else:
                    logger.debug(
                        "Using cached status for %s: %s", mod_id, self._archive_status[mod_id]
                    )

                # Update cache key
                self._update_archive_cache(mod_id, archive_info)

        self._refresh_archive_table()
        self._archive_table.sortItems(COL_MOD_NAME, Qt.SortOrder.AscendingOrder)
        self._update_navigation_buttons()

        # Count verified archives
        verified_count = sum(1 for m in self._archives if self._is_archive_verified(m))
        unknown_count = len(self._archives) - verified_count

        logger.info(
            "Archives loaded: %d total, %d cached, %d need verification",
            len(self._archives),
            verified_count,
            unknown_count,
        )

    def _get_archive_info_from_mod(self, mod) -> ArchiveInfo | None:
        """Extract archive information from mod object."""
        if not mod.has_file():
            return None

        platform = self.state_manager.get_current_platform()
        mod_file = mod.get_file_for_platform(platform)

        if not mod_file:
            logger.warning(f"No file available for platform {platform}: {mod.id}")
            return None

        if not mod_file.has_download_url():
            return None

        return ArchiveInfo(
            mod_id=mod.id,
            filename=mod_file.filename,
            url=mod_file.download,
            expected_hash=mod_file.sha256,
            hash_algorithm=HashAlgorithm.SHA256,
            file_size=mod_file.size,
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
            color = self.STATUS_COLORS.get(status, QColor(COLOR_STATUS_NONE))
            status_item.setForeground(color)
            self._archive_table.setItem(row, COL_STATUS, status_item)

            row += 1

        self._archive_table.setSortingEnabled(True)

    def _update_archive_status(
        self, mod_id: str, status: ArchiveStatus, message: str = ""
    ) -> None:
        """Update status of a specific archive in the table.

        Args:
            mod_id: ID of the mod to update
            status: New status
        """
        # Update internal state
        self._archive_status[mod_id] = status

        # Find the row in the table
        for row in range(self._archive_table.rowCount()):
            item = self._archive_table.item(row, COL_MOD_NAME)
            if item and item.data(Qt.ItemDataRole.UserRole) == mod_id:
                # Update status cell
                status_text = tr(f"page.download.status.{status.value}")
                status_item = self._archive_table.item(row, COL_STATUS)

                if status_item:
                    status_item.setText(status_text)

                    color = self.STATUS_COLORS.get(status, QColor(COLOR_STATUS_NONE))
                    status_item.setForeground(color)
                    if status == ArchiveStatus.ERROR:
                        status_item.setToolTip(
                            tr("page.download.error_download_failed", message=message)
                        )
                    elif status in [ArchiveStatus.INVALID_SIZE, ArchiveStatus.INVALID_HASH]:
                        status_item.setToolTip(tr("page.download.warning_invalid_meta"))
                    else:
                        status_item.setToolTip("")

                break

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
            homepage=mod.homepage,
            status=status,
        )

    # ========================================
    # Verification Management
    # ========================================

    def _verify_archive(self, mod_id: str) -> ArchiveStatus:
        archive_info = self._archives[mod_id]
        file_path = self._download_path / archive_info.filename
        status = self._verifier.verify_archive(file_path, archive_info)

        # Update status AND cache
        self._archive_status[mod_id] = status
        self._update_archive_cache(mod_id, archive_info)
        self._update_archive_status(mod_id, status)

        return status

    def _on_verification_started(self, mod_id: str) -> None:
        """Handle verification start for a mod."""
        self._update_archive_status(mod_id, ArchiveStatus.VERIFYING)
        logger.debug(f"Started verification for {mod_id}")

    def _on_verification_completed(self, mod_id: str, status: ArchiveStatus) -> None:
        """Handle verification completion for a mod with cache update."""
        self._update_archive_status(mod_id, status)

        if mod_id in self._archives:
            self._update_archive_cache(mod_id, self._archives[mod_id])

        logger.debug("Completed verification for %s: %s", mod_id, status)

    def _on_verification_error(self, mod_id: str, error_message: str) -> None:
        """Handle verification error."""
        self._update_archive_status(mod_id, ArchiveStatus.ERROR, error_message)
        logger.error(f"Verification error for {mod_id}: {error_message}")

    # ========================================
    # Download Management
    # ========================================

    def _download_all_missing(self) -> None:
        """Start downloading all missing archives (with pre-verification)."""
        if self._is_verifying or self._is_downloading:
            return

        # Get all UNKNOWN archives
        to_verify = {
            mod_id: archive_info
            for mod_id, archive_info in self._archives.items()
            if self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN) == ArchiveStatus.UNKNOWN
        }

        if not to_verify:
            to_download_directly = [
                archive_info
                for mod_id, archive_info in self._archives.items()
                if self._archive_status.get(mod_id, ArchiveStatus.MISSING)
                == ArchiveStatus.MISSING
            ]

            if not to_download_directly:
                QMessageBox.information(
                    self,
                    tr("page.download.all_valid_title"),
                    tr("page.download.all_valid_message"),
                )
                return

            self._start_downloads_from_list(to_download_directly)
            return

        # Start verification process
        self._is_verifying = True
        self._update_navigation_buttons()

        # Show progress bar for verification
        self._global_progress.setVisible(True)
        self._global_progress.setMaximum(len(to_verify))
        self._global_progress.setValue(0)
        self._global_progress.setFormat(tr("page.download.verifying_progress"))

        self._verification_worker = VerificationWorker(
            to_verify, self._download_path, self._verifier
        )

        self._verification_worker.verification_started.connect(self._on_verification_started)
        self._verification_worker.verification_completed.connect(
            self._on_verification_completed_with_progress
        )
        self._verification_worker.all_completed.connect(
            self._on_verification_completed_then_download
        )
        self._verification_worker.error_occurred.connect(self._on_verification_error)

        self._verification_worker.start()

    def _on_verification_completed_with_progress(
        self, mod_id: str, status: ArchiveStatus
    ) -> None:
        """Handle verification completion with progress update."""
        self._on_verification_completed(mod_id, status)

        # Update progress bar
        current = self._global_progress.value()
        self._global_progress.setValue(current + 1)

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
            if self._archive_status.get(mod_id, ArchiveStatus.MISSING) == ArchiveStatus.MISSING
        ]

        if not to_download:
            self._show_final_summary()
            self._update_navigation_buttons()
            return

        self._start_downloads_from_list(to_download)

    def _start_downloads_from_list(self, archives: list[ArchiveInfo]) -> None:
        """Start downloading a list of archives."""
        self._is_downloading = True
        self._update_navigation_buttons()

        self._global_progress.setVisible(True)
        self._global_progress.setMaximum(len(archives))
        self._global_progress.setValue(0)
        self._global_progress.setFormat(tr("page.download.downloading_progress"))

        for archive_info in archives:
            self._download_manager.add_to_queue(archive_info)

        logger.info(f"Downloads queued: {len(archives)}")

        if not self._update_timer.isActive():
            self._update_timer.start()

    def _start_single_download(self, mod_id: str, force: bool = False) -> None:
        """Start downloading a single archive."""
        if self._is_downloading and not force:
            QMessageBox.warning(
                self,
                tr("page.download.operation_in_progress_title"),
                tr("page.download.download_in_progress_message"),
            )
            return

        if mod_id not in self._archives:
            return

        self._start_downloads_from_list([self._archives[mod_id]])

    # ========================================
    # Context Menu
    # ========================================

    def _show_context_menu(self, position):
        """Show context menu for archive table."""
        item = self._archive_table.itemAt(position)
        if not item:
            return

        row = item.row()
        mod_id_item = self._archive_table.item(row, COL_MOD_NAME)
        if not mod_id_item:
            return

        mod_id = mod_id_item.data(Qt.ItemDataRole.UserRole)
        if not mod_id or mod_id not in self._archives:
            return

        archive_info = self._archives[mod_id]
        status = self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN)

        menu = QMenu(self)

        action_download = QAction(tr("page.download.context.download"), self)
        action_download.triggered.connect(
            lambda: self._start_single_download(mod_id, force=True)
        )
        menu.addAction(action_download)

        if status != ArchiveStatus.MISSING:
            action_verify = QAction(tr("page.download.context.verify"), self)
            action_verify.triggered.connect(lambda: self._verify_archive(mod_id))
            menu.addAction(action_verify)

        menu.addSeparator()

        mod = self._mod_manager.get_mod_by_id(mod_id)
        if mod and hasattr(mod, "homepage") and mod.homepage:
            action_homepage = QAction(tr("page.download.context.open_homepage"), self)
            action_homepage.triggered.connect(
                lambda: QDesktopServices.openUrl(QUrl(mod.homepage))
            )
            menu.addAction(action_homepage)

        if archive_info.url:
            action_url = QAction(tr("page.download.context.open_download_url"), self)
            action_url.triggered.connect(
                lambda: QDesktopServices.openUrl(QUrl(archive_info.url))
            )
            menu.addAction(action_url)

        menu.exec(QCursor.pos())

    def _show_final_summary(self) -> None:
        """Show comprehensive summary of download/verification results."""
        # Count archives by status
        valid_count = 0
        warning_count = 0  # INVALID_HASH or INVALID_SIZE
        error_count = 0

        warning_details = []
        error_details = []

        for mod_id, _ in self._archives.items():
            status = self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN)
            mod = self._mod_manager.get_mod_by_id(mod_id)
            mod_name = mod.name if mod else mod_id

            if status == ArchiveStatus.VALID:
                valid_count += 1
            elif status in (ArchiveStatus.INVALID_HASH, ArchiveStatus.INVALID_SIZE):
                warning_count += 1
                warning_details.append(
                    f"• {mod_name} ({tr(f'page.download.status.{status.value}')})"
                )
            elif status in (ArchiveStatus.ERROR, ArchiveStatus.MISSING):
                error_count += 1
                error_details.append(f"• {mod_name}")

        # Build summary message
        summary_parts = []

        if valid_count > 0:
            summary_parts.append(tr("page.download.summary.valid", count=valid_count))

        if warning_count > 0:
            summary_parts.append(tr("page.download.summary.warnings", count=warning_count))

        if error_count > 0:
            summary_parts.append(tr("page.download.summary.errors", count=error_count))

        summary_text = "\n".join(summary_parts)

        # Create detailed message if there are issues
        details_text = ""
        if warning_details or error_details:
            details_parts = []

            if warning_details:
                details_parts.append("\n" + tr("page.download.summary.warnings_title") + ":")
                details_parts.extend(warning_details)

            if error_details:
                details_parts.append("\n" + tr("page.download.summary.errors_title") + ":")
                details_parts.extend(error_details)

            details_text = "\n".join(details_parts)

        msg = QMessageBox(self)
        msg.setWindowTitle(tr("page.download.summary.title"))
        msg.setText(summary_text)

        if details_text:
            msg.setDetailedText(details_text)

        # Determine icon based on worst status
        if error_count > 0:
            msg.setIcon(QMessageBox.Icon.Critical)
        elif warning_count > 0:
            msg.setIcon(QMessageBox.Icon.Warning)
        else:
            msg.setIcon(QMessageBox.Icon.Information)

        msg.exec()

    def _on_download_started(self, mod_id: str) -> None:
        """Handle download start."""
        self._update_archive_status(mod_id, ArchiveStatus.DOWNLOADING)

        # Create download widget
        progress = next(
            (
                p
                for p in self._download_manager.get_active_downloads()
                if p.archive_info.mod_id == mod_id
            ),
            None,
        )

        if progress:
            widget = ProgressItemWidget(progress.archive_info.filename)
            widget._btn_cancel.clicked.connect(lambda: self._cancel_download(mod_id))

            self._progress_widgets[mod_id] = widget
            self._progress_layout.insertWidget(self._progress_layout.count() - 1, widget)

    def _on_download_progress(self, mod_id: str, progress: DownloadProgress) -> None:
        """Handle download progress update."""
        pass  # Handled by timer

    def _on_download_canceled(self, mod_id: str) -> None:
        """Handle download cancelation."""
        logger.info(f"Download canceled: {mod_id}")
        self._update_after_download(mod_id)

    def _on_download_finished(self, mod_id: str) -> None:
        """Handle download completion."""
        logger.info(f"Download completed: {mod_id}")
        self._update_after_download(mod_id)

    def _on_all_downloads_completed(self) -> None:
        """Handle completion of all downloads."""
        self._update_timer.stop()
        self._is_downloading = False
        self._global_progress.setVisible(False)
        self._update_navigation_buttons()
        self._show_final_summary()

    def _update_after_download(self, mod_id: str):
        """Handle post-download updates with cache update."""
        # Remove widget
        if mod_id in self._progress_widgets:
            widget = self._progress_widgets[mod_id]
            self._progress_layout.removeWidget(widget)
            widget.deleteLater()
            del self._progress_widgets[mod_id]

        if mod_id in self._archives:
            self._verify_archive(mod_id)

        current = self._global_progress.value()
        self._global_progress.setValue(current + 1)

    def _on_download_error(self, mod_id: str, error_message: str) -> None:
        """Handle download error."""
        logger.error(f"Download error for {mod_id}: {error_message}")

        if mod_id in self._progress_widgets:
            widget = self._progress_widgets[mod_id]
            self._progress_layout.removeWidget(widget)
            widget.deleteLater()
            del self._progress_widgets[mod_id]

        self._update_archive_status(mod_id, ArchiveStatus.ERROR, error_message)

        current = self._global_progress.value()
        self._global_progress.setValue(current + 1)

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
    # Cache Management
    # ========================================

    def _cache_key(self, archive_info: ArchiveInfo) -> str:
        """
        Generate cache key for an archive.

        Key includes filename, size, and hash to detect changes.

        Args:
            archive_info: Archive information

        Returns:
            Cache key string
        """
        return f"{archive_info.filename}:{archive_info.file_size}:{archive_info.expected_hash}"

    def _is_archive_verified(self, mod_id: str) -> bool:
        """
        Check if archive has been verified (has a known status).

        Args:
            mod_id: Mod identifier

        Returns:
            True if archive has been verified
        """
        return (
            mod_id in self._archive_status
            and self._archive_status[mod_id] != ArchiveStatus.UNKNOWN
        )

    def _should_revalidate_archive(self, mod_id: str, archive_info: ArchiveInfo) -> bool:
        """
        Check if an archive needs revalidation.

        Returns True if:
        - Archive is new (not in cache)
        - Archive properties changed (filename, size, hash)
        - Download folder changed

        Args:
            mod_id: Mod identifier
            archive_info: Current archive information

        Returns:
            True if revalidation needed
        """
        # Not verified yet
        if not self._is_archive_verified(mod_id):
            return True

        # Check if archive properties match cached version
        cached_key = self._archive_cache_keys.get(mod_id)
        if not cached_key:
            return True

        current_key = self._cache_key(archive_info)

        # Properties changed, need revalidation
        if cached_key != current_key:
            logger.debug("Archive properties changed for %s, revalidation needed", mod_id)
            return True

        if self._cached_download_path != self._download_path:
            logger.debug("Download path changed, full revalidation needed")
            return True

        return False

    def _update_archive_cache(self, mod_id: str, archive_info: ArchiveInfo) -> None:
        """
        Update cache with archive information.

        Args:
            mod_id: Mod identifier
            archive_info: Archive information
        """
        self._archive_cache_keys[mod_id] = self._cache_key(archive_info)

    def _invalidate_removed_archives(self, current_mod_ids: set[str]) -> None:
        """
        Remove cache entries for archives no longer in selection.

        Args:
            current_mod_ids: Set of currently selected mod IDs
        """
        # Remove statuses for mods no longer selected
        removed_mods = set(self._archive_status.keys()) - current_mod_ids

        for mod_id in removed_mods:
            logger.debug("Removing cache for unselected mod: %s", mod_id)
            self._archive_status.pop(mod_id, None)
            self._archive_cache_keys.pop(mod_id, None)

    # ========================================
    # UI Actions
    # ========================================

    def _on_ignore_settings_changed(self) -> None:
        """Handle changes to ignore warnings/errors checkboxes."""
        self._update_navigation_buttons()

    def _open_download_folder(self) -> None:
        """Open download folder in file manager."""
        import subprocess

        try:
            platform = self.state_manager.get_current_platform()
            if platform == "windows":
                subprocess.run(["explorer", str(self._download_path)])
            elif platform == "macos":
                subprocess.run(["open", str(self._download_path)])
            else:
                subprocess.run(["xdg-open", str(self._download_path)])
        except Exception as e:
            logger.error(f"Error opening folder: {e}")
            QMessageBox.warning(
                self,
                tr("page.download.open_folder_error_title"),
                tr("page.download.open_folder_error_message", error=str(e)),
            )

    def _on_archive_double_click(self, item: QTableWidgetItem) -> None:
        """Handle double-click on archive to verify and optionally download."""
        row = item.row()
        mod_id_item = self._archive_table.item(row, COL_MOD_NAME)

        if not mod_id_item:
            return

        mod_id = mod_id_item.data(Qt.ItemDataRole.UserRole)
        if not mod_id or mod_id not in self._archives:
            return

        archive_info = self._archives[mod_id]
        current_status = self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN)

        if current_status == ArchiveStatus.MISSING:
            self._start_single_download(mod_id)
            return

        reply = QMessageBox.question(
            self,
            tr("page.download.redownload_title"),
            tr("page.download.redownload_message", filename=archive_info.filename),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._start_single_download(mod_id, force=True)

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

    def get_additional_buttons(self) -> list[QPushButton]:
        """Get additional buttons."""
        return [self._btn_download_all, self._btn_open_folder]

    def can_go_to_next_page(self) -> bool:
        """Check if can proceed to next page."""
        if self._is_verifying or self._is_downloading:
            return False

        ignore_warnings = self._chk_ignore_warnings.isChecked()
        ignore_errors = self._chk_ignore_errors.isChecked()

        for mod_id, _ in self._archives.items():
            status = self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN)

            if status == ArchiveStatus.UNKNOWN:
                return False

            if not ignore_warnings and not ignore_errors:
                if status != ArchiveStatus.VALID:
                    return False

            elif ignore_warnings:
                # Allow VALID and warning statuses only
                if status not in (
                    ArchiveStatus.VALID,
                    ArchiveStatus.INVALID_HASH,
                    ArchiveStatus.INVALID_SIZE,
                ):
                    return False

            # Ignore errors (implicitly ignores warnings too)

        return True

    def can_go_to_previous_page(self) -> bool:
        """Check if user can return to the previous page."""
        if self._is_verifying or self._is_downloading:
            return False
        return True

    def on_page_shown(self) -> None:
        """Called when page becomes visible."""
        super().on_page_shown()

        # Check if download path changed
        download_folder = self.state_manager.get_download_folder()

        if not download_folder:
            return

        new_download_path = Path(download_folder)
        download_path_changed = self._cached_download_path != new_download_path

        if download_path_changed:
            logger.info(
                "Download path changed from %s to %s, invalidating cache",
                self._cached_download_path,
                new_download_path,
            )
            # Clear cache when download folder changes
            self._archive_status.clear()
            self._archive_cache_keys.clear()

        # Update paths
        self._download_path = new_download_path
        self._cached_download_path = new_download_path
        self._download_manager.set_download_path(self._download_path)

        # Load archives (will preserve cache if path unchanged)
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

        self._chk_ignore_warnings.setText(tr("page.download.ignore_warnings"))
        self._chk_ignore_warnings.setToolTip(tr("page.download.ignore_warnings_tooltip"))
        self._chk_ignore_errors.setText(tr("page.download.ignore_errors"))
        self._chk_ignore_errors.setToolTip(tr("page.download.ignore_errors_tooltip"))

        # Update filter combo
        current = self._filter_combo.currentData()
        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()
        self._filter_combo.addItem(tr("page.download.filter.all"), None)
        self._filter_combo.addItem(tr("page.download.filter.missing"), ArchiveStatus.MISSING)
        self._filter_combo.addItem(
            tr("page.download.filter.invalid"), ArchiveStatus.INVALID_HASH
        )
        self._filter_combo.addItem(tr("page.download.filter.valid"), ArchiveStatus.VALID)
        self._filter_combo.addItem(tr("page.download.filter.unknown"), ArchiveStatus.UNKNOWN)

        for i in range(self._filter_combo.count()):
            if self._filter_combo.itemData(i) == current:
                self._filter_combo.setCurrentIndex(i)
                break
        self._filter_combo.blockSignals(False)

        # Update table headers
        self._archive_table.setHorizontalHeaderLabels(
            [
                tr("page.download.col_mod_name"),
                tr("page.download.col_filename"),
                tr("page.download.col_status"),
            ]
        )

        self._refresh_archive_table()

        for widget in self._progress_widgets.values():
            widget.retranslate_ui()
