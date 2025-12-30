"""
InstallOrderPage - Page for managing component installation order.

This module provides an interface for organizing mod installation order
with drag-and-drop support, automatic ordering, and validation rules.
Supports EET dual-sequence installation (BG1 and BG2 phases).
"""

from __future__ import annotations

from dataclasses import dataclass
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from constants import (
    COLOR_BACKGROUND_ERROR,
    COLOR_BACKGROUND_WARNING,
    ICON_ERROR,
    ICON_WARNING,
    MARGIN_SMALL,
    MARGIN_STANDARD,
    ROLE_COMPONENT,
    SPACING_MEDIUM,
    SPACING_SMALL,
)
from core.ComponentReference import ComponentReference
from core.GameModels import GameDefinition
from core.models.PauseEntry import PAUSE_PREFIX, PauseEntry
from core.OrderGenerator import OrderGenerator
from core.OrderImportExportManager import OrderImportError, OrderImportExportManager
from core.StateManager import StateManager
from core.TranslationManager import tr
from core.WeiDULogParser import WeiDULogParser
from ui.pages.BasePage import BasePage
from ui.pages.install_order.DraggableTable import DraggableTableWidget
from ui.pages.install_order.OrderTableWidget import OrderTableWidget
from ui.pages.install_order.PauseDescriptionDialog import PauseDescriptionDialog

logger = logging.getLogger(__name__)

# ============================================================================
# Constants
# ============================================================================

# Ordered table columns
COL_ORDERED_MOD = 0
COL_ORDERED_COMPONENT = 1
ORDERED_COLUMN_COUNT = 2

# Unordered table columns
COL_UNORDERED_MOD = 0
COL_UNORDERED_COMPONENT = 1
UNORDERED_COLUMN_COUNT = 2


# ============================================================================
# Validation System
# ============================================================================


@dataclass
class ComponentIssue:
    """Single validation issue for a component.

    Attributes:
        reference: Component reference
        is_error: True for errors, False for warnings
    """

    reference: ComponentReference
    is_error: bool


class ValidationResult:
    """Result of order validation with warnings and errors.

    Tracks validation issues per component and provides aggregated
    statistics for UI display.
    """

    # Color constants for validation
    COLOR_VALID = QColor("transparent")
    COLOR_WARNING = QColor(COLOR_BACKGROUND_WARNING)
    COLOR_ERROR = QColor(COLOR_BACKGROUND_ERROR)

    def __init__(self):
        self._issues: list[ComponentIssue] = []

    def add_warning(self, reference: ComponentReference) -> None:
        """Add a warning for a component.

        Args:
            reference: Component reference
        """
        self._issues.append(ComponentIssue(reference, is_error=False))

    def add_error(self, reference: ComponentReference) -> None:
        """Add an error for a component.

        Args:
            reference: Component reference
        """
        self._issues.append(ComponentIssue(reference, is_error=True))

    @property
    def is_valid(self) -> bool:
        """Check if order is valid (no errors).

        Returns:
            True if no errors exist, False otherwise
        """
        return not any(issue.is_error for issue in self._issues)

    @property
    def has_errors(self) -> bool:
        """Check if order has errors.

        Returns:
            True if errors exist, False otherwise
        """
        return any(issue.is_error for issue in self._issues)

    @property
    def has_warnings(self) -> bool:
        """Check if order has warnings.

        Returns:
            True if warnings exist, False otherwise
        """
        return any(not issue.is_error for issue in self._issues)

    def get_component_issues(self, reference: ComponentReference) -> list[ComponentIssue]:
        """Get all issues for a specific component.

        Args:
            reference: Component reference

        Returns:
            List of issues for the component
        """
        return [issue for issue in self._issues if issue.reference == reference]

    def get_component_indicator(self, reference: ComponentReference) -> tuple[QColor, str]:
        """Get color for a component based on its issues.

        Args:
            reference: Component reference

        Returns:
            Color to use for component display
        """
        issues = self.get_component_issues(reference)
        if not issues:
            return self.COLOR_VALID, ""

        has_error = any(issue.is_error for issue in issues)
        return (
            self.COLOR_ERROR if has_error else self.COLOR_WARNING,
            ICON_ERROR if has_error else ICON_WARNING,
        )

    def clear(self) -> None:
        """Clear all validation issues."""
        self._issues.clear()


# ============================================================================
# Sequence Data Model
# ============================================================================


@dataclass
class SequenceData:
    """Data model for a single installation sequence.

    Attributes:
        ordered: Components in installation order [(mod_id, comp_key), ...]
        unordered: Components not yet ordered [(mod_id, comp_key), ...]
        validation: Validation result for this sequence
    """

    ordered: list[ComponentReference]
    unordered: list[ComponentReference]
    validation: ValidationResult

    @property
    def total_count(self) -> int:
        """Get total number of components.

        Returns:
            Sum of ordered and unordered components
        """
        return len(self.ordered) + len(self.unordered)

    @property
    def is_complete(self) -> bool:
        """Check if all components are ordered.

        Returns:
            True if unordered list is empty, False otherwise
        """
        return len(self.unordered) == 0


# ============================================================================
# Install Order Page
# ============================================================================


class InstallOrderPage(BasePage):
    """Page for managing component installation order.

    Features:
    - Dynamic sequence handling (single or multiple phases)
    - Drag-and-drop reordering with multi-select
    - Automatic ordering from game definition
    - Import/export order
    - WeiDU.log parsing
    - Order validation with rules
    """

    def __init__(self, state_manager: StateManager) -> None:
        """Initialize install order page.

        Args:
            state_manager: State manager instance
        """
        super().__init__(state_manager)

        self._mod_manager = self.state_manager.get_mod_manager()
        self._game_manager = self.state_manager.get_game_manager()
        self._rule_manager = self.state_manager.get_rule_manager()
        self._import_export_manager = OrderImportExportManager(WeiDULogParser())
        self._order_generator = OrderGenerator(self._rule_manager)

        # Game state
        self._current_game: str | None = None
        self._game_def: GameDefinition | None = None

        # Sequence data
        self._sequences_data: dict[int, SequenceData] = {}
        self._current_sequence_idx = 0

        # Validation
        self._ignore_warnings = False
        self._ignore_errors = False

        # Widget containers
        self._main_container: QWidget | None = None
        self._main_layout: QVBoxLayout | None = None
        self._phase_tabs: QTabWidget | None = None
        self._ordered_tables: dict[int, dict] = {}
        self._unordered_tables: dict[int, dict] = {}

        # Action buttons
        self._btn_default: QPushButton | None = None
        self._btn_import: QToolButton | None = None
        self._btn_export: QPushButton | None = None
        self._btn_reset: QPushButton | None = None
        self._action_import_file: QAction | None = None
        self._action_import_weidu: QAction | None = None
        self._chk_ignore_warnings: QCheckBox | None = None
        self._chk_ignore_errors: QCheckBox | None = None
        self._btn_add_pause: list[QToolButton] = []

        self._create_widgets()
        self._create_additional_buttons()

        logger.info("InstallOrderPage initialized")

    # ========================================
    # Widget Creation
    # ========================================

    def _create_widgets(self) -> None:
        """Create page UI layout."""
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACING_MEDIUM)
        layout.setContentsMargins(
            MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD, MARGIN_STANDARD
        )

        # Main container
        self._main_container = QWidget()
        self._main_layout = QVBoxLayout(self._main_container)
        self._main_layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._main_container, stretch=1)

        # Ignore warnings checkbox
        self._chk_ignore_warnings = QCheckBox()
        self._chk_ignore_warnings.stateChanged.connect(self._on_ignore_warnings_changed)
        # Ignore errors checkbox
        self._chk_ignore_errors = QCheckBox()
        self._chk_ignore_errors.stateChanged.connect(self._on_ignore_errors_changed)

    def _create_additional_buttons(self):
        """Create action buttons row.

        Returns:
            Widget containing action buttons
        """
        # Reset order
        self._btn_reset = QPushButton()
        self._btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_reset.clicked.connect(self._reset_order)

        # Load default order
        self._btn_default = QPushButton()
        self._btn_default.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_default.clicked.connect(self._import_order_default)

        # Import order
        self._btn_import = QToolButton()
        self._btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_import.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        menu = QMenu(self._btn_import)

        self._action_import_file = QAction("", self._btn_import)
        self._action_import_file.triggered.connect(self._import_order_file)
        self._action_import_weidu = QAction("", self._btn_import)
        self._action_import_weidu.triggered.connect(self._import_order_weidu)

        menu.addAction(self._action_import_file)
        menu.addAction(self._action_import_weidu)

        self._btn_import.setMenu(menu)
        self._btn_import.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)

        # Export order
        self._btn_export = QPushButton()
        self._btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_export.clicked.connect(self._export_order)

    def _rebuild_ui_for_game(self) -> None:
        """Rebuild UI based on current game configuration."""
        self._clear_main_layout()
        self._reset_widget_references()

        selected_game = self.state_manager.get_selected_game()
        self._game_def = self._game_manager.get(selected_game)

        if not self._game_def:
            logger.error(f"Game definition not found: {selected_game}")
            return

        logger.info(
            f"Rebuilding UI for {selected_game}: {self._game_def.sequence_count} sequence(s)"
        )

        # Create UI based on sequence count
        if self._game_def.has_multiple_sequences:
            content = self._create_multi_sequence_tabs()
            self._main_layout.addWidget(content)
        else:
            layout = QVBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)

            # Widget du haut (minimum)
            checkbox_widget = self._create_checkboxs_widget()
            checkbox_widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            layout.addWidget(checkbox_widget)

            # Widget du bas (stretch + expand)
            content = self._create_single_sequence_panel(0)
            content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            layout.addWidget(content)
            self._main_layout.addLayout(layout)

    def _clear_main_layout(self) -> None:
        """Clear all widgets from main layout."""
        while self._main_layout.count():
            item = self._main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _reset_widget_references(self) -> None:
        """Reset all widget reference dictionaries."""
        self._ordered_tables.clear()
        self._unordered_tables.clear()
        self._btn_add_pause.clear()
        self._phase_tabs = None

    def _create_checkboxs_widget(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_SMALL)
        layout.addStretch()
        layout.addWidget(self._chk_ignore_warnings)
        layout.addWidget(self._chk_ignore_errors)

        return container

    def _create_multi_sequence_tabs(self) -> QWidget:
        """Create tabbed interface for multiple sequences.

        Returns:
            Tab widget containing all sequences
        """
        tabs = QTabWidget()
        tabs.setCornerWidget(self._create_checkboxs_widget())

        for seq_idx, sequence in enumerate(self._game_def.sequences):
            panel = self._create_single_sequence_panel(seq_idx)
            game_name = self._game_manager.get(sequence.game).name
            tab_name = tr("page.order.phase_tab", name=game_name)
            tabs.addTab(panel, tab_name)

        tabs.currentChanged.connect(self._on_sequence_tab_changed)
        self._phase_tabs = tabs

        return tabs

    def _create_single_sequence_panel(self, seq_idx: int) -> QWidget:
        """Create panel for a single installation sequence.

        Args:
            seq_idx: Sequence index

        Returns:
            Widget containing the sequence panel
        """
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_panel = self._create_ordered_panel(seq_idx)
        right_panel = self._create_unordered_panel(seq_idx)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        return splitter

    def _create_ordered_panel(self, seq_idx: int) -> QWidget:
        """Create left panel with ordered components.

        Args:
            seq_idx: Sequence index

        Returns:
            Panel widget
        """
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(panel)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(MARGIN_SMALL, MARGIN_SMALL, MARGIN_SMALL, MARGIN_SMALL)

        header_layout = QHBoxLayout()

        # Title
        title = self._create_section_title()
        header_layout.addWidget(title)

        # Add pause button
        btn_add_pause = QToolButton()
        btn_add_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add_pause.clicked.connect(lambda: self._add_pause_to_sequence(seq_idx))
        header_layout.addWidget(btn_add_pause)
        self._btn_add_pause.append(btn_add_pause)

        layout.addLayout(header_layout)

        # Table widget
        table = OrderTableWidget(
            self._rule_manager,
            self._mod_manager,
            column_count=ORDERED_COLUMN_COUNT,
            accept_from_other=True,
            table_role="ordered",
        )

        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setHorizontalHeaderLabels(
            [tr("page.order.col_mod"), tr("page.order.col_component")]
        )

        header = table.horizontalHeader()
        header.setSectionResizeMode(COL_ORDERED_MOD, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_ORDERED_COMPONENT, QHeaderView.ResizeMode.Stretch)

        table.orderChanged.connect(lambda: self._on_order_changed(seq_idx))
        layout.addWidget(table)

        # Store references
        self._ordered_tables[seq_idx] = {
            "title": title,
            "table": table,
            "btn_pause": btn_add_pause,
        }

        return panel

    def _create_unordered_panel(self, seq_idx: int) -> QWidget:
        """Create right panel with unordered components table.

        Args:
            seq_idx: Sequence index

        Returns:
            Panel widget
        """
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(panel)
        layout.setSpacing(SPACING_SMALL)
        layout.setContentsMargins(MARGIN_SMALL, MARGIN_SMALL, MARGIN_SMALL, MARGIN_SMALL)

        # Title
        title = self._create_section_title()
        layout.addWidget(title)

        # Table widget
        table = DraggableTableWidget(
            column_count=UNORDERED_COLUMN_COUNT, accept_from_other=True, table_role="unordered"
        )

        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setHorizontalHeaderLabels(
            [tr("page.order.col_mod"), tr("page.order.col_component")]
        )

        header = table.horizontalHeader()
        header.setSectionResizeMode(COL_UNORDERED_MOD, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_UNORDERED_COMPONENT, QHeaderView.ResizeMode.Stretch)

        table.orderChanged.connect(lambda: self._on_order_changed(seq_idx))
        table.itemDoubleClicked.connect(
            lambda item: self._on_unordered_double_click(seq_idx, item)
        )

        layout.addWidget(table)

        # Store references
        self._unordered_tables[seq_idx] = {"title": title, "table": table}

        return panel

    def _add_pause_to_sequence(self, seq_idx: int):
        if seq_idx not in self._ordered_tables:
            return

        dialog = PauseDescriptionDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        description = dialog.get_description()
        table = self._ordered_tables[seq_idx]["table"]

        selected = table.selectedItems()
        if selected:
            selected_rows = sorted(set(item.row() for item in selected))
            insert_row = selected_rows[-1] + 1
        else:
            insert_row = table.rowCount()

        pause = PauseEntry(description=description)

        table.blockSignals(True)
        try:
            self.insert_pause_to_ordered_table(table, insert_row, str(pause), True)
        finally:
            table.blockSignals(False)

        self._on_order_changed(seq_idx)

    @staticmethod
    def insert_pause_to_ordered_table(
        table: QTableWidget, row: int, pause_string: str, focus: bool = False
    ) -> None:
        table.insertRow(row)

        _, description = PauseEntry.parse(pause_string)

        pause_item = QTableWidgetItem(f"⏸ {tr('page.order.pause_label')}")
        pause_item.setData(ROLE_COMPONENT, ComponentReference.from_string(pause_string))

        table.setItem(row, COL_ORDERED_MOD, pause_item)

        desc_text = description if description else tr("page.order.pause_description")
        desc_item = QTableWidgetItem(desc_text)
        table.setItem(row, COL_ORDERED_COMPONENT, desc_item)

        if focus:
            table.clearSelection()
            table.selectRow(row)
            table.scrollTo(
                table.model().index(row, 0), QAbstractItemView.ScrollHint.PositionAtCenter
            )
            table.setFocus()

    # ========================================
    # Order Management
    # ========================================

    def _load_components(self) -> None:
        """Load selected components from state manager."""
        selected = self.state_manager.get_selected_components()
        if not selected:
            logger.warning("No components selected")
            return

        if not self._game_def:
            logger.error("No game definition available")
            return

        self._sequences_data.clear()

        # Initialize sequence data
        for seq_idx in range(self._game_def.sequence_count):
            self._sequences_data[seq_idx] = SequenceData(
                ordered=[], unordered=[], validation=ValidationResult()
            )

        # Distribute components to sequences
        for ref in selected:
            reference = ComponentReference.from_string(ref)
            if reference.mod_id not in ["weidu64", "weidu"] and reference.is_component():
                mod = self._mod_manager.get_mod_by_id(reference.mod_id)
                component = mod.get_component(reference.comp_key)
                if component:
                    self._place_component_in_sequences(reference)

        self._refresh_all_tables()

    def _place_component_in_sequences(self, reference: ComponentReference) -> None:
        """Place a component in allowed sequences.

        Args:
            reference: Component reference
        """
        # Extract simple key (before first dot for SUB components)
        simple_key = reference.get_base_component_key()
        mod_id = reference.mod_id
        placed = False

        for seq_idx, sequence in enumerate(self._game_def.sequences):
            if not sequence.is_mod_allowed(mod_id):
                continue

            if not sequence.is_component_allowed(mod_id, simple_key):
                continue

            self._sequences_data[seq_idx].unordered.append(reference)

            placed = True

        if not placed:
            logger.debug(f"Component not allowed in any sequence: {reference}")

    def _apply_order_from_list(self, seq_idx: int, order: list[ComponentReference]) -> None:
        """Apply order from a list of component IDs.

        Args:
            seq_idx: Sequence index
            order: List of component IDs in format "mod:comp"
        """
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data:
            return

        PauseEntry.reset_counter()

        pool = {
            reference: reference
            for reference in (seq_data.ordered + seq_data.unordered)
            if reference.mod_id != PAUSE_PREFIX
        }

        # Apply order
        new_ordered = []
        for reference in order:
            if PauseEntry.is_pause(str(reference)):
                new_ordered.append(reference)
                continue

            if reference in pool:
                new_ordered.append(pool[reference])
                del pool[reference]

        # Remaining components
        new_unordered = list(pool.values())

        seq_data.ordered = new_ordered
        seq_data.unordered = new_unordered

        self._refresh_sequence_tables(seq_idx)
        self._validate_sequence(seq_idx)

        logger.info(
            f"Sequence {seq_idx}: {len(new_ordered)} ordered "
            f"(including pauses), {len(new_unordered)} unordered"
        )

    def _load_default_order(self) -> None:
        """Load default order from game definition."""
        if not self._game_def:
            return

        selected_components = ComponentReference.from_string_list(
            self.state_manager.get_selected_components()
        )
        for seq_idx, sequence in enumerate(self._game_def.sequences):
            if seq_idx in self._sequences_data:
                base_order = [
                    ComponentReference.from_string(f"{step.mod.lower()}:{step.comp}")
                    for step in sequence.order
                    if not step.is_annotation and step.is_install
                ]
                order = self._order_generator.generate(selected_components, base_order)
                self._apply_order_from_list(seq_idx, order)

        logger.info("Loaded default order for all sequences")

    def _reset_order(self) -> None:
        """Reset order."""
        # Check if there's any current order to reset
        has_ordered_components = any(
            len(seq_data.ordered) > 0 for seq_data in self._sequences_data.values()
        )

        if has_ordered_components:
            # Ask for confirmation
            reply = QMessageBox.question(
                self,
                tr("page.order.reset_order_confirm_title"),
                tr("page.order.reset_order_confirm_message"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )

            if reply != QMessageBox.StandardButton.Yes:
                return

        for seq_idx, _ in enumerate(self._game_def.sequences):
            if seq_idx in self._sequences_data:
                self._apply_order_from_list(seq_idx, [])
        logger.info("Order reseted")

    def _import_order_default(self) -> None:
        # Check if there's any current order to overwrite
        has_ordered_components = any(
            len(seq_data.ordered) > 0 for seq_data in self._sequences_data.values()
        )

        if has_ordered_components:
            # Ask for confirmation
            reply = QMessageBox.question(
                self,
                tr("page.order.import_default_confirm_title"),
                tr("page.order.import_default_confirm_message"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )

            if reply != QMessageBox.StandardButton.Yes:
                return

        self._load_default_order()
        logger.info("Default order loaded")

    def _import_order_weidu(self) -> None:
        """Load order from WeiDU.log file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            tr("page.order.select_weidu_log"),
            "",
            "WeiDU Log (WeiDU.log);;Log Files (*.log)",
        )

        if not file_path:
            return

        try:
            imported_order_list = self._import_export_manager.import_from_weidu_log(file_path)
            self._apply_order_from_list(self._current_sequence_idx, imported_order_list)

            seq_data = self._sequences_data[self._current_sequence_idx]
            QMessageBox.information(
                self,
                tr("page.order.import_success_title"),
                tr(
                    "page.order.import_success_message",
                    ordered=len(seq_data.ordered),
                    unordered=len(seq_data.unordered),
                ),
            )
        except OrderImportError as e:
            logger.error(f"Error parsing WeiDU.log: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                tr("page.order.import_error_title"),
                tr("page.order.import_error_message", error=str(e)),
            )

    def _import_order_file(self) -> None:
        """Import order from JSON file."""
        if not self._game_def:
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            tr("page.order.import_select_file"),
            "",
            "JSON Files (*.json);;All Files (*.*)",
        )

        if not file_path:
            return

        try:
            imported_order = self._import_export_manager.import_from_json(file_path)

            for seq_idx, order_list in imported_order.items():
                if seq_idx in self._sequences_data:
                    self._apply_order_from_list(seq_idx, order_list)

            stats = self._import_export_manager.get_order_statistics(imported_order)

            QMessageBox.information(
                self,
                tr("page.order.import_success_title"),
                tr(
                    "page.order.import_success_message",
                    ordered=stats["total_components"],
                    unordered=sum(len(seq.unordered) for seq in self._sequences_data.values()),
                ),
            )

            logger.info(
                "Imported %d components and %d pauses from %s",
                stats["component_count"],
                stats["pause_count"],
                file_path,
            )
        except OrderImportError as e:
            logger.error(f"Import failed: {e}")
            QMessageBox.critical(
                self,
                tr("page.order.import_error_title"),
                tr("page.order.import_error_message", error=str(e)),
            )

    def _export_order(self) -> None:
        """Export current order to JSON file."""
        self.save_state()

        install_order_strings = self.state_manager.get_install_order()
        install_order = {
            seq_idx: ComponentReference.from_string_list(order_strings)
            for seq_idx, order_strings in install_order_strings.items()
        }
        stats = self._import_export_manager.get_order_statistics(install_order)

        if stats["total_components"] == 0:
            QMessageBox.warning(
                self,
                tr("page.order.export_empty_title"),
                tr("page.order.export_empty_message"),
            )
            return

        # Ask for save location
        selected_game = self.state_manager.get_selected_game()
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            tr("page.order.export_select_file"),
            f"{selected_game}_order.json",
            "JSON Files (*.json);;All Files (*.*)",
        )

        if not file_path:
            return

        if not file_path.endswith(".json"):
            file_path += ".json"

        try:
            self._import_export_manager.export_to_json(install_order, file_path)

            QMessageBox.information(
                self,
                tr("page.order.export_success_title"),
                tr(
                    "page.order.export_success_message",
                    count=stats["total_components"],
                    path=file_path,
                ),
            )
        except OrderImportError as e:
            logger.error(f"Export failed: {e}")
            QMessageBox.critical(
                self,
                tr("page.order.export_error_title"),
                tr("page.order.export_error_message", error=str(e)),
            )

    # ========================================
    # UI Updates
    # ========================================

    def _refresh_all_tables(self) -> None:
        """Refresh tables for all sequences."""
        for seq_idx in self._sequences_data.keys():
            self._refresh_sequence_tables(seq_idx)

    def _refresh_sequence_tables(self, seq_idx: int) -> None:
        """Refresh tables for a specific sequence.

        Args:
            seq_idx: Sequence index
        """
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data:
            return

        if seq_idx not in self._ordered_tables or seq_idx not in self._unordered_tables:
            return

        ordered_table = self._ordered_tables[seq_idx]["table"]
        unordered_table = self._unordered_tables[seq_idx]["table"]

        # Block signals during refresh
        ordered_table.blockSignals(True)
        unordered_table.blockSignals(True)

        try:
            # Clear tables
            ordered_table.setRowCount(0)
            unordered_table.setRowCount(0)

            # Populate ordered table (3 columns)
            for reference in seq_data.ordered:
                if reference.mod_id == PAUSE_PREFIX:
                    self.insert_pause_to_ordered_table(
                        ordered_table, ordered_table.rowCount(), str(reference)
                    )
                else:
                    self._add_row_to_ordered_table(ordered_table, reference)

            # Populate unordered table (2 columns)
            for reference in seq_data.unordered:
                self._add_row_to_unordered_table(unordered_table, reference)

        finally:
            ordered_table.blockSignals(False)
            unordered_table.blockSignals(False)

        self._update_sequence_counters(seq_idx)

    def _add_row_to_ordered_table(
        self, table: QTableWidget, reference: ComponentReference
    ) -> None:
        """Add a row to the ordered table."""
        row = table.rowCount()
        self.insert_row_to_ordered_table(table, row, reference)

    def _add_row_to_unordered_table(
        self, table: QTableWidget, reference: ComponentReference
    ) -> None:
        """Add a row to the unordered table."""
        row = table.rowCount()
        self.insert_row_to_unordered_table(table, row, reference)

    def _update_sequence_counters(self, seq_idx: int) -> None:
        """Update component counters for a sequence.

        Args:
            seq_idx: Sequence index
        """
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data:
            return

        if seq_idx not in self._ordered_tables or seq_idx not in self._unordered_tables:
            return

        ordered_count = len(seq_data.ordered)
        unordered_count = len(seq_data.unordered)
        total = seq_data.total_count

        self._ordered_tables[seq_idx]["title"].setText(
            tr("page.order.ordered_title", count=ordered_count, total=total)
        )
        self._unordered_tables[seq_idx]["title"].setText(
            tr("page.order.unordered_title", count=unordered_count)
        )

    # ========================================
    # Validation
    # ========================================

    def _validate_sequence(self, seq_idx: int) -> None:
        """Validate all sequences at once."""
        sequences = {}
        for seq_idx, seq_data in self._sequences_data.items():
            if seq_data.ordered:
                components_only = [
                    ref for ref in seq_data.ordered if ref.mod_id != PAUSE_PREFIX
                ]
                sequences[seq_idx] = components_only
            else:
                sequences[seq_idx] = []

        all_violations = self._rule_manager.validate_order(sequences)

        for seq_idx, violations in all_violations.items():
            seq_data = self._sequences_data.get(seq_idx)
            if not seq_data:
                continue

            seq_data.validation.clear()

            for violation in violations:
                for reference in violation.affected_components:
                    if violation.is_error:
                        seq_data.validation.add_error(reference)
                    elif violation.is_warning:
                        seq_data.validation.add_warning(reference)

            self._apply_visual_indicators(seq_idx)

        self.notify_navigation_changed()

    def _apply_visual_indicators(self, seq_idx: int) -> None:
        """Apply visual indicators to ordered table."""
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data or seq_idx not in self._ordered_tables:
            return

        ordered_table = self._ordered_tables[seq_idx]["table"]
        ordered_table.set_current_order(seq_data.ordered)

        for row in range(ordered_table.rowCount()):
            mod_item = ordered_table.item(row, COL_ORDERED_MOD)
            if not mod_item:
                continue

            reference = mod_item.data(ROLE_COMPONENT)
            violations = self._rule_manager.get_order_violations(reference)

            mod_item.setText(
                mod_item.text().replace(f"{ICON_ERROR} ", "").replace(f"{ICON_WARNING} ", "")
            )

            if violations:
                color, icon = seq_data.validation.get_component_indicator(reference)
                mod_item.setText(f"{icon} {mod_item.text()}")

                for col in range(ordered_table.columnCount()):
                    item = ordered_table.item(row, col)
                    if item:
                        item.setBackground(color)

            else:
                for col in range(ordered_table.columnCount()):
                    item = ordered_table.item(row, col)
                    if item:
                        item.setBackground(Qt.GlobalColor.transparent)

    # ========================================
    # Event Handlers
    # ========================================

    def _on_order_changed(self, seq_idx: int) -> None:
        """Handle order change in tables for a sequence.

        Args:
            seq_idx: Sequence index
        """
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data:
            return

        if seq_idx not in self._ordered_tables or seq_idx not in self._unordered_tables:
            return

        ordered_table = self._ordered_tables[seq_idx]["table"]
        unordered_table = self._unordered_tables[seq_idx]["table"]

        # Rebuild from tables
        seq_data.ordered = []
        for row in range(ordered_table.rowCount()):
            mod_item = ordered_table.item(row, COL_ORDERED_MOD)
            if mod_item:
                reference = mod_item.data(ROLE_COMPONENT)
                seq_data.ordered.append(reference)

        seq_data.unordered = []
        for row in range(unordered_table.rowCount()):
            mod_item = unordered_table.item(row, COL_UNORDERED_MOD)
            if mod_item:
                reference = mod_item.data(ROLE_COMPONENT)
                seq_data.unordered.append(reference)

        self._update_sequence_counters(seq_idx)
        self._validate_sequence(seq_idx)

    def _on_unordered_double_click(self, seq_idx: int, item: QTableWidgetItem) -> None:
        """Handle double-click on unordered item to move it to ordered table.

        Args:
            seq_idx: Sequence index
            item: Clicked table item
        """
        if seq_idx not in self._ordered_tables or seq_idx not in self._unordered_tables:
            return

        ordered_table = self._ordered_tables[seq_idx]["table"]
        unordered_table = self._unordered_tables[seq_idx]["table"]

        row = item.row()
        mod_item = unordered_table.item(row, COL_UNORDERED_MOD)
        if not mod_item:
            return

        reference = mod_item.data(ROLE_COMPONENT)

        # Determine target position in ordered table
        selected = ordered_table.selectedItems()
        if selected:
            selected_rows = sorted(set(item.row() for item in selected))
            target_row = selected_rows[-1] + 1
        else:
            target_row = ordered_table.rowCount()

        # Block signals
        ordered_table.blockSignals(True)
        unordered_table.blockSignals(True)

        try:
            # Add to ordered table
            self.insert_row_to_ordered_table(ordered_table, target_row, reference)

            # Remove from unordered table
            unordered_table.removeRow(row)

        finally:
            ordered_table.blockSignals(False)
            unordered_table.blockSignals(False)

        # Trigger update
        self._on_order_changed(seq_idx)

    def insert_row_to_ordered_table(
        self, table: QTableWidget, row: int, reference: ComponentReference
    ) -> None:
        """Insert a row at specific position in ordered table."""
        mod_id = reference.mod_id
        if mod_id == PAUSE_PREFIX:
            self.insert_pause_to_ordered_table(table, row, str(reference))
            return

        table.insertRow(row)

        comp_key = reference.comp_key
        mod = self._mod_manager.get_mod_by_id(mod_id)
        mod_name = mod.name if mod else mod_id
        comp_text = mod.get_component(comp_key).get_name()

        # Column 0: Mod name
        mod_item = QTableWidgetItem(f"[{mod.id}] {mod_name}")
        mod_item.setData(ROLE_COMPONENT, reference)
        table.setItem(row, COL_ORDERED_MOD, mod_item)

        # Column 1: Component
        comp_item = QTableWidgetItem(f"[{comp_key}] {comp_text}")
        table.setItem(row, COL_ORDERED_COMPONENT, comp_item)

    def insert_row_to_unordered_table(
        self, table: QTableWidget, row: int, reference: ComponentReference
    ) -> None:
        """Insert a row at specific position in unordered table."""
        table.insertRow(row)

        mod_id = reference.mod_id
        comp_key = reference.comp_key

        mod = self._mod_manager.get_mod_by_id(mod_id)
        mod_name = mod.name if mod else mod_id
        comp_text = mod.get_component(comp_key).get_name()

        # Column 0: Mod name
        mod_item = QTableWidgetItem(f"[{mod.id}] {mod_name}")
        mod_item.setData(ROLE_COMPONENT, reference)
        table.setItem(row, COL_UNORDERED_MOD, mod_item)

        # Column 1: Component text
        comp_item = QTableWidgetItem(f"[{comp_key}] {comp_text}")
        table.setItem(row, COL_UNORDERED_COMPONENT, comp_item)

    def _on_sequence_tab_changed(self, index: int) -> None:
        """Handle sequence tab change.

        Args:
            index: New tab index
        """
        if index >= 0:
            self._current_sequence_idx = index
            logger.debug(f"Sequence tab changed: {index}")

    def _on_ignore_warnings_changed(self, state: int) -> None:
        """Handle ignore warnings checkbox change.

        Args:
            state: Checkbox state
        """
        self._ignore_warnings = state == Qt.CheckState.Checked.value
        self.notify_navigation_changed()
        logger.debug(f"Ignore warnings: {self._ignore_warnings}")

    def _on_ignore_errors_changed(self, state: int) -> None:
        """Handle ignore errors checkbox change.

        Args:
            state: Checkbox state
        """
        self._ignore_errors = state == Qt.CheckState.Checked.value
        self.notify_navigation_changed()
        logger.debug(f"Ignore errors: {self._ignore_errors}")

    # ========================================
    # BasePage Implementation
    # ========================================

    def get_page_id(self) -> str:
        """Get page identifier.

        Returns:
            Page identifier string
        """
        return "install_order"

    def get_page_title(self) -> str:
        """Get page title.

        Returns:
            Translated page title
        """
        return tr("page.order.title")

    def get_additional_buttons(self) -> list[QPushButton]:
        """Get additional buttons."""
        return [self._btn_reset, self._btn_default, self._btn_import, self._btn_export]

    def can_go_to_next_page(self) -> bool:
        """Check if can proceed to next page.

        All sequences must have:
        - All components in ordered list
        - No validation errors
        - Warnings OK if ignored

        Returns:
            True if all conditions met, False otherwise
        """
        for seq_data in self._sequences_data.values():
            # Check all components are ordered
            if not seq_data.is_complete:
                return False

            # Check validation
            if seq_data.validation.has_errors and not self._ignore_errors:
                return False

            if seq_data.validation.has_warnings and not self._ignore_warnings:
                return False

        return True

    def on_page_shown(self) -> None:
        """Called when page becomes visible."""
        super().on_page_shown()

        selected_game = self.state_manager.get_selected_game()

        # Check if game has changed
        if selected_game != self._current_game:
            logger.info(f"Game changed: {self._current_game} → {selected_game}")
            self._current_game = selected_game

            # Rebuild UI for new game
            self._rebuild_ui_for_game()
            self.retranslate_ui()

        # Load components and default order
        self._load_components()

        if not self._restore_saved_order():
            self._load_default_order()

    def _restore_saved_order(self) -> bool:
        """Restore installation order from saved state.

        Attempts to restore the previously saved installation order.

        Returns:
            True if order was successfully restored, False if no saved order exists
        """
        install_order = self.state_manager.get_install_order()

        if not install_order:
            logger.debug("No saved installation order to restore")
            return False

        logger.info(f"Restoring saved installation order for {len(install_order)} sequence(s)")

        for seq_idx, order_list in install_order.items():
            if seq_idx in self._sequences_data:
                order_list = ComponentReference.from_string_list(order_list)
                self._apply_order_from_list(seq_idx, order_list)

        return True

    def retranslate_ui(self) -> None:
        """Update UI text for language change."""
        # Update buttons
        self._btn_default.setText(tr("page.order.btn_default"))
        self._btn_import.setText(tr("page.order.btn_import"))
        self._btn_export.setText(tr("page.order.btn_export"))
        self._btn_reset.setText(tr("page.order.btn_reset"))
        self._action_import_file.setText(tr("page.order.action_import_file"))
        self._action_import_weidu.setText(tr("page.order.action_import_weidu"))
        self._chk_ignore_warnings.setText(tr("page.order.ignore_warnings"))
        self._chk_ignore_errors.setText(tr("page.order.ignore_errors"))

        for btn_add_pause in self._btn_add_pause:
            btn_add_pause.setText(tr("page.order.btn_add_pause"))
            btn_add_pause.setToolTip(tr("page.order.pause_tooltip"))

        self._refresh_all_tables()

        # Update tab labels for multiple sequences
        if self._game_def and self._game_def.has_multiple_sequences and self._phase_tabs:
            for seq_idx in range(self._game_def.sequence_count):
                sequence = self._game_def.get_sequence(seq_idx)
                if sequence:
                    game_name = self._game_manager.get(sequence.game).name
                    tab_name = tr("page.order.phase_tab", name=game_name)
                    self._phase_tabs.setTabText(seq_idx, tab_name)

        # Update table headers
        for seq_idx in self._ordered_tables.keys():
            table = self._ordered_tables[seq_idx]["table"]
            table.setHorizontalHeaderLabels(
                [tr("page.order.col_mod"), tr("page.order.col_component")]
            )

        for seq_idx in self._unordered_tables.keys():
            table = self._unordered_tables[seq_idx]["table"]
            table.setHorizontalHeaderLabels(
                [tr("page.order.col_mod"), tr("page.order.col_component")]
            )

    def load_state(self) -> None:
        """Load state from state manager."""
        super().load_state()

        self._chk_ignore_errors.setChecked(
            self.state_manager.get_page_option(self.get_page_id(), "ignore_errors", False)
        )
        self._chk_ignore_warnings.setChecked(
            self.state_manager.get_page_option(self.get_page_id(), "ignore_warnings", False)
        )

    def save_state(self) -> None:
        """Save page data to state manager."""
        super().save_state()

        # Convert sequences data to state format
        install_order = {}
        for seq_idx, seq_data in self._sequences_data.items():
            install_order[seq_idx] = []

            for reference in seq_data.ordered:
                install_order[seq_idx].append(str(reference))

        self.state_manager.set_install_order(install_order)
        self.state_manager.set_page_option(
            self.get_page_id(), "ignore_errors", self._ignore_errors
        )
        self.state_manager.set_page_option(
            self.get_page_id(), "ignore_warnings", self._ignore_warnings
        )
