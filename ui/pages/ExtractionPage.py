import logging
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView,
    QVBoxLayout, QProgressBar
)

from constants import (
    COLOR_STATUS_COMPLETE, COLOR_ERROR, COLOR_STATUS_NONE,
    COLOR_WARNING, MARGIN_STANDARD,
    SPACING_SMALL
)
from core.ArchiveExtractor import ArchiveExtractor, ExtractionStatus, ExtractionInfo
from core.StateManager import StateManager
from core.TranslationManager import tr
from ui.pages.BasePage import BasePage

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

COL_MOD_NAME = 0
COL_ARCHIVE = 1
COL_DESTINATION = 2
COL_STATUS = 3
COLUMN_COUNT = 4


# ============================================================================
# Structure Validator
# ============================================================================

class StructureValidator:
    """Validates and fixes extracted mod structure."""

    # Possible TP2 locations (in order of preference)
    TP2_PATTERNS = [
        '{game_dir}/setup-{tp2}.exe',  # Most reliable
        '{game_dir}/{tp2}.tp2',
        '{game_dir}/setup-{tp2}.tp2',
        '{game_dir}/{tp2}/{tp2}.tp2',
        '{game_dir}/{tp2}/setup-{tp2}.tp2',
    ]

    @staticmethod
    def validate_structure(game_dir: Path, tp2_name: str) -> tuple[bool, Path | None]:
        """Validate mod extraction structure.

        Args:
            game_dir: Game directory where mod should be
            tp2_name: TP2 name (without extension)

        Returns:
            Tuple of (is_valid, tp2_path if found)
        """
        for pattern in StructureValidator.TP2_PATTERNS:
            tp2_path = Path(str(pattern).format(game_dir=game_dir, tp2=tp2_name))
            if tp2_path.exists():
                return True, tp2_path

        return False, None

    @staticmethod
    def fix_structure(game_dir: Path, tp2_name: str) -> bool:
        """Attempt to fix invalid extraction structure.

        Moves files up from nested directories if needed.

        Args:
            game_dir: Game directory
            tp2_name: TP2 name

        Returns:
            True if structure fixed successfully
        """
        # Find TP2 file anywhere in the tree
        tp2_location = StructureValidator._find_tp2_deep(game_dir, tp2_name)

        if not tp2_location:
            logger.error(f"Could not find TP2 file for {tp2_name}")
            return False

        # Check if already valid
        valid, _ = StructureValidator.validate_structure(game_dir, tp2_name)
        if valid:
            return True

        # Determine how many levels to move up
        current_parent = tp2_location.parent

        # If TP2 is in a subdirectory, check if we should move to game_dir or tp2_name folder
        if current_parent != game_dir:
            # Check if there's a mod folder that should exist
            expected_mod_folder = game_dir / tp2_name

            # Move all content from current location up to correct location
            try:
                StructureValidator._move_content_up(current_parent, expected_mod_folder)
                return True
            except Exception as e:
                logger.error(f"Failed to fix structure: {e}")
                return False

        return False

    @staticmethod
    def _find_tp2_deep(search_dir: Path, tp2_name: str, max_depth: int = 3) -> Path | None:
        """Recursively search for TP2 file.

        Args:
            search_dir: Directory to search in
            tp2_name: TP2 name to look for
            max_depth: Maximum search depth

        Returns:
            Path to TP2 file if found
        """
        if max_depth <= 0:
            return None

        # Check current directory
        for pattern in [f"{tp2_name}.tp2", f"setup-{tp2_name}.tp2"]:
            tp2_path = search_dir / pattern
            if tp2_path.exists():
                return tp2_path

        # Search subdirectories
        try:
            for subdir in search_dir.iterdir():
                if subdir.is_dir():
                    result = StructureValidator._find_tp2_deep(subdir, tp2_name, max_depth - 1)
                    if result:
                        return result
        except PermissionError:
            pass

        return None

    @staticmethod
    def _move_content_up(source_dir: Path, target_dir: Path) -> None:
        """Move all content from source to target directory.

        Args:
            source_dir: Source directory
            target_dir: Target directory
        """
        target_dir.mkdir(parents=True, exist_ok=True)

        for item in source_dir.iterdir():
            dest = target_dir / item.name

            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()

            shutil.move(str(item), str(dest))

        # Remove empty source directory if it's not the target
        if source_dir != target_dir and not any(source_dir.iterdir()):
            source_dir.rmdir()


# ============================================================================
# Extraction Worker Thread
# ============================================================================

class ExtractionWorker(QThread):
    """Worker thread for extracting archives without blocking UI."""

    extraction_started = Signal(str)  # mod_id
    extraction_completed = Signal(str)  # mod_id
    extraction_error = Signal(str, str)  # mod_id, error_message
    all_completed = Signal()

    def __init__(self, extractions: list[ExtractionInfo]):
        """Initialize worker.

        Args:
            extractions: List of extractions to perform
        """
        super().__init__()
        self._extractions = extractions
        self._is_cancelled = False
        self._extractor = ArchiveExtractor()
        self._validator = StructureValidator()

    def run(self) -> None:
        """Run extraction process."""
        try:
            for idx, extraction_info in enumerate(self._extractions):
                if self._is_cancelled:
                    break

                mod_id = extraction_info.mod_id

                try:
                    self.extraction_started.emit(mod_id)
                    success = self._extractor.extract_archive(
                        extraction_info.archive_path,
                        extraction_info.destination_path
                    )

                    if not success:
                        self.extraction_error.emit(
                            mod_id,
                            tr("page.extraction.error_extraction_failed")
                        )
                        continue

                    # Validate structure immediately after extraction
                    valid, tp2_path = self._validator.validate_structure(
                        extraction_info.destination_path,
                        extraction_info.tp2_name
                    )

                    # Fix structure if needed
                    if not valid:
                        fixed = self._validator.fix_structure(
                            extraction_info.destination_path,
                            extraction_info.tp2_name
                        )

                        if not fixed:
                            self.extraction_error.emit(
                                mod_id,
                                tr("page.extraction.error_structure_invalid")
                            )
                            continue

                    self.extraction_completed.emit(mod_id)

                except Exception as e:
                    logger.error(f"Error extracting {mod_id}: {e}")
                    self.extraction_error.emit(mod_id, str(e))

            self.all_completed.emit()

        except Exception as e:
            logger.error(f"Critical error in extraction thread: {e}")
            self.all_completed.emit()

    def cancel(self) -> None:
        """Cancel extraction process."""
        self._is_cancelled = True


# ============================================================================
# Extraction Page
# ============================================================================

class ExtractionPage(BasePage):
    """Page for extracting mod archives."""

    STATUS_COLORS = {
        ExtractionStatus.TO_EXTRACT: QColor(COLOR_STATUS_NONE),
        ExtractionStatus.EXTRACTING: QColor(COLOR_WARNING),
        ExtractionStatus.EXTRACTED: QColor(COLOR_STATUS_COMPLETE),
        ExtractionStatus.ERROR: QColor(COLOR_ERROR),
    }

    def __init__(self, state_manager: StateManager):
        """Initialize extraction page.

        Args:
            state_manager: Application state manager
        """
        super().__init__(state_manager)

        self._mod_manager = self.state_manager.get_mod_manager()
        self._download_path = Path(self.state_manager.get_download_folder())

        # Extraction tracking
        self._extractions: dict[str, ExtractionInfo] = {}
        self._extraction_status: dict[str, ExtractionStatus] = {}

        # Operation tracking
        self._is_extracting = False
        self._extraction_worker: ExtractionWorker | None = None

        # UI components
        self._extraction_table: QTableWidget | None = None
        self._filter_combo: QComboBox | None = None
        self._btn_extract_all: QPushButton | None = None
        self._progress_bar: QProgressBar | None = None

        self._create_widgets()
        self._create_additional_buttons()

        logger.info("ExtractionPage initialized")

    def _create_widgets(self) -> None:
        """Create page UI layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(
            MARGIN_STANDARD, MARGIN_STANDARD,
            MARGIN_STANDARD, MARGIN_STANDARD
        )

        # Header
        header_layout = QHBoxLayout()
        header_layout.setSpacing(SPACING_SMALL)

        self._title_label = self._create_section_title()
        header_layout.addWidget(self._title_label)
        header_layout.addStretch()

        filter_label = QLabel()
        header_layout.addWidget(filter_label)
        self._filter_label = filter_label

        self._filter_combo = QComboBox()
        self._filter_combo.currentIndexChanged.connect(self._apply_filter)
        header_layout.addWidget(self._filter_combo)

        layout.addLayout(header_layout)

        # Extraction table
        self._extraction_table = QTableWidget()
        self._extraction_table.setColumnCount(COLUMN_COUNT)
        self._extraction_table.setAlternatingRowColors(True)
        self._extraction_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._extraction_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._extraction_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._extraction_table.verticalHeader().setVisible(False)
        self._extraction_table.setSortingEnabled(True)

        header = self._extraction_table.horizontalHeader()
        header.setSectionResizeMode(COL_MOD_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_ARCHIVE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_DESTINATION, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionsClickable(True)

        layout.addWidget(self._extraction_table, stretch=1)

        # Progress bar
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

    def _create_additional_buttons(self) -> None:
        self._btn_extract_all = QPushButton()
        self._btn_extract_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_extract_all.clicked.connect(self._extract_all)

    # ========================================
    # Extraction Loading
    # ========================================

    def _load_extractions(self) -> None:
        """Load extraction information from selected mods."""
        self._extractions.clear()
        self._extraction_status.clear()

        selected = self.state_manager.get_selected_components()
        if not selected:
            logger.warning("No components selected")
            return

        # Get game definition and sequences
        selected_game = self.state_manager.get_selected_game()
        game_manager = self.state_manager.get_game_manager()
        game_def = game_manager.get(selected_game)

        if not game_def:
            logger.error(f"Game definition not found: {selected_game}")
            return

        # Get game folder paths from state manager

        saved_folders = self.state_manager.get_game_folders()
        game_manager = self.state_manager.get_game_manager()
        game_folders = {}
        for seq_idx, sequence in enumerate(game_def.sequences):
            game = game_manager.get(sequence.game)
            for folder_key, path in saved_folders.items():

                if folder_key in game.get_folder_keys():
                    game_folders[seq_idx] = (sequence, Path(path))

        if not game_folders:
            logger.warning("No game folders configured")
            return

        unique_mods = set(selected.keys())

        for mod_id in unique_mods:
            mod = self._mod_manager.get_mod_by_id(mod_id)
            if not mod or not hasattr(mod, 'file') or not mod.file:
                continue

            archive_path = self._download_path / mod.file.filename
            if not archive_path.exists():
                logger.warning(f"Archive not found for {mod_id}: {archive_path}")
                continue

            # Create extraction info for each applicable sequence
            for seq_idx, (sequence, folder_path) in game_folders.items():
                # Check if mod is allowed in this sequence
                if not sequence.is_mod_allowed(mod_id):
                    logger.debug(f"Mod {mod_id} not allowed in sequence {seq_idx}")
                    continue

                # Create unique extraction ID
                extraction_id = f"{mod_id}_seq{seq_idx}"

                # Determine display name based on number of sequences
                if game_def.has_multiple_sequences:
                    seq_name = sequence.game or f"Sequence {seq_idx + 1}"
                    display_name = f"{mod.name} ({seq_name})"
                else:
                    display_name = mod.name

                extraction_info = ExtractionInfo(
                    mod_id=extraction_id,
                    mod_name=display_name,
                    tp2_name=mod.tp2,
                    archive_path=archive_path,
                    destination_path=folder_path
                )

                self._extractions[extraction_id] = extraction_info
                self._extraction_status[extraction_id] = ExtractionStatus.TO_EXTRACT

        self._refresh_extraction_table()

        # Check which mods are already extracted
        self._check_existing_extractions()

        logger.info(f"Extractions loaded: {len(self._extractions)}")

    def _refresh_extraction_table(self) -> None:
        """Refresh the extraction table display."""
        if not self._extraction_table:
            return

        # Disable sorting during update
        self._extraction_table.setSortingEnabled(False)
        self._extraction_table.setRowCount(0)

        filter_status = self._filter_combo.currentData() if self._filter_combo else None

        row = 0
        for extraction_id, extraction_info in self._extractions.items():
            status = self._extraction_status.get(extraction_id, ExtractionStatus.TO_EXTRACT)

            if filter_status and status != filter_status:
                continue

            self._extraction_table.insertRow(row)

            # Column 0: Mod name (sortable)
            name_item = QTableWidgetItem(extraction_info.mod_name)
            name_item.setData(Qt.ItemDataRole.UserRole, extraction_id)
            self._extraction_table.setItem(row, COL_MOD_NAME, name_item)

            # Column 1: Archive (sortable)
            archive_item = QTableWidgetItem(extraction_info.archive_path.name)
            self._extraction_table.setItem(row, COL_ARCHIVE, archive_item)

            # Column 2: Destination (sortable)
            dest_item = QTableWidgetItem(str(extraction_info.destination_path))
            self._extraction_table.setItem(row, COL_DESTINATION, dest_item)

            # Column 3: Status (sortable by status enum order)
            status_text = tr(f"page.extraction.status.{status.value}")
            status_item = QTableWidgetItem(status_text)
            # Store status value for sorting
            status_item.setData(Qt.ItemDataRole.UserRole, status.value)
            color = self.STATUS_COLORS.get(status, QColor("#000000"))
            status_item.setForeground(color)

            # Add error tooltip if applicable
            if extraction_info.error_message:
                status_item.setToolTip(extraction_info.error_message)

            self._extraction_table.setItem(row, COL_STATUS, status_item)

            row += 1

        self._extraction_table.setSortingEnabled(True)
        self._extraction_table.sortItems(COL_MOD_NAME, Qt.SortOrder.AscendingOrder)

    # ========================================
    # Verification
    # ========================================

    def _check_existing_extractions(self) -> None:
        """Check which extractions already exist on disk."""
        validator = StructureValidator()

        for extraction_id, extraction_info in self._extractions.items():
            valid, tp2_path = validator.validate_structure(
                extraction_info.destination_path,
                extraction_info.tp2_name
            )

            if valid:
                self._update_extraction_status(extraction_id, ExtractionStatus.EXTRACTED)

        logger.info(f"Checked existing extractions")

    # ========================================
    # Extraction
    # ========================================

    def _extract_all(self) -> None:
        """Start extraction of all pending archives."""
        if self._is_extracting:
            return

        to_extract = [
            info for extraction_id, info in self._extractions.items()
            if self._extraction_status.get(extraction_id, ExtractionStatus.TO_EXTRACT).needs_extraction
        ]

        if not to_extract:
            QMessageBox.information(
                self,
                tr("page.extraction.no_extraction_title"),
                tr("page.extraction.no_extraction_message")
            )
            return

        self._is_extracting = True
        self._update_navigation_buttons()

        self._progress_bar.setVisible(True)
        self._progress_bar.setMaximum(len(to_extract))
        self._progress_bar.setValue(0)

        self._extraction_worker = ExtractionWorker(to_extract)
        self._extraction_worker.extraction_started.connect(self._on_extraction_started)
        self._extraction_worker.extraction_completed.connect(self._on_extraction_completed)
        self._extraction_worker.extraction_error.connect(self._on_extraction_error)
        self._extraction_worker.all_completed.connect(self._on_all_extractions_completed)

        self._extraction_worker.start()

        logger.info(f"Started extraction of {len(to_extract)} archives")

    def _on_extraction_started(self, extraction_id: str) -> None:
        """Handle extraction start."""
        self._update_extraction_status(extraction_id, ExtractionStatus.EXTRACTING)
        logger.debug(f"Started extraction: {extraction_id}")

    def _on_extraction_completed(self, extraction_id: str) -> None:
        """Handle extraction completion."""
        self._update_extraction_status(extraction_id, ExtractionStatus.EXTRACTED)

        current = self._progress_bar.value()
        self._progress_bar.setValue(current + 1)

        logger.debug(f"Completed extraction: {extraction_id}")

    def _on_extraction_error(self, extraction_id: str, error_message: str) -> None:
        """Handle extraction error."""
        self._update_extraction_status(extraction_id, ExtractionStatus.ERROR, error_message)

        current = self._progress_bar.value()
        self._progress_bar.setValue(current + 1)

        logger.error(f"Extraction error for {extraction_id}: {error_message}")

    def _on_all_extractions_completed(self) -> None:
        """Handle completion of all extractions."""
        self._is_extracting = False
        self._extraction_worker = None
        self._progress_bar.setVisible(False)
        self._update_navigation_buttons()

        # Count successes and failures
        success_count = sum(
            1 for status in self._extraction_status.values()
            if status == ExtractionStatus.EXTRACTED
        )
        error_count = sum(
            1 for status in self._extraction_status.values()
            if status == ExtractionStatus.ERROR
        )

        if error_count > 0:
            QMessageBox.warning(
                self,
                tr("page.extraction.complete_with_errors_title"),
                tr("page.extraction.complete_with_errors_message",
                   success=success_count, errors=error_count)
            )
        else:
            QMessageBox.information(
                self,
                tr("page.extraction.complete_title"),
                tr("page.extraction.complete_message", count=success_count)
            )

        logger.info(f"Extraction completed: {success_count} success, {error_count} errors")

    # ========================================
    # UI Actions
    # ========================================

    def _update_extraction_status(self, extraction_id: str, status: ExtractionStatus,
                                  error_message: str | None = None) -> None:
        """Update status of a specific extraction in the table.

        Args:
            extraction_id: ID of the extraction to update
            status: New status
            error_message: Optional error message
        """
        # Update internal state
        self._extraction_status[extraction_id] = status

        if error_message and extraction_id in self._extractions:
            self._extractions[extraction_id].error_message = error_message

        # Find the row in the table
        for row in range(self._extraction_table.rowCount()):
            item = self._extraction_table.item(row, COL_MOD_NAME)
            if item and item.data(Qt.ItemDataRole.UserRole) == extraction_id:
                # Update status cell
                status_text = tr(f"page.extraction.status.{status.value}")
                status_item = self._extraction_table.item(row, COL_STATUS)

                if status_item:
                    status_item.setText(status_text)
                    status_item.setData(Qt.ItemDataRole.UserRole, status.value)

                    color = self.STATUS_COLORS.get(status, QColor("#000000"))
                    status_item.setForeground(color)

                    # Update tooltip if error
                    if error_message:
                        status_item.setToolTip(error_message)
                    else:
                        status_item.setToolTip("")

                break

    def _apply_filter(self) -> None:
        """Apply status filter to extraction table."""
        self._refresh_extraction_table()

    def _update_navigation_buttons(self) -> None:
        """Update navigation button states."""
        can_operate = not self._is_extracting
        self._btn_extract_all.setEnabled(can_operate)

        # Disable filter combo during extraction
        self._filter_combo.setEnabled(can_operate)

        self.notify_navigation_changed()

    # ========================================
    # BasePage Implementation
    # ========================================

    def get_page_id(self) -> str:
        """Get unique page identifier."""
        return "extraction"

    def get_page_title(self) -> str:
        """Get page title for display."""
        return tr("page.extraction.title")

    def get_additional_buttons(self) -> list[QPushButton]:
        """Get additional buttons."""
        return [self._btn_extract_all]

    def can_proceed(self) -> bool:
        """Check if can proceed to next page."""
        if self._is_extracting:
            return False

        # If no extractions loaded, cannot proceed
        if not self._extractions:
            return False

        # All extractions must be extracted successfully
        return all(
            status == ExtractionStatus.EXTRACTED
            for status in self._extraction_status.values()
        )

    def on_page_shown(self) -> None:
        """Called when page becomes visible."""
        super().on_page_shown()
        self._download_path = Path(self.state_manager.get_download_folder())
        self._load_extractions()

    def on_page_hidden(self) -> None:
        """Called when page becomes hidden."""
        super().on_page_hidden()

        if self._extraction_worker and self._extraction_worker.isRunning():
            self._extraction_worker.cancel()
            self._extraction_worker.wait()

    def retranslate_ui(self) -> None:
        """Update UI text for language change."""
        self._title_label.setText(tr("page.extraction.title"))
        self._filter_label.setText(tr("page.extraction.filter_label"))
        self._btn_extract_all.setText(tr("page.extraction.btn_extract_all"))

        # Update filter combo
        current = self._filter_combo.currentData()
        self._filter_combo.blockSignals(True)
        self._filter_combo.clear()
        self._filter_combo.addItem(tr("page.extraction.filter.all"), None)
        self._filter_combo.addItem(
            tr("page.extraction.filter.to_extract"),
            ExtractionStatus.TO_EXTRACT
        )
        self._filter_combo.addItem(
            tr("page.extraction.filter.extracted"),
            ExtractionStatus.EXTRACTED
        )
        self._filter_combo.addItem(
            tr("page.extraction.filter.error"),
            ExtractionStatus.ERROR
        )

        for i in range(self._filter_combo.count()):
            if self._filter_combo.itemData(i) == current:
                self._filter_combo.setCurrentIndex(i)
                break
        self._filter_combo.blockSignals(False)

        # Update table headers
        self._extraction_table.setHorizontalHeaderLabels([
            tr("page.extraction.col_mod_name"),
            tr("page.extraction.col_archive"),
            tr("page.extraction.col_destination"),
            tr("page.extraction.col_status")
        ])

        self._refresh_extraction_table()
