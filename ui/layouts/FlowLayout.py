"""
FlowLayout - Custom layout that arranges widgets in a flowing manner.

Widgets are laid out horizontally until the available width is filled,
then wraps to the next line. Similar to CSS flexbox with flex-wrap.
"""

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget


class FlowLayout(QLayout):
    """Layout that arranges widgets in rows, wrapping to new lines as needed.

    This layout automatically wraps widgets to the next line when the current
    line would exceed the available width. It's useful for creating responsive
    interfaces with variable numbers of items (tags, buttons, cards, etc.).

    Example:
        layout = FlowLayout(spacing=10)
        for i in range(20):
            layout.addWidget(QPushButton(f"Button {i}"))
        container.setLayout(layout)
    """

    # Default layout constants
    DEFAULT_MARGIN = 0
    DEFAULT_SPACING = 10

    def __init__(
        self,
        parent: QWidget | None = None,
        margin: int = DEFAULT_MARGIN,
        spacing: int = DEFAULT_SPACING,
    ):
        """Initialize the flow layout.

        Args:
            parent: Parent widget (optional)
            margin: Margin around the layout in pixels
            spacing: Space between items in pixels
        """
        super().__init__(parent)
        self._item_list: list[QLayoutItem] = []

        self.setContentsMargins(margin, margin, margin, margin)
        self.setSpacing(spacing)

    # ========================================
    # QLayout Interface Implementation
    # ========================================

    def addItem(self, item: QLayoutItem) -> None:
        """Add an item to the layout.

        Args:
            item: Layout item to add
        """
        self._item_list.append(item)

    def count(self) -> int:
        """Return the number of items in the layout.

        Returns:
            Number of items
        """
        return len(self._item_list)

    def itemAt(self, index: int) -> QLayoutItem | None:
        """Return the item at the specified index.

        Args:
            index: Index of the item

        Returns:
            Layout item at index, or None if index is out of bounds
        """
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:
        """Remove and return the item at the specified index.

        Args:
            index: Index of the item to remove

        Returns:
            Removed layout item, or None if index is out of bounds
        """
        if 0 <= index < len(self._item_list):
            return self._item_list.pop(index)
        return None

    # ========================================
    # Layout Behavior
    # ========================================

    def expandingDirections(self) -> Qt.Orientations:
        """Return which directions the layout can expand.

        Returns:
            No expansion (layout respects size hints)
        """
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        """Indicate that height depends on width.

        This is true for flow layouts since wrapping depends on available width.

        Returns:
            Always True
        """
        return True

    def heightForWidth(self, width: int) -> int:
        """Calculate required height for given width.

        Args:
            width: Available width in pixels

        Returns:
            Required height in pixels
        """
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        """Set the geometry of the layout and arrange items.

        Args:
            rect: Available rectangle for the layout
        """
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    # ========================================
    # Size Hints
    # ========================================

    def sizeHint(self) -> QSize:
        """Return the preferred size of the layout.

        Returns:
            Preferred size (same as minimum size for flow layouts)
        """
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        """Calculate minimum size needed for the layout.

        Returns:
            Minimum size that can contain all items
        """
        size = QSize()

        # Find the largest minimum size among all items
        for item in self._item_list:
            size = size.expandedTo(item.minimumSize())

        # Add margins
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

        return size

    # ========================================
    # Layout Algorithm
    # ========================================

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        """Arrange items in a flowing manner within the given rectangle.

        This is the core layout algorithm that positions items left-to-right,
        wrapping to new lines when necessary.

        Args:
            rect: Available rectangle for layout
            test_only: If True, only calculate height without actually positioning

        Returns:
            Total height used by the layout
        """
        x = rect.x()
        y = rect.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._item_list:
            item_width = item.sizeHint().width()
            item_height = item.sizeHint().height()

            # Calculate position if we place item at current x
            next_x = x + item_width + spacing

            # Check if item fits on current line
            # (subtract spacing because last item doesn't need trailing space)
            if next_x - spacing > rect.right() and line_height > 0:
                # Wrap to next line
                x = rect.x()
                y += line_height + spacing
                next_x = x + item_width + spacing
                line_height = 0

            # Position the item (unless we're just testing)
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            # Update position for next item
            x = next_x
            line_height = max(line_height, item_height)

        # Return total height used
        return y + line_height - rect.y()

    # ========================================
    # Utility Methods
    # ========================================

    def clear(self) -> None:
        """Remove all items from the layout.

        Note: This does not delete the widgets, only removes them from layout.
        """
        while self.count() > 0:
            item = self.takeAt(0)
            if item and item.widget():
                item.widget().setParent(None)
