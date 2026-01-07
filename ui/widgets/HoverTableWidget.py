import logging

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QTableWidget

from constants import COLOR_BACKGROUND_SECONDARY, ROLE_BACKGROUND

logger = logging.getLogger(__name__)


class HoverTableWidget(QTableWidget):
    """Custom table widget with row hover highlighting."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_row = -1
        self._setup_table()

    def _setup_table(self) -> None:
        """Configure table settings."""
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.setMouseTracking(True)
        self.setSortingEnabled(True)

    def mouseMoveEvent(self, event) -> None:
        """Handle mouse move for row hover effect."""
        row = self.rowAt(int(event.position().y()))

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
                if not item.data(ROLE_BACKGROUND):
                    original_bg = item.background()
                    item.setData(ROLE_BACKGROUND, original_bg)

                item.setBackground(QColor(COLOR_BACKGROUND_SECONDARY))

    def _clear_row_hover(self, row: int) -> None:
        """Clear hover style from row."""
        for col in range(self.columnCount()):
            item = self.item(row, col)
            if item:
                # Restore original background
                original_bg = item.data(ROLE_BACKGROUND)
                if original_bg:
                    item.setBackground(original_bg)
                    item.setData(ROLE_BACKGROUND, None)
