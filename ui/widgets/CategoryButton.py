"""Category button widget with modern design and selection state."""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from constants import ICON_SIZE_MEDIUM
from core.enums.CategoryEnum import CategoryEnum

logger = logging.getLogger(__name__)


class CategoryButton(QWidget):
    """
    Modern category button with icon, label, and count badge.

    Signals:
        clicked: Emitted when button is clicked (CategoryEnum)
    """

    clicked = Signal(CategoryEnum)

    # Visual constants
    MIN_HEIGHT = 30
    BADGE_MIN_WIDTH = 35
    BADGE_HEIGHT = 18

    def __init__(
            self,
            category: CategoryEnum,
            count: int,
            is_selected: bool = False
    ) -> None:
        """
        Initialize category button.

        Args:
            category: Category enum value
            count: Number of mods in this category
            is_selected: Initial selection state
        """
        super().__init__()

        self.container = None
        self.icon_label = None
        self.category_label = None
        self.count_label = None

        self._category = category
        self._count = count
        self._is_selected = is_selected

        self.setup_ui()
        self.retranslate_ui()

        self.set_selected(is_selected)
        self.update_count(count)

        logger.debug(f"CategoryButton created: {category.value}")

    def setup_ui(self) -> None:
        """Configure UI layout and widgets."""
        # Main layout with no margins
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Container frame
        self.container = QFrame()
        self.container.setObjectName("category-button")
        self.container.setProperty("empty", False)
        main_layout.addWidget(self.container)

        # Content layout inside container
        layout = QHBoxLayout(self.container)
        layout.setContentsMargins(12, 2, 12, 2)
        layout.setSpacing(8)

        # Category icon
        self.icon_label = QLabel(self._category.icon)
        self.icon_label.setFixedWidth(ICON_SIZE_MEDIUM)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        layout.addWidget(self.icon_label)

        # Category name
        self.category_label = QLabel()
        layout.addWidget(self.category_label, 1)

        # Count badge
        self.count_label = QLabel(str(self._count))
        self.count_label.setObjectName("count-badge")
        self.count_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.count_label.setMinimumWidth(self.BADGE_MIN_WIDTH)
        self.count_label.setFixedHeight(self.BADGE_HEIGHT)
        layout.addWidget(self.count_label)

        # Widget configuration
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        """
        Handle mouse click event.

        Args:
            event: Mouse event
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._category)
            logger.debug(f"Category clicked: {self._category.value}")
        super().mousePressEvent(event)

    def _update_style(self):
        for widget in [self.container] + self.container.findChildren(QWidget):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    # ========================================
    # PUBLIC API
    # ========================================

    def set_selected(self, selected: bool) -> None:
        """
        Set selection state..

        Args:
            selected: True to select, False to deselect
        """
        self._is_selected = selected
        self.container.setProperty("selected", selected)
        self._update_style()

    def is_selected(self) -> bool:
        """
        Check if button is selected.

        Returns:
            True if selected
        """
        return self._is_selected

    def update_count(self, count: int) -> None:
        """
        Update mod count and visual state.

        Args:
            count: New mod count
        """
        self._count = count
        self.count_label.setText(str(count))
        self.container.setProperty("empty", count == 0)
        self._update_style()

    def retranslate_ui(self) -> None:
        """Update translatable text."""
        self.category_label.setText(
            CategoryEnum.get_display_name(self._category)
        )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<CategoryButton category={self._category.value} "
            f"count={self._count} selected={self._is_selected}>"
        )
