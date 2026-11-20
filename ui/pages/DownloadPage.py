from dataclasses import dataclass
import html
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
from core.ArchiveRegistry import ArchiveRecord, ArchiveSource
from core.ArchiveService import ArchiveResolution, ArchiveService
from core.DownloadManager import (
    ArchiveInfo,
    ArchiveStatus,
    DownloadManager,
    DownloadProgress,
    HashAlgorithm,
)
from core.File import format_size
from core.Mod import Mod
from core.Platform import Platform
from core.StateManager import StateManager
from core.TranslationManager import tr
from ui.pages.BasePage import BasePage
from ui.widgets.ArchiveDialogs import confirm_archive_mismatch, select_archive_file
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


@dataclass(frozen=True)
class VerificationJob:
    """Archive to hash for a mod; `associate` makes it the user's manual choice."""

    mod: Mod
    path: Path
    source: ArchiveSource
    associate: bool = False


class VerificationWorker(QThread):
    """Worker thread hashing archives without blocking UI.

    Cancelling takes effect between two archives: a hash in progress is
    completed so that no partial proof is ever recorded.
    """

    verification_started = Signal(str)  # mod_id
    verification_completed = Signal(str, object)  # mod_id, ArchiveResolution
    all_completed = Signal(dict)  # {mod_id: ArchiveResolution}
    error_occurred = Signal(str, str)  # mod_id, error_message

    def __init__(
        self, jobs: dict[str, VerificationJob], service: ArchiveService, platform: str
    ):
        super().__init__()
        self.jobs = jobs
        self.service = service
        self.platform = platform
        self._is_cancelled = False
        self.results: dict[str, ArchiveResolution] = {}

    def run(self):
        """Run verification process."""
        try:
            for mod_id, job in self.jobs.items():
                if self._is_cancelled:
                    break

                try:
                    self.verification_started.emit(mod_id)
                    resolution = self._run_job(job)
                    self.results[mod_id] = resolution
                    self.verification_completed.emit(mod_id, resolution)
                except Exception as e:
                    logger.error(f"Error verifying {mod_id}: {e}")
                    self.error_occurred.emit(mod_id, str(e))

            self.all_completed.emit(self.results)
        except Exception as e:
            logger.error(f"Critical error in verification thread: {e}")
            self.all_completed.emit(self.results)

    def _run_job(self, job: VerificationJob) -> ArchiveResolution:
        if job.associate:
            return self.service.associate_manual(job.mod, job.path, self.platform)
        return self.service.verify(job.mod, job.path, job.source, self.platform)

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
        manual_path: Path | None = None,
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
            manual_path: Archive associated by the user, if any
        """

        details = [
            f"<b>{tr('page.download.details.mod')}:</b> {mod_name}",
            f"<b>{tr('page.download.details.filename')}:</b> {filename or '—'}",
        ]

        if manual_path:
            details.append(
                f"<b>{tr('page.download.details.manual_archive')}:</b> {html.escape(str(manual_path))}"
            )

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
    """Page for managing mod archive downloads.

    Statuses come from the archive registry: a proof is reused only while the
    file keeps the size and modification time it had when it was hashed, and
    every hash runs in a `VerificationWorker`.
    """

    STATUS_COLORS = {
        ArchiveStatus.VALID: QColor(COLOR_STATUS_COMPLETE),
        ArchiveStatus.NO_REFERENCE: QColor(COLOR_STATUS_COMPLETE),
        ArchiveStatus.MISMATCH_ACCEPTED: QColor(COLOR_WARNING),
        ArchiveStatus.INVALID_HASH: QColor(COLOR_WARNING),
        ArchiveStatus.INVALID_SIZE: QColor(COLOR_WARNING),
        ArchiveStatus.MISSING: QColor(COLOR_STATUS_NONE),
        ArchiveStatus.MANUAL_EXPECTED: QColor(COLOR_ERROR),
        ArchiveStatus.UNKNOWN: QColor(COLOR_STATUS_NONE),
        ArchiveStatus.DOWNLOADING: QColor(COLOR_WARNING),
        ArchiveStatus.VERIFYING: QColor(COLOR_WARNING),
        ArchiveStatus.ERROR: QColor(COLOR_ERROR),
    }
    MISMATCH_STATUSES = (ArchiveStatus.INVALID_HASH, ArchiveStatus.INVALID_SIZE)
    ACTIVE_STATUSES = (ArchiveStatus.DOWNLOADING, ArchiveStatus.VERIFYING)

    def __init__(self, state_manager: StateManager):
        super().__init__(state_manager)

        self._mod_manager = self.state_manager.get_mod_manager()
        self._download_path: Path | None = None
        self._platform = Platform.get_current()

        # Core components
        self._archive_service = ArchiveService(self.state_manager.get_archive_registry())
        self._download_manager = DownloadManager()
        self._download_manager.setParent(self)

        # Archive tracking
        self._archives: dict[str, ArchiveInfo] = {}
        self._archive_status: dict[str, ArchiveStatus] = {}
        self._archive_resolutions: dict[str, ArchiveResolution] = {}
        # Mods whose TP2 ships in another mod's archive: {mod_id: provider_id}
        self._provided_mods: dict[str, str] = {}

        # Operation tracking
        self._is_verifying = False
        self._is_downloading = False
        self._verification_worker: VerificationWorker | None = None
        self._pending_verifications: dict[str, VerificationJob] = {}
        self._download_after_verification = False
        self._summary_pending = False
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
        """List the archive of every selected mod, including manual ones.

        A mod provided by another one needs that mod's archive, which is then
        listed even when none of the provider's components is selected.
        """
        self._archives.clear()
        self._provided_mods.clear()

        selected = self.state_manager.get_selected_components()
        if not selected:
            logger.warning("No components selected")
            return

        archive_owners = self._archive_owners(
            {reference.partition(":")[0] for reference in selected if reference}
        )
        self._forget_removed_archives({mod.id for mod in archive_owners})

        for mod in archive_owners:
            self._archives[mod.id] = self._get_archive_info_from_mod(mod)
            if self._archive_status.get(mod.id) not in self.ACTIVE_STATUSES:
                self._resolve_archive(mod)

        self._refresh_archive_table()
        self._archive_table.sortItems(COL_MOD_NAME, Qt.SortOrder.AscendingOrder)
        self._update_navigation_buttons()

        unknown_count = sum(
            1 for status in self._archive_status.values() if status is ArchiveStatus.UNKNOWN
        )
        logger.info(
            "Archives loaded: %d total, %d need verification",
            len(self._archives),
            unknown_count,
        )

    def _archive_owners(self, mod_ids: set[str]) -> list[Mod]:
        """Return the mods whose archives the selection needs, providers included."""
        owners: dict[str, Mod] = {}
        for mod_id in sorted(mod_ids):
            mod = self._mod_manager.get_mod_by_id(mod_id)
            if not mod:
                continue
            if mod.provided_by:
                provider = self._mod_manager.get_mod_by_id(mod.provided_by)
                if provider is None:
                    logger.error("Provider %s of %s not in catalogue", mod.provided_by, mod.id)
                    continue
                self._provided_mods[mod.id] = provider.id
                mod = provider
            owners[mod.id] = mod
        return list(owners.values())

    @staticmethod
    def _get_archive_info_from_mod(mod: Mod) -> ArchiveInfo:
        """Describe the archive expected for a mod, downloadable or not.

        A mod without automatic download is still listed: its archive is then
        expected from the user.
        """
        mod_file = mod.get_file_for_platform(Platform.get_current()) if mod.has_file() else None
        if mod_file is None:
            return ArchiveInfo(mod_id=mod.id, filename=None, url=None, expected_hash="")

        return ArchiveInfo(
            mod_id=mod.id,
            filename=mod_file.filename,
            url=mod_file.download if mod_file.has_download_url() else None,
            expected_hash=mod_file.sha256 or "",
            hash_algorithm=HashAlgorithm.SHA256,
            file_size=mod_file.size,
        )

    def _resolve_archive(self, mod: Mod) -> ArchiveResolution:
        """Read the registry proof of a mod archive without hashing it."""
        resolution = self._archive_service.resolve(mod, self._download_path, self._platform)
        self._archive_resolutions[mod.id] = resolution
        self._archive_status[mod.id] = resolution.status
        return resolution

    def _manual_path(self, mod_id: str) -> Path | None:
        resolution = self._archive_resolutions.get(mod_id)
        if (
            resolution
            and resolution.record
            and resolution.record.source is ArchiveSource.MANUAL
        ):
            return resolution.record.archive_path
        return None

    def _displayed_filename(self, mod_id: str) -> str:
        manual_path = self._manual_path(mod_id)
        if manual_path:
            return manual_path.name
        return self._archives[mod_id].filename or "—"

    def _refresh_archive_table(self) -> None:
        """Refresh the archive table display."""
        if not self._archive_table:
            return

        # Disable sorting during update
        self._archive_table.setSortingEnabled(False)
        self._archive_table.setRowCount(0)

        filter_status = self._filter_combo.currentData() if self._filter_combo else None

        row = 0
        for mod_id in self._archives:
            status = self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN)

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
            filename_item = QTableWidgetItem(self._displayed_filename(mod_id))
            self._archive_table.setItem(row, COL_FILENAME, filename_item)

            # Column 2: Status (not sortable, displayed with color)
            self._archive_table.setItem(row, COL_STATUS, QTableWidgetItem())

            row += 1

        if not filter_status:
            for mod_id, provider_id in self._provided_mods.items():
                self._insert_provided_row(row, mod_id, provider_id)
                row += 1

        self._archive_table.setSortingEnabled(True)

        for mod_id in self._archives:
            self._update_archive_status(
                mod_id, self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN)
            )

    def _insert_provided_row(self, row: int, mod_id: str, provider_id: str) -> None:
        """Show a mod without archive of its own and the mod that delivers it."""
        mod = self._mod_manager.get_mod_by_id(mod_id)
        provider = self._mod_manager.get_mod_by_id(provider_id)
        provider_name = provider.name if provider else provider_id

        self._archive_table.insertRow(row)
        name_item = QTableWidgetItem(mod.name if mod else mod_id)
        name_item.setData(Qt.ItemDataRole.UserRole, mod_id)
        self._archive_table.setItem(row, COL_MOD_NAME, name_item)
        self._archive_table.setItem(
            row, COL_FILENAME, QTableWidgetItem(self._displayed_filename(provider_id))
        )

        status_item = QTableWidgetItem(
            tr("page.download.status.provided", provider=provider_name)
        )
        status_item.setForeground(QColor(COLOR_STATUS_NONE))
        status_item.setToolTip(tr("page.download.provided_tooltip", provider=provider_name))
        self._archive_table.setItem(row, COL_STATUS, status_item)

    def _update_archive_status(
        self, mod_id: str, status: ArchiveStatus, message: str = ""
    ) -> None:
        """Update status of a specific archive in the table.

        Args:
            mod_id: ID of the mod to update
            status: New status
            message: Error detail shown in the tooltip
        """
        self._archive_status[mod_id] = status

        row = self._find_row(mod_id)
        if row is None:
            return

        filename_item = self._archive_table.item(row, COL_FILENAME)
        if filename_item and mod_id in self._archives:
            filename_item.setText(self._displayed_filename(mod_id))

        status_item = self._archive_table.item(row, COL_STATUS)
        if not status_item:
            return

        status_item.setText(tr(f"page.download.status.{status.value}"))
        status_item.setForeground(self.STATUS_COLORS.get(status, QColor(COLOR_STATUS_NONE)))
        status_item.setToolTip(self._status_tooltip(status, message))

    @staticmethod
    def _status_tooltip(status: ArchiveStatus, message: str) -> str:
        if status == ArchiveStatus.ERROR:
            return tr("page.download.error_download_failed", message=message)
        if status in DownloadPage.MISMATCH_STATUSES:
            return tr("page.download.warning_invalid_meta")
        if status == ArchiveStatus.MANUAL_EXPECTED:
            return tr("page.download.manual_expected_tooltip")
        if status == ArchiveStatus.NO_REFERENCE:
            return tr("page.download.no_reference_tooltip")
        if status == ArchiveStatus.MISMATCH_ACCEPTED:
            return tr("page.download.mismatch_accepted_tooltip")
        return ""

    def _find_row(self, mod_id: str) -> int | None:
        if not self._archive_table:
            return None
        for row in range(self._archive_table.rowCount()):
            item = self._archive_table.item(row, COL_MOD_NAME)
            if item and item.data(Qt.ItemDataRole.UserRole) == mod_id:
                return row
        return None

    def _selected_mod_id(self, item: QTableWidgetItem | None) -> str | None:
        if item is None:
            return None
        mod_id_item = self._archive_table.item(item.row(), COL_MOD_NAME)
        mod_id = mod_id_item.data(Qt.ItemDataRole.UserRole) if mod_id_item else None
        # Acting on a provided mod means acting on the archive that delivers it.
        mod_id = self._provided_mods.get(mod_id, mod_id)
        return mod_id if mod_id in self._archives else None

    # ========================================
    # Selection Management
    # ========================================

    def _on_selection_changed(self) -> None:
        """Handle table selection change to update details panel."""
        selected_items = self._archive_table.selectedItems()
        mod_id = self._selected_mod_id(selected_items[0] if selected_items else None)
        mod = self._mod_manager.get_mod_by_id(mod_id) if mod_id else None

        if not mod:
            self._details_panel.clear()
            return

        archive_info = self._archives[mod_id]
        self._details_panel.set_archive_info(
            mod_name=mod.name,
            filename=archive_info.filename,
            file_size=archive_info.file_size,
            expected_hash=archive_info.expected_hash,
            url=archive_info.url,
            homepage=mod.homepage,
            status=self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN),
            manual_path=self._manual_path(mod_id),
        )

    # ========================================
    # Verification Management
    # ========================================

    def _verify_archives(self, mod_ids: list[str], then_download: bool = False) -> None:
        """Hash the archives of these mods in the background.

        Requests made while a worker runs are queued and handled once it ends.
        """
        jobs = {}
        for mod_id in mod_ids:
            job = self._verification_job(mod_id)
            if job is not None:
                jobs[mod_id] = job
        self._queue_verifications(jobs, then_download)

    def _verification_job(self, mod_id: str) -> VerificationJob | None:
        mod = self._mod_manager.get_mod_by_id(mod_id)
        if mod is None:
            return None
        resolution = self._resolve_archive(mod)
        if resolution.path is None:
            self._update_archive_status(mod_id, resolution.status)
            return None
        source = (
            resolution.record.source
            if resolution.record is not None
            else ArchiveSource.DOWNLOAD
        )
        return VerificationJob(mod, resolution.path, source)

    def _queue_verifications(
        self, jobs: dict[str, VerificationJob], then_download: bool
    ) -> None:
        self._download_after_verification |= then_download
        self._pending_verifications.update(jobs)
        if self._verification_worker is None:
            self._start_pending_verifications()

    def _start_pending_verifications(self) -> None:
        if not self._pending_verifications:
            return

        jobs, self._pending_verifications = self._pending_verifications, {}
        self._is_verifying = True
        self._update_navigation_buttons()

        # While downloading, the bar counts downloads and their hashes follow.
        if not self._is_downloading:
            self._global_progress.setVisible(True)
            self._global_progress.setMaximum(len(jobs))
            self._global_progress.setValue(0)
            self._global_progress.setFormat(tr("page.download.verifying_progress"))

        self._verification_worker = VerificationWorker(
            jobs, self._archive_service, self._platform
        )
        self._verification_worker.verification_started.connect(self._on_verification_started)
        self._verification_worker.verification_completed.connect(
            self._on_verification_completed
        )
        self._verification_worker.all_completed.connect(self._on_verifications_completed)
        self._verification_worker.error_occurred.connect(self._on_verification_error)

        self.start_task(self._verification_worker)

    def _on_verification_started(self, mod_id: str) -> None:
        """Handle verification start for a mod."""
        self._update_archive_status(mod_id, ArchiveStatus.VERIFYING)
        logger.debug(f"Started verification for {mod_id}")

    def _on_verification_completed(self, mod_id: str, resolution: ArchiveResolution) -> None:
        """Show the proof of an archive, asking for review when it differs."""
        if not self._is_downloading:
            self._global_progress.setValue(self._global_progress.value() + 1)

        job = self._verification_worker.jobs.get(mod_id) if self._verification_worker else None
        if job is not None and job.associate and not resolution.status.is_usable:
            self._review_manual_archive(job.mod, resolution)
            return

        self._archive_resolutions[mod_id] = resolution
        self._update_archive_status(mod_id, resolution.status)
        logger.debug("Completed verification for %s: %s", mod_id, resolution.status)

    def _on_verification_error(self, mod_id: str, error_message: str) -> None:
        """Handle verification error."""
        self._update_archive_status(mod_id, ArchiveStatus.ERROR, error_message)
        logger.error(f"Verification error for {mod_id}: {error_message}")

    def _on_verifications_completed(self, results: dict) -> None:
        """Start queued verifications, then the downloads they were preparing."""
        self._is_verifying = False
        self._verification_worker = None
        self._global_progress.setVisible(self._is_downloading)

        if self._closing:
            return

        if self._pending_verifications:
            self._start_pending_verifications()
            return

        self._update_navigation_buttons()
        if self._download_after_verification:
            self._download_after_verification = False
            logger.info("Verification completed, starting downloads")
            self._start_downloads()
            return
        self._show_summary_when_idle()

    # ========================================
    # Manual Archives
    # ========================================

    def _associate_manual_archive(self, mod_id: str) -> None:
        """Let the user pick the archive of a mod; it is hashed before use."""
        mod = self._mod_manager.get_mod_by_id(mod_id)
        if mod is None:
            return

        archive_path = select_archive_file(self, self._download_path)
        if archive_path is None:
            return

        job = VerificationJob(mod, archive_path, ArchiveSource.MANUAL, associate=True)
        self._queue_verifications({mod_id: job}, then_download=False)

    def _review_manual_archive(self, mod: Mod, resolution: ArchiveResolution) -> None:
        """Ask whether a differing user archive may replace the catalogue one."""
        if self._confirm_mismatch(mod, resolution.record):
            accepted = self._archive_service.accept_mismatch(
                mod, resolution.record, self._platform
            )
            self._archive_resolutions[mod.id] = accepted
            self._update_archive_status(mod.id, accepted.status)
            return
        self._update_archive_status(mod.id, self._resolve_archive(mod).status)

    def _accept_current_mismatch(self, mod_id: str) -> None:
        """Accept a verified archive whose size or hash differs from the catalogue."""
        mod = self._mod_manager.get_mod_by_id(mod_id)
        resolution = self._archive_resolutions.get(mod_id)
        if mod is None or resolution is None or resolution.record is None:
            return
        if not self._confirm_mismatch(mod, resolution.record):
            return

        accepted = self._archive_service.accept_mismatch(mod, resolution.record, self._platform)
        self._archive_resolutions[mod_id] = accepted
        self._update_archive_status(mod_id, accepted.status)
        self._update_navigation_buttons()

    def _confirm_mismatch(self, mod: Mod, record: ArchiveRecord) -> bool:
        return confirm_archive_mismatch(self, mod, record, self._archives[mod.id].file_size)

    def _remove_manual_archive(self, mod_id: str) -> None:
        mod = self._mod_manager.get_mod_by_id(mod_id)
        if mod is None:
            return
        self._archive_service.remove_manual(mod_id)
        self._update_archive_status(mod_id, self._resolve_archive(mod).status)
        self._update_navigation_buttons()

    # ========================================
    # Download Management
    # ========================================

    def _download_all_missing(self) -> None:
        """Start downloading all missing archives (with pre-verification)."""
        if self._is_verifying or self._is_downloading:
            return

        to_verify = [
            mod_id
            for mod_id in self._archives
            if self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN) == ArchiveStatus.UNKNOWN
        ]
        if to_verify:
            self._verify_archives(to_verify, then_download=True)
            return

        to_download = self._downloadable_missing_archives()
        if not to_download:
            QMessageBox.information(
                self,
                tr("page.download.all_valid_title"),
                tr("page.download.all_valid_message"),
            )
            return

        self._start_downloads_from_list(to_download)

    def _downloadable_missing_archives(self) -> list[ArchiveInfo]:
        return [
            archive_info
            for mod_id, archive_info in self._archives.items()
            if archive_info.url
            and self._archive_status.get(mod_id, ArchiveStatus.MISSING) == ArchiveStatus.MISSING
        ]

    def _start_downloads(self) -> None:
        """Start downloading all archives that need downloading."""
        to_download = self._downloadable_missing_archives()
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

        if mod_id not in self._archives or not self._archives[mod_id].url:
            return

        self._start_downloads_from_list([self._archives[mod_id]])

    # ========================================
    # Context Menu
    # ========================================

    def _show_context_menu(self, position):
        """Show context menu for archive table."""
        mod_id = self._selected_mod_id(self._archive_table.itemAt(position))
        if not mod_id:
            return

        archive_info = self._archives[mod_id]
        status = self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN)
        busy = status in self.ACTIVE_STATUSES

        menu = QMenu(self)

        if archive_info.url and not self._manual_path(mod_id):
            action_download = QAction(tr("page.download.context.download"), self)
            action_download.triggered.connect(
                lambda: self._start_single_download(mod_id, force=True)
            )
            menu.addAction(action_download)

        if status not in (ArchiveStatus.MISSING, ArchiveStatus.MANUAL_EXPECTED) and not busy:
            action_verify = QAction(tr("page.download.context.verify"), self)
            action_verify.triggered.connect(lambda: self._verify_archives([mod_id]))
            menu.addAction(action_verify)

        if status in self.MISMATCH_STATUSES:
            action_accept = QAction(tr("page.download.context.accept_mismatch"), self)
            action_accept.triggered.connect(lambda: self._accept_current_mismatch(mod_id))
            menu.addAction(action_accept)

        menu.addSeparator()

        if not busy:
            action_associate = QAction(tr("page.download.context.associate"), self)
            action_associate.triggered.connect(lambda: self._associate_manual_archive(mod_id))
            menu.addAction(action_associate)

        if self._manual_path(mod_id) and not busy:
            action_remove = QAction(tr("page.download.context.remove_association"), self)
            action_remove.triggered.connect(lambda: self._remove_manual_archive(mod_id))
            menu.addAction(action_remove)

        menu.addSeparator()

        mod = self._mod_manager.get_mod_by_id(mod_id)
        if mod and mod.homepage:
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

    def _show_summary_when_idle(self) -> None:
        """Show the download summary once downloads and their verification ended."""
        if self._summary_pending and not (self._is_downloading or self._is_verifying):
            self._summary_pending = False
            self._show_final_summary()

    def _show_final_summary(self) -> None:
        """Show comprehensive summary of download/verification results."""
        if self._closing:
            return
        # Count archives by status
        valid_count = 0
        warning_count = 0  # INVALID_HASH or INVALID_SIZE
        error_count = 0

        warning_details = []
        error_details = []

        for mod_id in self._archives:
            status = self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN)
            mod = self._mod_manager.get_mod_by_id(mod_id)
            mod_name = mod.name if mod else mod_id
            label = tr(f"page.download.status.{status.value}")

            if status.is_usable:
                valid_count += 1
            elif status in self.MISMATCH_STATUSES:
                warning_count += 1
                warning_details.append(f"• {mod_name} ({label})")
            elif status in (
                ArchiveStatus.ERROR,
                ArchiveStatus.MISSING,
                ArchiveStatus.MANUAL_EXPECTED,
            ):
                error_count += 1
                error_details.append(f"• {mod_name} ({label})")

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
        """Handle completion of all downloads; the summary waits for their hashes."""
        self._update_timer.stop()
        self._is_downloading = False
        self._global_progress.setVisible(self._is_verifying)
        self._update_navigation_buttons()
        self._summary_pending = True
        self._show_summary_when_idle()

    def _update_after_download(self, mod_id: str):
        """Remove the progress widget and verify what is now on disk."""
        self._remove_progress_widget(mod_id)

        if mod_id in self._archives:
            self._verify_archives([mod_id])

        current = self._global_progress.value()
        self._global_progress.setValue(current + 1)

    def _on_download_error(self, mod_id: str, error_message: str) -> None:
        """Handle download error; a previous archive kept on disk is verified again."""
        logger.error(f"Download error for {mod_id}: {error_message}")
        self._remove_progress_widget(mod_id)
        self._update_archive_status(mod_id, ArchiveStatus.ERROR, error_message)

        mod = self._mod_manager.get_mod_by_id(mod_id)
        if (
            mod is not None
            and self._archive_service.resolve(mod, self._download_path, self._platform).path
        ):
            self._verify_archives([mod_id])

        current = self._global_progress.value()
        self._global_progress.setValue(current + 1)

    def _remove_progress_widget(self, mod_id: str) -> None:
        widget = self._progress_widgets.pop(mod_id, None)
        if widget is not None:
            self._progress_layout.removeWidget(widget)
            widget.deleteLater()

    def _update_active_downloads(self) -> None:
        """Update all active download widgets."""
        for progress in self._download_manager.get_active_downloads():
            mod_id = progress.archive_info.mod_id
            if mod_id in self._progress_widgets:
                self._progress_widgets[mod_id].update_progress(progress)

    def _cancel_download(self, mod_id: str) -> None:
        """Cancel an active download; the status is re-read once it has stopped."""
        self._download_manager.cancel_download(mod_id)

    def _forget_removed_archives(self, current_mod_ids: set[str]) -> None:
        """Drop the statuses of mods that are no longer selected."""
        for mod_id in set(self._archive_status) - current_mod_ids:
            self._archive_status.pop(mod_id, None)
            self._archive_resolutions.pop(mod_id, None)

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
            platform = Platform.get_current()
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
        """Download a missing archive, ask for a manual one, or offer a new download."""
        mod_id = self._selected_mod_id(item)
        if not mod_id:
            return

        archive_info = self._archives[mod_id]
        current_status = self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN)

        if current_status in self.ACTIVE_STATUSES:
            return

        if current_status == ArchiveStatus.MANUAL_EXPECTED or not archive_info.url:
            self._associate_manual_archive(mod_id)
            return

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

        for mod_id in self._archives:
            status = self._archive_status.get(mod_id, ArchiveStatus.UNKNOWN)

            if status == ArchiveStatus.UNKNOWN:
                return False

            if not ignore_warnings and not ignore_errors:
                if not status.is_usable:
                    return False

            elif ignore_warnings:
                # Allow usable and warning statuses only
                if not status.is_usable and status not in self.MISMATCH_STATUSES:
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

        # Statuses are re-read from the registry, which checks the files on disk.
        self._download_path = Path(download_folder)
        self._download_manager.set_download_path(self._download_path)
        self._load_archives()

    def has_critical_task(self) -> bool:
        """Verification and downloads are interrupted by a shutdown."""
        return super().has_critical_task() or self._is_verifying or self._is_downloading

    def request_shutdown(self) -> None:
        """Cancel the verification and every queued or active download."""
        super().request_shutdown()
        self._pending_verifications.clear()
        if self._verification_worker:
            self._verification_worker.cancel()
        self._download_manager.cancel_all()

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
        self._filter_combo.addItem(
            tr("page.download.filter.manual_expected"), ArchiveStatus.MANUAL_EXPECTED
        )

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
