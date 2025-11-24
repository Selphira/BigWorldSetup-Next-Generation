"""
InstallOrderPage - Page for managing component installation order.

This module provides an interface for organizing mod installation order
with drag-and-drop support, automatic ordering, and validation rules.
Supports EET dual-sequence installation (BG1 and BG2 phases).
"""
import logging
from dataclasses import dataclass

from PySide6.QtCore import QPoint, QTimer, Qt, QRect, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFrame, QHBoxLayout, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter,
    QStyledItemDelegate, QStyle, QTabWidget, QVBoxLayout, QWidget
)

from constants import (
    COLOR_ACCENT, COLOR_TEXT, COLOR_TEXT_DISABLED,
    MARGIN_SMALL, MARGIN_STANDARD, SPACING_MEDIUM, SPACING_SMALL
)
from core.GameModels import GameDefinition, InstallStep
from core.StateManager import StateManager
from core.TranslationManager import tr
from core.WeiDULogParser import WeiDULogParser
from ui.pages.BasePage import BasePage

logger = logging.getLogger(__name__)


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
    COLOR_VALID = QColor("#2ecc71")
    COLOR_WARNING = QColor("#f39c12")
    COLOR_ERROR = QColor("#e74c3c")

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

    def get_component_color(self, component_id: str) -> QColor:
        """Get color for a component based on its issues.

        Args:
            component_id: Component identifier

        Returns:
            Color to use for component display
        """
        issues = self.get_component_issues(component_id)
        if not issues:
            return self.COLOR_VALID

        has_error = any(issue.is_error for issue in issues)
        return self.COLOR_ERROR if has_error else self.COLOR_WARNING

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
# Draggable List Widget
# ============================================================================

class DraggableListWidget(QListWidget):
    """List widget with enhanced drag-and-drop support.

    Features:
    - Multi-selection (Ctrl/Shift)
    - Visual drop indicator
    - Auto-scroll during drag
    - Bidirectional drag between lists
    """

    # Signals
    orderChanged = Signal()

    # Auto-scroll constants
    AUTO_SCROLL_MARGIN = 30
    AUTO_SCROLL_SPEED = 5

    def __init__(self, parent=None, accept_from_other: bool = False):
        """Initialize draggable list widget.

        Args:
            parent: Parent widget
            accept_from_other: If True, accept drops from other list widgets
        """
        super().__init__(parent)
        self._dragged_items: list[QListWidgetItem] | None = None
        self._accept_from_other = accept_from_other
        self._drop_indicator_row = -1

        self._setup_drag_drop()
        self._setup_auto_scroll()

    def _setup_drag_drop(self) -> None:
        """Configure drag and drop settings."""
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)

    def _setup_auto_scroll(self) -> None:
        """Configure auto-scroll timer."""
        self._auto_scroll_timer = QTimer()
        self._auto_scroll_timer.setInterval(50)
        self._auto_scroll_timer.timeout.connect(self._perform_auto_scroll)
        self._auto_scroll_direction = 0

    def dragEnterEvent(self, event):
        """Handle drag enter event."""
        if self._should_accept_drag(event):
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        """Handle drag move event with visual feedback."""
        if not self._should_accept_drag(event):
            event.ignore()
            self._drop_indicator_row = -1
            self._auto_scroll_timer.stop()
            return

        event.accept()
        self._update_drop_indicator(event.pos())
        self._update_auto_scroll(event.pos())

    def dragLeaveEvent(self, event):
        """Handle drag leave event."""
        self._drop_indicator_row = -1
        self._auto_scroll_timer.stop()
        self.viewport().update()

    def startDrag(self, supported_actions):
        """Start drag operation."""
        self._dragged_items = list(self.selectedItems())
        super().startDrag(supported_actions)

    def dropEvent(self, event):
        """Handle drop event with multi-item support."""
        self._drop_indicator_row = -1
        source = event.source()

        # Let Qt handle same-list moves
        super().dropEvent(event)

        # Remove items from source if dropping from another list
        if source is not None and source is not self and hasattr(source, '_dragged_items'):
            if source._dragged_items:
                for item in source._dragged_items:
                    row = source.row(item)
                    if row >= 0:
                        source.takeItem(row)
                source._dragged_items = None
            source.orderChanged.emit()

        self.orderChanged.emit()
        event.accept()

    def paintEvent(self, event):
        """Custom paint to draw drop indicator."""
        super().paintEvent(event)

        if self._drop_indicator_row >= 0:
            self._draw_drop_indicator()

    def mouseDoubleClickEvent(self, event):
        """Handle double-click event."""
        # Parent will connect to itemDoubleClicked signal
        super().mouseDoubleClickEvent(event)

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
        index = self.indexAt(pos)

        if index.isValid():
            rect = self.visualRect(index)
            if pos.y() < rect.center().y():
                self._drop_indicator_row = index.row()
            else:
                self._drop_indicator_row = index.row() + 1
        else:
            self._drop_indicator_row = self.count()

        self.viewport().update()

    def _update_auto_scroll(self, pos: QPoint) -> None:
        """Update auto-scroll based on cursor position.

        Args:
            pos: Mouse position
        """
        viewport_height = self.viewport().height()

        if pos.y() < self.AUTO_SCROLL_MARGIN:
            self._auto_scroll_direction = -1
            if not self._auto_scroll_timer.isActive():
                self._auto_scroll_timer.start()
        elif pos.y() > viewport_height - self.AUTO_SCROLL_MARGIN:
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
            new_value = current + (self._auto_scroll_direction * self.AUTO_SCROLL_SPEED)
            scrollbar.setValue(new_value)

    def _draw_drop_indicator(self) -> None:
        """Draw the drop indicator line."""
        painter = QPainter(self.viewport())
        pen = QPen(QColor(COLOR_ACCENT), 2)
        painter.setPen(pen)

        # Calculate y position
        if self._drop_indicator_row < self.count():
            rect = self.visualRect(self.indexFromItem(self.item(self._drop_indicator_row)))
            y = rect.top()
        elif self.count() > 0:
            last_rect = self.visualRect(self.indexFromItem(self.item(self.count() - 1)))
            y = last_rect.bottom()
        else:
            y = 0

        painter.drawLine(0, y, self.viewport().width(), y)


# ============================================================================
# Custom Item Delegate
# ============================================================================

class ArrowDelegate(QStyledItemDelegate):
    """Item delegate that adds arrow prefix to unordered items."""

    def __init__(self, parent=None, prefix: str = "🞀"):
        """Initialize arrow delegate.

        Args:
            parent: Parent widget
            prefix: Prefix character/string to display
        """
        super().__init__(parent)
        self.prefix = prefix + " "

    def paint(self, painter: QPainter, option, index):
        """Paint item with arrow prefix.

        Args:
            painter: QPainter instance
            option: Style options
            index: Model index
        """
        painter.save()

        # Draw background/selection
        style = option.widget.style() if option.widget else QStyle()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)

        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        rect = option.rect
        fm = QFontMetrics(option.font)
        prefix_width = fm.horizontalAdvance(self.prefix)

        # Draw prefix
        painter.setPen(QColor(COLOR_TEXT_DISABLED))
        painter.setFont(option.font)
        painter.drawText(
            rect.left() + 6, rect.top(), prefix_width, rect.height(),
            Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
            self.prefix
        )

        # Draw text
        painter.setPen(QColor(COLOR_TEXT))
        text_rect = QRect(
            rect.left() + 20 + prefix_width, rect.top(),
            rect.width() - 20 - prefix_width, rect.height()
        )
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
            text
        )

        painter.restore()

    def sizeHint(self, option, index):
        """Calculate size hint for item.

        Args:
            option: Style options
            index: Model index

        Returns:
            Size hint
        """
        size = super().sizeHint(option, index)
        size.setHeight(size.height() + 2)
        return size


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
        self._weidu_parser = WeiDULogParser()

        # Game state
        self._current_game: str | None = None
        self._game_def: GameDefinition | None = None

        # Sequence data
        self._sequences_data: dict[int, SequenceData] = {}
        self._current_sequence_idx = 0

        # Validation
        self._ignore_warnings = False

        # Widget containers
        self._main_container: QWidget | None = None
        self._main_layout: QVBoxLayout | None = None
        self._phase_tabs: QTabWidget | None = None
        self._ordered_widgets: dict[int, dict] = {}
        self._unordered_widgets: dict[int, dict] = {}

        # Action buttons
        self._btn_default: QPushButton | None = None
        self._btn_weidu: QPushButton | None = None
        self._btn_import: QPushButton | None = None
        self._btn_export: QPushButton | None = None
        self._chk_ignore_warnings: QCheckBox | None = None

        self._create_widgets()

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

        # Main container (populated dynamically)
        self._main_container = QWidget()
        self._main_layout = QVBoxLayout(self._main_container)
        self._main_layout.setContentsMargins(0, 0, 0, 0)

        layout.addWidget(self._main_container, stretch=1)

        # Action buttons
        actions = self._create_action_buttons()
        layout.addWidget(actions)

    def _create_action_buttons(self) -> QWidget:
        """Create action buttons row.

        Returns:
            Widget containing action buttons
        """
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_SMALL)

        # Load default order
        self._btn_default = QPushButton()
        self._btn_default.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_default.clicked.connect(self._load_default_order)
        layout.addWidget(self._btn_default)

        # Load from WeiDU.log
        self._btn_weidu = QPushButton()
        self._btn_weidu.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_weidu.clicked.connect(self._load_from_weidu_log)
        layout.addWidget(self._btn_weidu)

        # Import order
        self._btn_import = QPushButton()
        self._btn_import.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_import.clicked.connect(self._import_order)
        layout.addWidget(self._btn_import)

        # Export order
        self._btn_export = QPushButton()
        self._btn_export.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_export.clicked.connect(self._export_order)
        layout.addWidget(self._btn_export)

        layout.addStretch()

        # Ignore warnings checkbox
        self._chk_ignore_warnings = QCheckBox()
        self._chk_ignore_warnings.stateChanged.connect(self._on_ignore_warnings_changed)
        layout.addWidget(self._chk_ignore_warnings)

        return container

    def _rebuild_ui_for_game(self) -> None:
        """Rebuild UI based on current game configuration."""
        self._clear_main_layout()
        self._reset_widget_references()

        selected_game = self.state_manager.get_selected_game()
        self._game_def = self._game_manager.get(selected_game)

        if not self._game_def:
            logger.error(f"Game definition not found for {selected_game}")
            return

        logger.info(f"Rebuilding UI for {selected_game}: {self._game_def.sequence_count} sequence(s)")

        # Create UI based on sequence count
        if self._game_def.has_multiple_sequences:
            content = self._create_multi_sequence_tabs()
        else:
            content = self._create_single_sequence_panel(0)

        self._main_layout.addWidget(content)

    def _clear_main_layout(self) -> None:
        """Clear all widgets from main layout."""
        while self._main_layout.count():
            item = self._main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _reset_widget_references(self) -> None:
        """Reset all widget reference dictionaries."""
        self._ordered_widgets.clear()
        self._unordered_widgets.clear()
        self._phase_tabs = None

    def _create_multi_sequence_tabs(self) -> QWidget:
        """Create tabbed interface for multiple sequences.

        Returns:
            Tab widget containing all sequences
        """
        tabs = QTabWidget()

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

        # List widget
        list_widget = DraggableListWidget(accept_from_other=True)
        list_widget.orderChanged.connect(lambda: self._on_order_changed(seq_idx))
        layout.addWidget(list_widget)

        # Store references
        self._ordered_widgets[seq_idx] = {
            'title': title,
            'list': list_widget
        }

        return panel

    def _create_unordered_panel(self, seq_idx: int) -> QWidget:
        """Create right panel with unordered components.

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

        # List widget
        list_widget = DraggableListWidget(accept_from_other=True)
        list_widget.orderChanged.connect(lambda: self._on_order_changed(seq_idx))
        list_widget.itemDoubleClicked.connect(
            lambda item: self._on_unordered_double_click(seq_idx, item)
        )

        # Add arrow delegate
        delegate = ArrowDelegate(parent=list_widget)
        list_widget.setItemDelegate(delegate)

        layout.addWidget(list_widget)

        # Store references
        self._unordered_widgets[seq_idx] = {
            'title': title,
            'list': list_widget
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
                comp_key = comp["key"] if isinstance(comp, dict) else comp
                self._place_component_in_sequences(mod_id, comp_key)

        self._refresh_all_lists()

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
            logger.info(
                f"Component discarded (not allowed in any sequence): "
                f"{mod_id}:{comp_key}"
            )

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
            f"{mod}:{comp}": (mod, comp)
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

        self._refresh_sequence_lists(seq_idx)
        self._validate_sequence(seq_idx)

        logger.info(f"{len(seq_data.ordered)} ordered, {len(seq_data.unordered)} unordered")

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
        order = [f"{step.mod}:{step.comp}" for step in install_steps if
                 not step.is_annotation and not not step.is_install]

        self._apply_order_from_list(seq_idx, order)

    def _load_default_order(self) -> None:
        """Load default order from game definition."""
        if not self._game_def:
            return

        for seq_idx, sequence in enumerate(self._game_def.sequences):
            if seq_idx in self._sequences_data:
                self._apply_sequence_order(seq_idx, sequence.order)

        logger.info(f"Loaded default order")

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

            QMessageBox.information(
                self,
                tr("page.order.apply_success_title"),
                tr("page.order.apply_success_message",
                   ordered=len(self._sequences_data[self._current_sequence_idx].ordered),
                   unordered=len(self._sequences_data[self._current_sequence_idx].unordered))
            )
        except Exception as e:
            logger.error(f"Error parsing WeiDU.log: {e}")
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

    def _refresh_all_lists(self) -> None:
        """Refresh lists for all sequences."""
        for seq_idx in self._sequences_data.keys():
            self._refresh_sequence_lists(seq_idx)

    def _refresh_sequence_lists(self, seq_idx: int) -> None:
        """Refresh lists for a specific sequence.

        Args:
            seq_idx: Sequence index
        """
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data:
            return

        # Check if widgets exist
        if seq_idx not in self._ordered_widgets or seq_idx not in self._unordered_widgets:
            return

        ordered_list = self._ordered_widgets[seq_idx]['list']
        unordered_list = self._unordered_widgets[seq_idx]['list']

        # Block signals during refresh
        ordered_list.blockSignals(True)
        unordered_list.blockSignals(True)

        try:
            ordered_list.clear()
            unordered_list.clear()

            # Populate ordered list
            for mod_id, comp_key in seq_data.ordered:
                item = self._create_list_item(mod_id, comp_key)
                ordered_list.addItem(item)

            # Populate unordered list
            for mod_id, comp_key in seq_data.unordered:
                item = self._create_list_item(mod_id, comp_key)
                unordered_list.addItem(item)

        finally:
            ordered_list.blockSignals(False)
            unordered_list.blockSignals(False)

        self._update_sequence_counters(seq_idx)

    def _create_list_item(self, mod_id: str, comp_key: str) -> QListWidgetItem:
        """Create a list item for a component.

        Args:
            mod_id: Mod identifier
            comp_key: Component key

        Returns:
            List widget item
        """
        mod = self._mod_manager.get_mod_by_id(mod_id)
        if not mod:
            display_text = f"{mod_id}: {comp_key}"
        else:
            comp_text = mod.get_component_text(comp_key)
            display_text = f"{mod.name}: {comp_text}"

        item = QListWidgetItem(display_text)
        item.setData(Qt.ItemDataRole.UserRole, mod_id)
        item.setData(Qt.ItemDataRole.UserRole + 1, comp_key)

        return item

    def _update_sequence_counters(self, seq_idx: int) -> None:
        """Update component counters for a sequence.

        Args:
            seq_idx: Sequence index
        """
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data:
            return

        if seq_idx not in self._ordered_widgets or seq_idx not in self._unordered_widgets:
            return

        ordered_count = len(seq_data.ordered)
        unordered_count = len(seq_data.unordered)
        total = seq_data.total_count

        self._ordered_widgets[seq_idx]['title'].setText(
            tr("page.order.ordered_title", count=ordered_count, total=total)
        )
        self._unordered_widgets[seq_idx]['title'].setText(
            tr("page.order.unordered_title", count=unordered_count)
        )

    # ========================================
    # Validation
    # ========================================

    def _validate_sequence(self, seq_idx: int) -> None:
        """Validate order for a specific sequence.

        Args:
            seq_idx: Sequence index
        """
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data:
            return

        seq_data.validation.clear()

        if not seq_data.ordered:
            return

        if seq_idx not in self._ordered_widgets:
            return

        # TODO: Implement actual validation rules
        # For now, just ensure all components are valid

        # Apply visual indicators
        ordered_list = self._ordered_widgets[seq_idx]['list']
        for idx in range(ordered_list.count()):
            item = ordered_list.item(idx)
            if not item:
                continue

            mod_id = item.data(Qt.ItemDataRole.UserRole)
            comp_key = item.data(Qt.ItemDataRole.UserRole + 1)
            comp_id = f"{mod_id}:{comp_key}"

            color = seq_data.validation.get_component_color(comp_id)
            if color != ValidationResult.COLOR_VALID:
                item.setBackground(color)
            else:
                item.setBackground(Qt.GlobalColor.transparent)

        self.notify_navigation_changed()

    # ========================================
    # Event Handlers
    # ========================================

    def _on_order_changed(self, seq_idx: int) -> None:
        """Handle order change in lists for a sequence.

        Args:
            seq_idx: Sequence index
        """
        seq_data = self._sequences_data.get(seq_idx)
        if not seq_data:
            return

        if seq_idx not in self._ordered_widgets or seq_idx not in self._unordered_widgets:
            return

        ordered_list = self._ordered_widgets[seq_idx]['list']
        unordered_list = self._unordered_widgets[seq_idx]['list']

        # Rebuild from widgets
        seq_data.ordered = [
            (ordered_list.item(i).data(Qt.ItemDataRole.UserRole),
             ordered_list.item(i).data(Qt.ItemDataRole.UserRole + 1))
            for i in range(ordered_list.count())
        ]

        seq_data.unordered = [
            (unordered_list.item(i).data(Qt.ItemDataRole.UserRole),
             unordered_list.item(i).data(Qt.ItemDataRole.UserRole + 1))
            for i in range(unordered_list.count())
        ]

        self._update_sequence_counters(seq_idx)
        self._validate_sequence(seq_idx)

    def _on_unordered_double_click(self, seq_idx: int, item: QListWidgetItem) -> None:
        """Handle double-click on unordered item to move it to ordered list.

        Args:
            seq_idx: Sequence index
            item: Clicked list item
        """
        if seq_idx not in self._ordered_widgets or seq_idx not in self._unordered_widgets:
            return

        ordered_list = self._ordered_widgets[seq_idx]['list']
        unordered_list = self._unordered_widgets[seq_idx]['list']

        # Determine target position
        selected_items = ordered_list.selectedItems()
        target_row = ordered_list.row(selected_items[-1]) + 1 if selected_items else ordered_list.count()

        # Block signals during operation
        ordered_list.blockSignals(True)
        unordered_list.blockSignals(True)

        try:
            # Create new item in ordered list
            new_item = QListWidgetItem(item.text())
            new_item.setData(Qt.ItemDataRole.UserRole, item.data(Qt.ItemDataRole.UserRole))
            new_item.setData(Qt.ItemDataRole.UserRole + 1, item.data(Qt.ItemDataRole.UserRole + 1))
            ordered_list.insertItem(target_row, new_item)

            # Remove from unordered list
            unordered_list.takeItem(unordered_list.row(item))

            # Select new item
            new_item.setSelected(True)
        finally:
            ordered_list.blockSignals(False)
            unordered_list.blockSignals(False)

        # Trigger update
        self._on_order_changed(seq_idx)

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

    def can_proceed(self) -> bool:
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
            if not seq_data.validation.is_valid:
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
            logger.info(f"Game changed from {self._current_game} to {selected_game}, rebuilding UI")
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

        # Update sequence labels
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

    def save_state(self) -> None:
        """Save page data to state manager."""
        super().save_state()

        # Convert sequences data to state format
        install_order = {}
        for seq_idx, seq_data in self._sequences_data.items():
            install_order[seq_idx] = [
                f"{mod_id}:{comp_key}"
                for mod_id, comp_key in seq_data.ordered
            ]

        self.state_manager.set_install_order(install_order)
