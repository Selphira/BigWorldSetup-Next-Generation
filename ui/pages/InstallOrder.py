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
    QLineEdit,
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
    COLOR_BLEND_BROAD_RULE_ROW,
    ICON_ERROR,
    ICON_WARNING,
    MARGIN_SMALL,
    MARGIN_STANDARD,
    ROLE_COMPONENT,
    ROLE_LABEL,
    SPACING_MEDIUM,
    SPACING_SMALL,
)
from core.ComponentReference import ComponentReference
from core.GameModels import GameDefinition
from core.models.PauseEntry import PAUSE_PREFIX, PauseEntry
from core.OrderImportExportManager import OrderImportError, OrderImportExportManager
from core.StateManager import StateManager
from core.TranslationManager import tr
from core.WeiDULogParser import WeiDULogParser
from ui.ColorHelpers import blend
from ui.layouts.LayoutHelpers import clear_layout
from ui.pages.BasePage import BasePage
from ui.pages.install_order.DraggableTable import DraggableTableWidget
from ui.pages.install_order.OrderTableWidget import OrderTableWidget
from ui.pages.install_order.OrderViolationPanel import OrderViolationPanel
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


class ValidationResult:
    """Order validation issues of one sequence, indexed by component reference.

    Errors and warnings are kept apart: a component flagged with both still
    counts as a warning, so ignoring errors alone does not hide it.
    """

    COLOR_VALID = QColor("transparent")
    COLOR_WARNING = QColor(COLOR_BACKGROUND_WARNING)
    COLOR_ERROR = QColor(COLOR_BACKGROUND_ERROR)

    def __init__(self) -> None:
        self._errors: set[ComponentReference] = set()
        self._warnings: set[ComponentReference] = set()

    def add_warning(self, reference: ComponentReference) -> None:
        self._warnings.add(reference)

    def add_error(self, reference: ComponentReference) -> None:
        self._errors.add(reference)

    @property
    def is_valid(self) -> bool:
        return not self.has_errors

    @property
    def has_errors(self) -> bool:
        return bool(self._errors)

    @property
    def has_warnings(self) -> bool:
        return bool(self._warnings)

    def get_component_indicator(self, reference: ComponentReference) -> tuple[QColor, str]:
        """Return the row background color and icon for a component."""
        if reference in self._errors:
            return self.COLOR_ERROR, ICON_ERROR
        if reference in self._warnings:
            return self.COLOR_WARNING, ICON_WARNING
        return self.COLOR_VALID, ""

    def clear(self) -> None:
        self._errors.clear()
        self._warnings.clear()


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

        # Game state
        self._current_game: str | None = None
        self._game_def: GameDefinition | None = None

        # Sequence data
        self._sequences_data: dict[int, SequenceData] = {}
        self._current_sequence_idx = 0
        self._missing_selection: list[str] = []

        # Validation
        self._ignore_warnings = False
        self._ignore_errors = False

        # Widget containers
        self._main_container: QWidget | None = None
        self._main_layout: QVBoxLayout | None = None
        self._phase_tabs: QTabWidget | None = None
        self._ordered_tables: dict[int, dict] = {}
        self._unordered_tables: dict[int, dict] = {}
        self._violation_panels: dict[int, OrderViolationPanel] = {}

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
        self._btn_reset = self._create_action_button(self._reset_order)

        # Load default order
        self._btn_default = self._create_action_button(self._import_order_default)

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
        self._btn_export = self._create_action_button(self._export_order)

    def _rebuild_ui_for_game(self) -> None:
        """Rebuild UI based on current game configuration."""
        clear_layout(self._main_layout)
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

    def _reset_widget_references(self) -> None:
        """Reset all widget reference dictionaries."""
        self._ordered_tables.clear()
        self._unordered_tables.clear()
        self._violation_panels.clear()
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

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
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

        table.itemSelectionChanged.connect(lambda: self._on_ordered_selection_changed(seq_idx))
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setHorizontalHeaderLabels(
            [tr("page.order.col_mod"), tr("page.order.col_component")]
        )

        header = table.horizontalHeader()
        header.setSectionsClickable(False)
        header.setSectionResizeMode(COL_ORDERED_MOD, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_ORDERED_COMPONENT, QHeaderView.ResizeMode.Stretch)

        table.orderChanged.connect(lambda moved: self._on_order_changed(seq_idx, moved))
        table.violationIgnored.connect(lambda: self._on_violation_ignored(seq_idx))
        search_widget = self._create_search_bar(seq_idx, table)
        layout.addWidget(search_widget)
        layout.addWidget(table)

        # Store references
        self._ordered_tables[seq_idx] = {
            "title": title,
            "table": table,
            "btn_pause": btn_add_pause,
            "search_input": None,
            "btn_prev": None,
            "btn_next": None,
            "search_results": [],
            "search_idx": -1,
        }

        if hasattr(self, "_pending_search_widgets") and seq_idx in self._pending_search_widgets:
            si, bp, bn = self._pending_search_widgets.pop(seq_idx)
            self._ordered_tables[seq_idx]["search_input"] = si
            self._ordered_tables[seq_idx]["btn_prev"] = bp
            self._ordered_tables[seq_idx]["btn_next"] = bn

        return panel

    def _create_search_bar(self, seq_idx: int, table) -> QWidget:
        """Create search bar with prev/next navigation."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        search_input = QLineEdit()
        search_input.setPlaceholderText(tr("page.order.search_placeholder"))
        search_input.setClearButtonEnabled(True)
        layout.addWidget(search_input, stretch=1)

        btn_prev = QToolButton()
        btn_prev.setArrowType(Qt.ArrowType.UpArrow)
        btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_prev.setToolTip(tr("page.order.search_prev"))
        btn_prev.setEnabled(False)
        layout.addWidget(btn_prev)

        btn_next = QToolButton()
        btn_next.setArrowType(Qt.ArrowType.DownArrow)
        btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_next.setToolTip(tr("page.order.search_next"))
        btn_next.setEnabled(False)
        layout.addWidget(btn_next)

        def on_text_changed(text):
            self._search_in_ordered_table(seq_idx, text)

        search_input.textChanged.connect(on_text_changed)
        btn_prev.clicked.connect(lambda: self._search_prev(seq_idx))
        btn_next.clicked.connect(lambda: self._search_next(seq_idx))

        search_input.setProperty("seq_idx", seq_idx)
        self._pending_search_widgets = getattr(self, "_pending_search_widgets", {})
        self._pending_search_widgets[seq_idx] = (search_input, btn_prev, btn_next)

        return container

    def _create_unordered_panel(self, seq_idx: int) -> QWidget:
        """Create right panel with unordered components table and violation panel below.

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

        table.orderChanged.connect(lambda moved: self._on_order_changed(seq_idx, moved))
        table.itemDoubleClicked.connect(
            lambda item: self._on_unordered_double_click(seq_idx, item)
        )
        layout.addWidget(table, stretch=1)

        violation_panel = OrderViolationPanel(self._mod_manager, self._rule_manager)
        layout.addWidget(violation_panel)

        # Store references
        self._unordered_tables[seq_idx] = {"title": title, "table": table}
        self._violation_panels[seq_idx] = violation_panel

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
            selected_rows = sorted({item.row() for item in selected})
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
        self._missing_selection = []
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
        missing = []
        for ref in selected:
            try:
                reference = ComponentReference.from_string(ref)
            except ValueError:
                missing.append(ref)
                continue
            if reference.mod_id not in ["weidu64", "weidu"] and reference.is_component():
                mod = self._mod_manager.get_mod_by_id(reference.mod_id)
                component = mod.get_component(reference.comp_key) if mod else None
                if not component or not self._place_component_in_sequences(reference):
                    missing.append(ref)

        self._missing_selection = missing
        if missing:
            QMessageBox.warning(
                self,
                tr("page.order.import_error_title"),
                tr("page.selection.missing_references", references=", ".join(missing)),
            )

        self._refresh_all_tables()

    def _place_component_in_sequences(self, reference: ComponentReference) -> bool:
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
            logger.warning(f"Component not allowed in any sequence: {reference}")
        return placed

    def _apply_order_from_list(self, seq_idx: int, order: list[ComponentReference]) -> None:
        """Apply order from a list of component IDs.

        Args:
            seq_idx: Sequence index
            order: List of component references
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
        for other in self._sequences_data:
            self._validate_sequence(other)

        logger.info(
            f"Sequence {seq_idx}: {len(new_ordered)} ordered "
            f"(including pauses), {len(new_unordered)} unordered"
        )

    def _load_default_order(self) -> None:
        """Load default order from game definition."""
        if not self._game_def:
            return

        for seq_idx, sequence in enumerate(self._game_def.sequences):
            if seq_idx in self._sequences_data:
                base_order = ComponentReference.from_string_list(list(sequence.order))
                seq_data = self._sequences_data[seq_idx]
                active = {
                    ref
                    for ref in seq_data.ordered + seq_data.unordered
                    if not PauseEntry.is_pause(str(ref))
                }
                editorial = [
                    ref for ref in base_order if ref in active or PauseEntry.is_pause(str(ref))
                ]
                earlier = {
                    ref
                    for idx, seq in self._sequences_data.items()
                    if idx < seq_idx
                    for ref in seq.ordered
                }
                selected = set(
                    ComponentReference.from_string_list(
                        self.state_manager.get_selected_components()
                    )
                )
                preferred = self._rule_manager.preferred_order(editorial, earlier, selected)
                self._apply_order_from_list(seq_idx, preferred.components)
                if preferred.cycle:
                    QMessageBox.warning(
                        self,
                        tr("page.order.title"),
                        tr("page.order.cycle", path=" → ".join(map(str, preferred.cycle))),
                    )

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

    def _confirm_importable_order(self, order: dict[int, list[ComponentReference]]) -> bool:
        """Reject incompatible orders before replacing any sequence."""
        unavailable = []
        for idx, refs in order.items():
            sequence = self._sequences_data.get(idx)
            pool = set(sequence.ordered + sequence.unordered) if sequence else set()
            for ref in refs:
                if sequence and PauseEntry.is_pause(str(ref)):
                    continue
                if ref in pool:
                    pool.remove(ref)
                else:
                    unavailable.append(f"{idx + 1} / {ref}")
        if unavailable:
            QMessageBox.warning(
                self,
                tr("page.order.import_error_title"),
                tr("page.selection.missing_references", references=", ".join(unavailable)),
            )
        return not unavailable

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
            if not self._confirm_importable_order(
                {self._current_sequence_idx: imported_order_list}
            ):
                return
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

            if not self._confirm_importable_order(imported_order):
                return

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
        for seq_idx in self._sequences_data:
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
                    self.insert_row_to_ordered_table(
                        ordered_table, ordered_table.rowCount(), reference
                    )

            # Populate unordered table (2 columns)
            for reference in seq_data.unordered:
                self.insert_row_to_unordered_table(
                    unordered_table, unordered_table.rowCount(), reference
                )

        finally:
            ordered_table.blockSignals(False)
            unordered_table.blockSignals(False)

        self._update_sequence_counters(seq_idx)

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

    def _validate_sequence(
        self, seq_idx: int, moved_components: list[ComponentReference] | None = None
    ) -> None:
        """Validate a sequence."""
        sequence = self._sequences_data.get(seq_idx)
        if not sequence:
            return

        moved_components = set(moved_components) if moved_components else None
        components = []
        if sequence.ordered:
            components = [ref for ref in sequence.ordered if ref.mod_id != PAUSE_PREFIX]

        violations = self._rule_manager.validate_order(
            self._current_game,
            seq_idx,
            components,
            moved_components,
            earlier={
                ref
                for idx, seq in self._sequences_data.items()
                if idx < seq_idx
                for ref in seq.ordered
            },
            later={
                ref
                for idx, seq in self._sequences_data.items()
                if idx > seq_idx
                for ref in seq.ordered
            },
            selection=set(
                ComponentReference.from_string_list(
                    self.state_manager.get_selected_components()
                )
            ),
        )

        sequence.validation.clear()

        for violation in violations:
            for reference in violation.affected_components:
                if violation.is_error:
                    sequence.validation.add_error(reference)
                elif violation.is_warning:
                    sequence.validation.add_warning(reference)

        self._apply_visual_indicators(seq_idx)

        if seq_idx in self._ordered_tables:
            self._ordered_tables[seq_idx]["table"].refresh_ignores()

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

            if not ordered_table.has_new_violations(reference, violations):
                self._set_status_icon(mod_item, "")
                self._paint_row(ordered_table, row, Qt.GlobalColor.transparent)
                continue

            color, icon = seq_data.validation.get_component_indicator(reference)
            if all(v.is_broad_rule() for v in violations):
                color = blend(color, COLOR_BLEND_BROAD_RULE_ROW)

            self._set_status_icon(mod_item, icon)
            self._paint_row(ordered_table, row, color)

    @staticmethod
    def _set_status_icon(mod_item: QTableWidgetItem, icon: str) -> None:
        """Prefix the item label with a status icon, or restore the bare label."""
        label = mod_item.data(ROLE_LABEL)
        if label is None:
            return
        mod_item.setText(f"{icon} {label}" if icon else label)

    @staticmethod
    def _paint_row(table: QTableWidget, row: int, color: QColor | Qt.GlobalColor) -> None:
        """Set the background color of every cell in a row."""
        for col in range(table.columnCount()):
            item = table.item(row, col)
            if item:
                item.setBackground(color)

    # ========================================
    # Event Handlers
    # ========================================

    def _on_order_changed(
        self, seq_idx: int, moved_components: list[ComponentReference] | None = None
    ) -> None:
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
        self._validate_sequence(seq_idx, moved_components)
        for other in self._sequences_data:
            if other != seq_idx:
                self._validate_sequence(other)

    def _on_ordered_selection_changed(self, seq_idx: int) -> None:
        """Update violation panel when ordered table selection changes."""
        panel = self._violation_panels.get(seq_idx)
        ordered = self._ordered_tables.get(seq_idx)
        if not panel or not ordered:
            return

        table = ordered["table"]
        selected_rows = {item.row() for item in table.selectedItems()}

        references = []
        for row in selected_rows:
            mod_item = table.item(row, COL_ORDERED_MOD)
            if mod_item:
                ref = mod_item.data(ROLE_COMPONENT)
                if ref and ref.mod_id != PAUSE_PREFIX:
                    references.append(ref)

        current_order = [
            ref for ref in self._sequences_data[seq_idx].ordered if ref.mod_id != PAUSE_PREFIX
        ]

        panel.update_for_references(references, current_order, table.ignored_violations)

    def _on_violation_ignored(self, seq_idx: int) -> None:
        """Refresh visual indicators when an ignore is toggled."""
        self._apply_visual_indicators(seq_idx)

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
            selected_rows = sorted({item.row() for item in selected})
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
        if reference.mod_id == PAUSE_PREFIX:
            self.insert_pause_to_ordered_table(table, row, str(reference))
            return

        mod_item, comp_item = self._component_items(reference)

        table.insertRow(row)
        table.setItem(row, COL_ORDERED_MOD, mod_item)
        table.setItem(row, COL_ORDERED_COMPONENT, comp_item)

    def insert_row_to_unordered_table(
        self, table: QTableWidget, row: int, reference: ComponentReference
    ) -> None:
        """Insert a row at specific position in unordered table."""
        mod_item, comp_item = self._component_items(reference)

        table.insertRow(row)
        table.setItem(row, COL_UNORDERED_MOD, mod_item)
        table.setItem(row, COL_UNORDERED_COMPONENT, comp_item)

    def _component_items(
        self, reference: ComponentReference
    ) -> tuple[QTableWidgetItem, QTableWidgetItem]:
        """Build the mod and component cells shared by both order tables."""
        mod_id = reference.mod_id
        comp_key = reference.comp_key

        mod = self._mod_manager.get_mod_by_id(mod_id)
        mod_name = mod.name if mod else mod_id
        comp_text = mod.get_component(comp_key).get_name()

        mod_label = f"[{mod.id}] {mod_name}"
        mod_item = QTableWidgetItem(mod_label)
        mod_item.setData(ROLE_COMPONENT, reference)
        mod_item.setData(ROLE_LABEL, mod_label)

        comp_item = QTableWidgetItem(f"[{comp_key}] {comp_text}")

        return mod_item, comp_item

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
    # Search API
    # ========================================

    def _search_in_ordered_table(self, seq_idx: int, text: str) -> None:
        data = self._ordered_tables.get(seq_idx)
        if not data:
            return
        table = data["table"]
        text_lower = text.strip().lower()

        if not text_lower:
            data["search_results"] = []
            data["search_idx"] = -1
            self._update_search_buttons(seq_idx)
            return

        results = [
            row
            for row in range(table.rowCount())
            if any(
                (item := table.item(row, col)) and text_lower in item.text().lower()
                for col in range(table.columnCount())
            )
        ]
        data["search_results"] = results
        data["search_idx"] = 0 if results else -1
        self._jump_to_search_result(seq_idx)
        self._update_search_buttons(seq_idx)

    def _jump_to_search_result(self, seq_idx: int) -> None:
        data = self._ordered_tables.get(seq_idx)
        if not data:
            return
        results, idx = data["search_results"], data["search_idx"]
        if not results or not (0 <= idx < len(results)):
            return
        row = results[idx]
        table = data["table"]
        table.clearSelection()
        table.selectRow(row)
        table.scrollTo(
            table.model().index(row, 0), QAbstractItemView.ScrollHint.PositionAtCenter
        )

    def _search_next(self, seq_idx: int) -> None:
        data = self._ordered_tables.get(seq_idx)
        if not data or not data["search_results"]:
            return
        data["search_idx"] = min(data["search_idx"] + 1, len(data["search_results"]) - 1)
        self._jump_to_search_result(seq_idx)
        self._update_search_buttons(seq_idx)

    def _search_prev(self, seq_idx: int) -> None:
        data = self._ordered_tables.get(seq_idx)
        if not data or not data["search_results"]:
            return
        data["search_idx"] = max(data["search_idx"] - 1, 0)
        self._jump_to_search_result(seq_idx)
        self._update_search_buttons(seq_idx)

    def _update_search_buttons(self, seq_idx: int) -> None:
        data = self._ordered_tables.get(seq_idx)
        if not data:
            return
        results, idx = data["search_results"], data["search_idx"]
        if data.get("btn_prev"):
            data["btn_prev"].setEnabled(idx > 0)
        if data.get("btn_next"):
            data["btn_next"].setEnabled(0 <= idx < len(results) - 1)

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
        has_default_order = False
        for sequence in self._game_def.sequences:
            if hasattr(sequence, "order") and len(sequence.order) > 0:
                has_default_order = True

        return [
            self._btn_reset,
            *([self._btn_default] if has_default_order else []),
            self._btn_import,
            self._btn_export,
        ]

    def can_go_to_next_page(self) -> bool:
        """Check if can proceed to next page.

        All sequences must have:
        - All components in ordered list
        - No validation errors
        - Warnings OK if ignored

        Returns:
            True if all conditions met, False otherwise
        """
        if self._missing_selection:
            return False
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

        try:
            orders = {
                idx: [ComponentReference.from_string(entry) for entry in entries]
                for idx, entries in install_order.items()
            }
        except ValueError as error:
            self.state_manager.set_page_option(
                self.get_page_id(), "unmatched_order", install_order
            )
            QMessageBox.warning(
                self,
                tr("page.order.import_error_title"),
                tr("page.order.import_error_message", error=str(error)),
            )
            return False
        if not self._confirm_importable_order(orders):
            self.state_manager.set_page_option(
                self.get_page_id(), "unmatched_order", install_order
            )
            for idx, entries in orders.items():
                if idx in self._sequences_data:
                    self._apply_order_from_list(idx, entries)
            return True
        for idx, refs in orders.items():
            self._apply_order_from_list(idx, refs)

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
        for seq_idx in self._ordered_tables:
            table = self._ordered_tables[seq_idx]["table"]
            table.setHorizontalHeaderLabels(
                [tr("page.order.col_mod"), tr("page.order.col_component")]
            )

        for seq_idx in self._unordered_tables:
            table = self._unordered_tables[seq_idx]["table"]
            table.setHorizontalHeaderLabels(
                [tr("page.order.col_mod"), tr("page.order.col_component")]
            )

        for panel in self._violation_panels.values():
            panel.retranslate_ui()

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
