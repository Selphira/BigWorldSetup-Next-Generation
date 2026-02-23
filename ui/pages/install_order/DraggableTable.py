from __future__ import annotations

import logging
from typing import Protocol, cast, runtime_checkable

from PySide6.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QPoint,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QAction, QColor, QDrag, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QMenu,
    QTableWidget,
)

from constants import (
    COLOR_ACCENT,
    ROLE_COMPONENT,
)
from core.ComponentReference import ComponentReference, IndexManager
from core.models.PauseEntry import PAUSE_PREFIX, PauseEntry
from core.TranslationManager import tr
from ui.pages.install_order.PauseDescriptionDialog import PauseDescriptionDialog
from ui.widgets.HoverTableWidget import HoverTableWidget

logger = logging.getLogger(__name__)


AUTO_SCROLL_MARGIN = 30
AUTO_SCROLL_SPEED = 5
MIME_TYPE_COMPONENT = "application/x-bws-component"


@runtime_checkable
class OrderPageProtocol(Protocol):
    """Protocol defining required methods for the parent page.

    This avoids circular import with InstallOrderPage.
    """

    def insert_row_to_ordered_table(
        self, table: QTableWidget, row: int, reference: ComponentReference
    ) -> None:
        """Insert a component row to ordered table."""
        ...

    def insert_row_to_unordered_table(
        self, table: QTableWidget, row: int, reference: ComponentReference
    ) -> None:
        """Insert a component row to unordered table."""
        ...

    def insert_pause_to_ordered_table(
        self, table: QTableWidget, row: int, pause_string: str, focus: bool
    ) -> None:
        """Insert a pause row to ordered table."""
        ...


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
    orderChanged = Signal(list)
    violationIgnored = Signal()

    def __init__(
        self,
        parent=None,
        column_count: int = 2,
        accept_from_other: bool = False,
        table_role: str | None = None,
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
        self._ignored_violations: dict[ComponentReference, set[int]] = {}

        super().__init__(parent)

        self._setup_drag_drop()
        self._setup_auto_scroll()

    def _setup_table(self) -> None:
        """Configure table settings."""
        super()._setup_table()

        self.setColumnCount(self._column_count)
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
        self._dragged_rows = [item.row() for item in self.selectedItems() if item.column() == 0]
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

            reference = first_item.data(ROLE_COMPONENT)

            components.append(str(reference))

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
        moved_refs = []

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
            insert_rows = []

            for i, ref in enumerate(components_data):
                reference = ComponentReference.from_string(ref)
                moved_refs.append(reference)

                insert_row = drop_row + i
                insert_rows.append(insert_row)

                if self._table_role == "ordered":
                    page.insert_row_to_ordered_table(self, insert_row, reference)
                else:
                    page.insert_row_to_unordered_table(self, insert_row, reference)

            self._select_rows(insert_rows)

            # Remove from source if dropping from another table
            if source and source is not self:
                if hasattr(source, "_dragged_rows"):
                    # Remove in reverse order to maintain indices
                    for row in sorted(source._dragged_rows, reverse=True):
                        source.removeRow(row)
                    source._dragged_rows = []
                    source.orderChanged.emit(moved_refs)
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

        for ref in moved_refs:
            self.clear_ignore_for(ref)

        self._drop_indicator_row = -1
        self._dragged_rows = []
        self.orderChanged.emit(moved_refs)
        event.acceptProposedAction()

    def _select_rows(self, rows: list[int]) -> None:
        """Select specific rows in the table, handling both contiguous and non-contiguous selections.

        Args:
            rows: List of row indices to select
        """
        if not rows:
            return

        self.clearSelection()
        # Check if rows are contiguous
        selection_flag = (
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        )
        if len(rows) > 1 and rows[-1] - rows[0] == len(rows) - 1:
            top_left = self.model().index(rows[0], 0)
            bottom_right = self.model().index(rows[-1], self.columnCount() - 1)
            selection = QItemSelection()
            selection.select(top_left, bottom_right)
            self.selectionModel().select(selection, selection_flag)
        else:
            # Non-contiguous: select each row individually
            for row in rows:
                index = self.model().index(row, 0)
                self.selectionModel().select(index, selection_flag)

    def _get_parent_page(self) -> OrderPageProtocol | None:
        """Get parent page implementing OrderPageProtocol."""
        parent = self.parent()
        while parent:
            if isinstance(parent, OrderPageProtocol):
                return parent
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
        if self._table_role == "unordered":
            mime_data = event.mimeData()
            if mime_data.hasFormat(MIME_TYPE_COMPONENT):
                data = mime_data.data(MIME_TYPE_COMPONENT).data().decode()
                if PAUSE_PREFIX in data:
                    return False
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

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if not item:
            return

        row = item.row()
        mod_item = self.item(row, 0)
        if not mod_item:
            return

        menu = QMenu(self)
        reference = mod_item.data(ROLE_COMPONENT)

        if reference and reference.mod_id == PAUSE_PREFIX:
            edit_action = QAction(tr("page.order.edit_pause"), self)
            edit_action.triggered.connect(lambda: self._edit_pause_at_row(row))
            menu.addAction(edit_action)
            menu.addSeparator()
            delete_action = QAction(tr("page.order.remove_pause"), self)
            delete_action.triggered.connect(lambda: self._remove_pause_at_row(row))
            menu.addAction(delete_action)
        else:
            if self._table_role == "ordered":
                insert_pause_action = QAction(tr("page.order.insert_pause_after"), self)
                insert_pause_action.triggered.connect(lambda: self._insert_pause_after_row(row))
                menu.addAction(insert_pause_action)

                if reference:
                    violations = IndexManager.get_indexes().get_order_violations(reference)
                    if violations:
                        menu.addSeparator()
                        if self.is_ignored(reference):
                            action = QAction(tr("page.order.restore_violations"), self)
                            action.triggered.connect(
                                lambda: self._restore_violations(reference)
                            )
                        else:
                            action = QAction(tr("page.order.ignore_violations"), self)
                            action.triggered.connect(
                                lambda: self.ignore_violations_for(reference)
                            )
                        menu.addAction(action)

        if not menu.isEmpty():
            menu.exec(event.globalPos())

    def _restore_violations(self, reference: ComponentReference) -> None:
        self.clear_ignore_for(reference)
        self.violationIgnored.emit()

    def _insert_pause_after_row(self, row: int):
        page = self._get_parent_page()
        if not page:
            return

        dialog = PauseDescriptionDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        description = dialog.get_description()
        pause = PauseEntry(description=description)

        insert_row = row + 1
        self.blockSignals(True)
        try:
            page.insert_pause_to_ordered_table(self, insert_row, str(pause), True)
        finally:
            self.blockSignals(False)

    def _edit_pause_at_row(self, row: int):
        page = self._get_parent_page()
        if not page:
            return

        mod_item = self.item(row, 0)
        if not mod_item:
            return

        reference = mod_item.data(ROLE_COMPONENT)
        _, current_description = PauseEntry.parse(reference.comp_key)

        dialog = PauseDescriptionDialog(self, current_description, "edit")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_description = dialog.get_description()
        pause = PauseEntry(description=new_description)

        self.blockSignals(True)
        try:
            self.removeRow(row)
            page.insert_pause_to_ordered_table(self, row, str(pause), False)
        finally:
            self.blockSignals(False)

    def _remove_pause_at_row(self, row: int):
        self.blockSignals(True)
        try:
            self.removeRow(row)
        finally:
            self.blockSignals(False)

    # ── Ignore API ──────────────────────────────────────────────────────────

    def ignore_violations_for(self, reference: ComponentReference) -> None:
        """Ignore all current violations for this reference."""
        violations = IndexManager.get_indexes().get_order_violations(reference)
        if violations:
            self._ignored_violations[reference] = {id(v.rule) for v in violations}
            self.violationIgnored.emit()

    def clear_ignore_for(self, reference: ComponentReference) -> None:
        self._ignored_violations.pop(reference, None)

    def is_ignored(self, reference: ComponentReference) -> bool:
        return reference in self._ignored_violations

    def refresh_ignores(self) -> None:
        """Clear ignores for refs that have NEW violations not present when ignored."""
        indexes = IndexManager.get_indexes()
        to_clear = []
        for ref, ignored_rule_ids in self._ignored_violations.items():
            current_ids = {id(v.rule) for v in indexes.get_order_violations(ref)}
            if current_ids - ignored_rule_ids:  # new violations appeared
                to_clear.append(ref)
        for ref in to_clear:
            self.clear_ignore_for(ref)
        if to_clear:
            self.violationIgnored.emit()
