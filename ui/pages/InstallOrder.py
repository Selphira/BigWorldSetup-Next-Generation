"""
InstallOrderPage - Page for managing component installation order.

This module provides an interface for organizing mod installation order
with drag-and-drop support, automatic ordering, and validation rules.
Supports EET dual-sequence installation (BG1 and BG2 phases).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QPoint, QTimer, Qt, Signal, QMimeData
from PySide6.QtGui import QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QFrame, QHBoxLayout,
    QHeaderView, QMessageBox, QPushButton, QSplitter, QTabWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QSizePolicy
)

from constants import (
    COLOR_ACCENT, COLOR_BACKGROUND_WARNING,
    COLOR_BACKGROUND_ERROR, ICON_ERROR, MARGIN_SMALL,
    MARGIN_STANDARD, SPACING_MEDIUM, SPACING_SMALL, ICON_WARNING,
    ROLE_MOD, ROLE_COMPONENT
)
from core.GameModels import GameDefinition, InstallStep
from core.StateManager import StateManager
from core.TranslationManager import tr
from core.WeiDULogParser import WeiDULogParser
from ui.pages.BasePage import BasePage
from ui.widgets.HoverTableWidget import HoverTableWidget

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

# Drag & drop
MIME_TYPE_COMPONENT = "application/x-bws-component"

# Auto-scroll
AUTO_SCROLL_MARGIN = 30
AUTO_SCROLL_SPEED = 5


# ============================================================================
# Validation System
# ============================================================================

@dataclass
class ComponentIssue:
    """Single validation issue for a component.

    Attributes:
        component_id: Component identifier (mod:comp format)
        message: Issue description
        is_error: True for errors, False for warnings
    """
    component_id: str
    message: str
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

    def add_warning(self, component_id: str, message: str) -> None:
        """Add a warning for a component.

        Args:
            component_id: Component identifier
            message: Warning message
        """
        self._issues.append(ComponentIssue(component_id, message, is_error=False))

    def add_error(self, component_id: str, message: str) -> None:
        """Add an error for a component.

        Args:
            component_id: Component identifier
            message: Error message
        """
        self._issues.append(ComponentIssue(component_id, message, is_error=True))

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

    @property
    def error_count(self) -> int:
        """Get number of errors.

        Returns:
            Count of error issues
        """
        return sum(1 for issue in self._issues if issue.is_error)

    @property
    def warning_count(self) -> int:
        """Get number of warnings.

        Returns:
            Count of warning issues
        """
        return sum(1 for issue in self._issues if not issue.is_error)

    def get_component_issues(self, component_id: str) -> list[ComponentIssue]:
        """Get all issues for a specific component.

        Args:
            component_id: Component identifier

        Returns:
            List of issues for the component
        """
        return [issue for issue in self._issues if issue.component_id == component_id]

    def get_component_indicator(self, component_id: str) -> tuple[QColor, str]:
        """Get color for a component based on its issues.

        Args:
            component_id: Component identifier

        Returns:
            Color to use for component display
        """
        issues = self.get_component_issues(component_id)
        if not issues:
            return self.COLOR_VALID, ""

        has_error = any(issue.is_error for issue in issues)
        return self.COLOR_ERROR if has_error else self.COLOR_WARNING, ICON_ERROR if has_error else ICON_WARNING

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
    ordered: list[tuple[str, str]]
    unordered: list[tuple[str, str]]
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

    def get_component_position(self, mod_id: str, comp_key: str) -> int:
        """Get position of a component in ordered list.

        Args:
            mod_id: Mod identifier
            comp_key: Component key

        Returns:
            Zero-based position, or -1 if not found
        """
        try:
            return self.ordered.index((mod_id, comp_key))
        except ValueError:
            return -1


# ============================================================================
# Draggable Table Widget
# ============================================================================

class DraggableTableWidget(HoverTableWidget):
    """Table widget with enhanced drag-and-drop support.

    Features:
    - Multi-row selection (Ctrl/Shift)
    - Visual drop indicator
    - Auto-scroll during drag
    - Bidirectional drag between tables
    - Row hover highlighting
    """

    # Signals
    orderChanged = Signal()

    def __init__(
            self,
            parent=None,
            column_count: int = 2,
            accept_from_other: bool = False,
            table_role: str | None = None
    ):
        """Initialize draggable table widget.

        Args:
            parent: Parent widget
            column_count: Number of columns
            accept_from_other: Accept drops from other tables
        """

        self._column_count = column_count
        self._accept_from_other = accept_from_other
        self._table_role = table_role
        self._auto_scroll_direction = None
        self._drop_indicator_row = -1
        self._dragged_rows: list[int] = []
        self._hover_row = -1

        super().__init__(parent)

        self._setup_drag_drop()
        self._setup_auto_scroll()

    def _setup_table(self) -> None:
        """Configure table settings."""
        super()._setup_table()

        self.setColumnCount(self._column_count)

        # Hide grid lines for cleaner look
        self.setShowGrid(False)

    def _setup_drag_drop(self) -> None:
        """Configure drag and drop settings."""
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def _setup_auto_scroll(self) -> None:
        """Configure auto-scroll timer."""
        self._auto_scroll_timer = QTimer()
        self._auto_scroll_timer.setInterval(50)
        self._auto_scroll_timer.timeout.connect(self._perform_auto_scroll)
        self._auto_scroll_direction = 0

    def startDrag(self, supported_actions):
        """Start drag operation."""
        self._dragged_rows = [item.row() for item in self.selectedItems()
                              if item.column() == 0]
        self._dragged_rows = sorted(set(self._dragged_rows))

        if not self._dragged_rows:
            return

        # Create drag with MIME data
        drag = QDrag(self)
        mime_data = self._create_mime_data()
        drag.setMimeData(mime_data)

        drag.exec(Qt.DropAction.MoveAction)

    def _create_mime_data(self):
        """Create MIME data for drag operation."""
        mime = QMimeData()

        # Store component data with metadata
        components = []
        for row in self._dragged_rows:
            # Get data from first column (always contains UserRole data)
            first_item = self.item(row, 0)
            if not first_item:
                continue

            mod_id = first_item.data(ROLE_MOD)
            comp_key = first_item.data(ROLE_COMPONENT)

            components.append(f"{mod_id}|{comp_key}")

        data = "\n".join(components)
        mime.setText(data)
        mime.setData(MIME_TYPE_COMPONENT, data.encode())

        return mime

    def dragEnterEvent(self, event):
        """Handle drag enter event."""
        if self._should_accept_drag(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Handle drag move event with visual feedback."""
        if not self._should_accept_drag(event):
            event.ignore()
            self._drop_indicator_row = -1
            self._auto_scroll_timer.stop()
            self.viewport().update()
            return

        event.acceptProposedAction()
        self._update_drop_indicator(event.position().toPoint())
        self._update_auto_scroll(event.position().toPoint())
        self.viewport().update()

    def dragLeaveEvent(self, event):
        """Handle drag leave event."""
        self._drop_indicator_row = -1
        self._auto_scroll_timer.stop()
        self._auto_scroll_direction = 0
        self.viewport().update()

    def dropEvent(self, event):
        """Handle drop event with multi-item support."""
        self._auto_scroll_timer.stop()
        self._auto_scroll_direction = 0

        if not self._should_accept_drag(event):
            event.ignore()
            return

        source = cast(DraggableTableWidget, event.source())
        drop_row = self._drop_indicator_row

        if drop_row < 0:
            drop_row = self.rowCount()

        # Parse MIME data
        mime_data = event.mimeData()
        if not mime_data.hasFormat(MIME_TYPE_COMPONENT):
            event.ignore()
            return

        # Decode component data: mod_id|comp_key format
        components_data = mime_data.data(MIME_TYPE_COMPONENT).data().decode().split("\n")

        # Get the page to rebuild rows properly
        page = self._get_parent_page()
        if not page:
            event.ignore()
            return

        # Block signals during operation
        self.blockSignals(True)

        try:
            # Insert rows at drop position
            for i, comp_data in enumerate(components_data):
                parts = comp_data.split("|")
                if len(parts) != 2:
                    continue

                mod_id = parts[0]
                comp_key = parts[1]

                insert_row = drop_row + i

                if self._table_role == "ordered":
                    page.insert_row_to_ordered_table(self, insert_row, mod_id, comp_key)
                else:
                    page.insert_row_to_unordered_table(self, insert_row, mod_id, comp_key)

            # Remove from source if dropping from another table
            if source and source is not self:
                if hasattr(source, '_dragged_rows'):
                    # Remove in reverse order to maintain indices
                    for row in sorted(source._dragged_rows, reverse=True):
                        source.removeRow(row)
                    source._dragged_rows = []
                    source.orderChanged.emit()
            elif source is self:
                # Same table - remove original rows (adjust for inserted rows)
                adjusted_rows = []
                for row in self._dragged_rows:
                    if row < drop_row:
                        adjusted_rows.append(row)
                    else:
                        adjusted_rows.append(row + len(components_data))

                for row in sorted(adjusted_rows, reverse=True):
                    self.removeRow(row)

        finally:
            self.blockSignals(False)

        self._drop_indicator_row = -1
        self._dragged_rows = []
        self.orderChanged.emit()
        event.acceptProposedAction()

    def _get_parent_page(self) -> InstallOrderPage | None:
        """Get parent InstallOrderPage instance."""
        parent = self.parent()
        while parent:
            if parent.__class__.__name__ == 'InstallOrderPage':
                return cast(InstallOrderPage, parent)
            parent = parent.parent()
        return None

    def mouseReleaseEvent(self, event):
        self._auto_scroll_timer.stop()
        self._auto_scroll_direction = 0
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        """Custom paint to draw drop indicator."""
        super().paintEvent(event)

        if self._drop_indicator_row >= 0:
            self._draw_drop_indicator()

    def _should_accept_drag(self, event) -> bool:
        """Check if drag should be accepted.

        Args:
            event: Drag event

        Returns:
            True if drag should be accepted, False otherwise
        """
        return event.source() == self or self._accept_from_other

    def _update_drop_indicator(self, pos: QPoint) -> None:
        """Update drop indicator position.

        Args:
            pos: Mouse position
        """
        row = self.rowAt(pos.y())

        if row >= 0:
            rect = self.visualRect(self.model().index(row, 0))
            if pos.y() < rect.center().y():
                self._drop_indicator_row = row
            else:
                self._drop_indicator_row = row + 1
        else:
            self._drop_indicator_row = self.rowCount()

    def _update_auto_scroll(self, pos: QPoint) -> None:
        """Update auto-scroll based on cursor position.

        Args:
            pos: Mouse position
        """
        viewport_height = self.viewport().height()

        if pos.y() < AUTO_SCROLL_MARGIN:
            self._auto_scroll_direction = -1
            if not self._auto_scroll_timer.isActive():
                self._auto_scroll_timer.start()
        elif pos.y() > viewport_height - AUTO_SCROLL_MARGIN:
            self._auto_scroll_direction = 1
            if not self._auto_scroll_timer.isActive():
                self._auto_scroll_timer.start()
        else:
            self._auto_scroll_direction = 0
            self._auto_scroll_timer.stop()

    def _perform_auto_scroll(self) -> None:
        """Perform auto-scroll action."""
        if self._auto_scroll_direction != 0:
            scrollbar = self.verticalScrollBar()
            current = scrollbar.value()
            new_value = current + (self._auto_scroll_direction * AUTO_SCROLL_SPEED)
            scrollbar.setValue(new_value)

    def _draw_drop_indicator(self) -> None:
        """Draw the drop indicator line."""
        painter = QPainter(self.viewport())
        pen = QPen(QColor(COLOR_ACCENT), 2)
        painter.setPen(pen)

        # Calculate y position
        if self._drop_indicator_row < self.rowCount():
            rect = self.visualRect(self.model().index(self._drop_indicator_row, 0))
            y = rect.top()
        elif self.rowCount() > 0:
            last_rect = self.visualRect(self.model().index(self.rowCount() - 1, 0))
            y = last_rect.bottom()
        else:
            y = 0

        painter.drawLine(0, y, self.viewport().width(), y)
        painter.end()


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
        self._weidu_parser = WeiDULogParser()

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
        self._btn_weidu: QPushButton | None = None
        self._btn_import: QPushButton | None = None
        self._btn_export: QPushButton | None = None
        self._chk_ignore_warnings: QCheckBox | None = None
        self._chk_ignore_errors: QCheckBox | None = None

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
            MARGIN_STANDARD, MARGIN_STANDARD,
            MARGIN_STANDARD, MARGIN_STANDARD
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
        # Load default order
        self._btn_default = QPushButton()
        self._btn_default.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_default.clicked.connect(self._load_default_order_current_tab)

        # Load from WeiDU.log
        self._btn_weidu = QPushButton()
        self._btn_weidu.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_weidu.clicked.connect(self._load_from_weidu_log)

        # Import order
        self._btn_import = QPushButton()
        self._btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_import.clicked.connect(self._import_order)

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

        logger.info(f"Rebuilding UI for {selected_game}: {self._game_def.sequence_count} sequence(s)")

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

        # Title
        title = self._create_section_title()
        layout.addWidget(title)

        # Table widget
        table = DraggableTableWidget(
            column_count=ORDERED_COLUMN_COUNT,
            accept_from_other=True,
            table_role="ordered"
        )

        # Configure columns
        table.setHorizontalHeaderLabels([
            tr("page.order.col_mod"),
            tr("page.order.col_component")
        ])

        header = table.horizontalHeader()
        header.setSectionResizeMode(COL_ORDERED_MOD, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_ORDERED_COMPONENT, QHeaderView.ResizeMode.Stretch)

        table.orderChanged.connect(lambda: self._on_order_changed(seq_idx))
        layout.addWidget(table)

        # Store references
        self._ordered_tables[seq_idx] = {
            'title': title,
            'table': table
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
            column_count=UNORDERED_COLUMN_COUNT,
            accept_from_other=True,
            table_role="unordered"
        )

        # Configure columns
        table.setHorizontalHeaderLabels([
            tr("page.order.col_mod"),
            tr("page.order.col_component")
        ])

        header = table.horizontalHeader()
        header.setSectionResizeMode(COL_UNORDERED_MOD, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_UNORDERED_COMPONENT, QHeaderView.ResizeMode.Stretch)

        table.orderChanged.connect(lambda: self._on_order_changed(seq_idx))
        table.itemDoubleClicked.connect(
            lambda item: self._on_unordered_double_click(seq_idx, item)
        )

        layout.addWidget(table)

        # Store references
        self._unordered_tables[seq_idx] = {
            'title': title,
            'table': table
        }

        return panel

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
                ordered=[],
                unordered=[],
                validation=ValidationResult()
            )

        # Distribute components to sequences
        for mod_id, comp_list in selected.items():
            for comp in comp_list:
                mod = self._mod_manager.get_mod_by_id(mod_id)
                if not mod:
                    continue
                comp_key = comp["key"] if isinstance(comp, dict) else comp
                component = mod.get_component(comp_key)
                if component and not component.is_dwn():
                    self._place_component_in_sequences(mod_id, comp_key)

        self._refresh_all_tables()

    def _place_component_in_sequences(self, mod_id: str, comp_key: str) -> None:
        """Place a component in allowed sequences.

        Args:
            mod_id: Mod identifier
            comp_key: Component key
        """
        placed = False

        for seq_idx, sequence in enumerate(self._game_def.sequences):
            if not sequence.is_mod_allowed(mod_id):
                continue

            if not sequence.is_component_allowed(mod_id, comp_key):
                continue

            self._sequences_data[seq_idx].unordered.append((mod_id, comp_key))
            placed = True

        if not placed:
            logger.debug(f"Component not allowed in any sequence: {mod_id}:{comp_key}")

    def _apply_order_from_list(self, seq_idx: int, order: list[str]) -> None:
        """Apply order from a list of component IDs.

        Args:
            seq_idx: Sequence index
            order: List of component IDs in format "mod:comp"
        """
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data:
            return

        # Build component pool
        pool = {
            f"{mod.lower()}:{comp}": (mod, comp)
            for mod, comp in (seq_data.ordered + seq_data.unordered)
        }

        # Apply order
        new_ordered = []
        for comp_id in order:
            if comp_id in pool:
                new_ordered.append(pool[comp_id])
                del pool[comp_id]

        # Remaining components
        new_unordered = list(pool.values())

        seq_data.ordered = new_ordered
        seq_data.unordered = new_unordered

        self._refresh_sequence_tables(seq_idx)
        self._validate_sequence(seq_idx)

        logger.info(f"Sequence {seq_idx}: {len(new_ordered)} ordered, {len(new_unordered)} unordered")

    def _apply_sequence_order(
            self,
            seq_idx: int,
            install_steps: tuple[InstallStep, ...]
    ) -> None:
        """Apply installation order from InstallStep sequence.

        Args:
            seq_idx: Sequence index
            install_steps: Tuple of installation steps
        """
        order = [
            f"{step.mod.lower()}:{step.comp}"
            for step in install_steps
            if not step.is_annotation and step.is_install
        ]
        self._apply_order_from_list(seq_idx, order)

    def _load_default_order_current_tab(self) -> None:
        """Load default order for current tab."""
        if not self._game_def:
            return

        index = self._current_sequence_idx
        sequence = self._game_def.get_sequence(index)
        if sequence:
            self._apply_sequence_order(index, sequence.order)
            logger.info(f"Loaded default order for sequence {index}")

    def _load_default_order(self) -> None:
        """Load default order from game definition."""
        if not self._game_def:
            return

        for seq_idx, sequence in enumerate(self._game_def.sequences):
            if seq_idx in self._sequences_data:
                self._apply_sequence_order(seq_idx, sequence.order)

        logger.info("Loaded default order for all sequences")

    def _load_from_weidu_log(self) -> None:
        """Load order from WeiDU.log file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            tr("page.order.select_weidu_log"),
            "",
            "WeiDU Log (WeiDU.log);;All Files (*.*)"
        )

        if not file_path:
            return

        try:
            component_ids = self._weidu_parser.parse_file_simple(file_path)
            self._apply_order_from_list(self._current_sequence_idx, component_ids)

            seq_data = self._sequences_data[self._current_sequence_idx]
            QMessageBox.information(
                self,
                tr("page.order.apply_success_title"),
                tr("page.order.apply_success_message",
                   ordered=len(seq_data.ordered),
                   unordered=len(seq_data.unordered))
            )
        except Exception as e:
            logger.error(f"Error parsing WeiDU.log: {e}", exc_info=True)
            QMessageBox.critical(
                self,
                tr("page.order.parse_error_title"),
                tr("page.order.parse_error_message", error=str(e))
            )

    def _import_order(self) -> None:
        """Import order from JSON file."""
        QMessageBox.information(
            self,
            "A développer",
            "Importation depuis un fichier exporté"
        )

    def _export_order(self) -> None:
        """Export current order to JSON file."""
        QMessageBox.information(
            self,
            "A développer",
            "Exportation de l'ordre actuel"
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

        ordered_table = self._ordered_tables[seq_idx]['table']
        unordered_table = self._unordered_tables[seq_idx]['table']

        # Block signals during refresh
        ordered_table.blockSignals(True)
        unordered_table.blockSignals(True)

        try:
            # Clear tables
            ordered_table.setRowCount(0)
            unordered_table.setRowCount(0)

            # Populate ordered table (3 columns)
            for mod_id, comp_key in seq_data.ordered:
                self._add_row_to_ordered_table(ordered_table, mod_id, comp_key)

            # Populate unordered table (2 columns)
            for mod_id, comp_key in seq_data.unordered:
                self._add_row_to_unordered_table(unordered_table, mod_id, comp_key)

        finally:
            ordered_table.blockSignals(False)
            unordered_table.blockSignals(False)

        self._update_sequence_counters(seq_idx)

    def _add_row_to_ordered_table(
            self,
            table: QTableWidget,
            mod_id: str,
            comp_key: str
    ) -> None:
        """Add a row to the ordered table."""
        row = table.rowCount()
        self.insert_row_to_ordered_table(table, row, mod_id, comp_key)

    def _add_row_to_unordered_table(
            self,
            table: QTableWidget,
            mod_id: str,
            comp_key: str
    ) -> None:
        """Add a row to the unordered table."""
        row = table.rowCount()
        self.insert_row_to_unordered_table(table, row, mod_id, comp_key)

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

        self._ordered_tables[seq_idx]['title'].setText(
            tr("page.order.ordered_title", count=ordered_count, total=total)
        )
        self._unordered_tables[seq_idx]['title'].setText(
            tr("page.order.unordered_title", count=unordered_count)
        )

    # ========================================
    # Validation
    # ========================================

    def _validate_sequence(self, seq_idx: int) -> None:
        """Validate order for a sequence."""
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data:
            return

        seq_data.validation.clear()

        if not seq_data.ordered:
            return

        # Validate order
        order_violations = self._rule_manager.validate_order(seq_data.ordered)

        for violation in order_violations:
            for mod_id, comp_key in violation.affected_components:
                comp_id = f"{mod_id}:{comp_key}"
                if violation.is_error:
                    seq_data.validation.add_error(comp_id, violation.message)
                elif violation.is_warning:
                    seq_data.validation.add_warning(comp_id, violation.message)

        self._apply_visual_indicators(seq_idx)
        self.notify_navigation_changed()

    def _apply_visual_indicators(self, seq_idx: int) -> None:
        """Apply visual indicators to ordered table."""
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data or seq_idx not in self._ordered_tables:
            return

        ordered_table = self._ordered_tables[seq_idx]['table']

        for row in range(ordered_table.rowCount()):
            mod_item = ordered_table.item(row, COL_ORDERED_MOD)
            if not mod_item:
                continue

            mod_id = mod_item.data(ROLE_MOD)
            comp_key = mod_item.data(ROLE_COMPONENT)

            # Get violations
            violations = self._rule_manager.get_violations_for_component(mod_id, comp_key)

            mod_item.setText(mod_item.text().replace(f"{ICON_ERROR} ", "").replace(f"{ICON_WARNING} ", ""))

            if violations:
                tooltip_lines = []
                for v in violations:
                    tooltip_lines.append(f"{v.icon} {v.message}")

                color, icon = seq_data.validation.get_component_indicator(f"{mod_id}:{comp_key}")
                mod_item.setText(f"{icon} {mod_item.text()}")
                mod_item.setToolTip("\n".join(tooltip_lines))

                for col in range(ordered_table.columnCount()):
                    item = ordered_table.item(row, col)
                    if item:
                        item.setBackground(color)

            else:
                mod_item.setToolTip("")

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

        ordered_table = self._ordered_tables[seq_idx]['table']
        unordered_table = self._unordered_tables[seq_idx]['table']

        # Rebuild from tables
        seq_data.ordered = []
        for row in range(ordered_table.rowCount()):
            mod_item = ordered_table.item(row, COL_ORDERED_MOD)
            if mod_item:
                mod_id = mod_item.data(ROLE_MOD)
                comp_key = mod_item.data(ROLE_COMPONENT)
                seq_data.ordered.append((mod_id, comp_key))

        seq_data.unordered = []
        for row in range(unordered_table.rowCount()):
            mod_item = unordered_table.item(row, COL_UNORDERED_MOD)
            if mod_item:
                mod_id = mod_item.data(ROLE_MOD)
                comp_key = mod_item.data(ROLE_COMPONENT)
                seq_data.unordered.append((mod_id, comp_key))

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

        ordered_table = self._ordered_tables[seq_idx]['table']
        unordered_table = self._unordered_tables[seq_idx]['table']

        row = item.row()
        mod_item = unordered_table.item(row, COL_UNORDERED_MOD)
        if not mod_item:
            return

        mod_id = mod_item.data(ROLE_MOD)
        comp_key = mod_item.data(ROLE_COMPONENT)

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
            self.insert_row_to_ordered_table(ordered_table, target_row, mod_id, comp_key)

            # Remove from unordered table
            unordered_table.removeRow(row)

        finally:
            ordered_table.blockSignals(False)
            unordered_table.blockSignals(False)

        # Trigger update
        self._on_order_changed(seq_idx)

    def insert_row_to_ordered_table(
            self,
            table: QTableWidget,
            row: int,
            mod_id: str,
            comp_key: str
    ) -> None:
        """Insert a row at specific position in ordered table."""
        table.insertRow(row)

        mod = self._mod_manager.get_mod_by_id(mod_id)
        mod_name = mod.name if mod else mod_id
        comp_text = mod.get_component(comp_key).get_name()

        # Column 0: Mod name
        mod_item = QTableWidgetItem(f"[{mod.tp2}] {mod_name}")
        mod_item.setData(ROLE_MOD, mod_id)
        mod_item.setData(ROLE_COMPONENT, comp_key)
        table.setItem(row, COL_ORDERED_MOD, mod_item)

        # Column 1: Component
        comp_item = QTableWidgetItem(f"[{comp_key}] {comp_text}")
        table.setItem(row, COL_ORDERED_COMPONENT, comp_item)

    def insert_row_to_unordered_table(
            self,
            table: QTableWidget,
            row: int,
            mod_id: str,
            comp_key: str
    ) -> None:
        """Insert a row at specific position in unordered table."""
        table.insertRow(row)

        mod = self._mod_manager.get_mod_by_id(mod_id)
        mod_name = mod.name if mod else mod_id
        comp_text = mod.get_component(comp_key).get_name()

        # Column 0: Mod name
        mod_item = QTableWidgetItem(f"[{mod.tp2}] {mod_name}")
        mod_item.setData(ROLE_MOD, mod_id)
        mod_item.setData(ROLE_COMPONENT, comp_key)
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
        self._ignore_warnings = (state == Qt.CheckState.Checked.value)
        self.notify_navigation_changed()
        logger.debug(f"Ignore warnings: {self._ignore_warnings}")

    def _on_ignore_errors_changed(self, state: int) -> None:
        """Handle ignore errors checkbox change.

        Args:
            state: Checkbox state
        """
        self._ignore_errors = (state == Qt.CheckState.Checked.value)
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
        return [self._btn_default, self._btn_weidu, self._btn_import, self._btn_export]

    def can_go_to_next_page(self) -> bool:
        """Check if can proceed to next page.

        All sequences must have:
        - All components in ordered list
        - No validation errors
        - Warnings OK if ignored

        Returns:
            True if all conditions met, False otherwise
        """
        for seq_idx, seq_data in self._sequences_data.items():
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

        # Apply saved order to each sequence
        for seq_idx, order_list in install_order.items():
            if seq_idx in self._sequences_data:
                self._apply_order_from_list(seq_idx, order_list)

        return True

    def retranslate_ui(self) -> None:
        """Update UI text for language change."""
        # Update buttons
        self._btn_default.setText(tr("page.order.btn_default"))
        self._btn_weidu.setText(tr("page.order.btn_weidu"))
        self._btn_import.setText(tr("page.order.btn_import"))
        self._btn_export.setText(tr("page.order.btn_export"))
        self._chk_ignore_warnings.setText(tr("page.order.ignore_warnings"))
        self._chk_ignore_errors.setText(tr("page.order.ignore_errors"))

        # Update sequence counters
        for seq_idx in self._sequences_data.keys():
            self._update_sequence_counters(seq_idx)

        # TODO: Translate components entries on language change !

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
            table = self._ordered_tables[seq_idx]['table']
            table.setHorizontalHeaderLabels([
                tr("page.order.col_mod"),
                tr("page.order.col_component")
            ])

        for seq_idx in self._unordered_tables.keys():
            table = self._unordered_tables[seq_idx]['table']
            table.setHorizontalHeaderLabels([
                tr("page.order.col_mod"),
                tr("page.order.col_component")
            ])

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
            install_order[seq_idx] = [
                f"{mod_id.lower()}:{comp_key}"
                for mod_id, comp_key in seq_data.ordered
            ]

        self.state_manager.set_install_order(install_order)
        self.state_manager.set_page_option(self.get_page_id(), "ignore_errors", self._ignore_errors)
        self.state_manager.set_page_option(self.get_page_id(), "ignore_warnings", self._ignore_warnings)
