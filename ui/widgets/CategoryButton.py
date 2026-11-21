"""Category button widget with modern design and selection state."""

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel

from constants import BADGE_HEIGHT, BADGE_MIN_WIDTH, ICON_SIZE_MEDIUM
from core.enums.CategoryEnum import CategoryEnum
from ui.widgets.SelectableCard import SelectableCard

logger = logging.getLogger(__name__)


class CategoryButton(SelectableCard):
    """
    Modern category button with icon, label, and count badge.

    Signals:
        clicked: Emitted when button is clicked (CategoryEnum)
    """

    clicked = Signal(CategoryEnum)

    # Visual constants
    MIN_HEIGHT = 30

    def __init__(self, category: CategoryEnum, count: int, is_selected: bool = False) -> None:
        """
        Initialize category button.

        Args:
            category: Category enum value
            count: Number of mods in this category
            is_selected: Initial selection state
        """
        super().__init__("category-button")

        self.icon_label: QLabel | None = None
        self.category_label: QLabel | None = None
        self.count_label: QLabel | None = None

        self._category = category
        self._count = count

        self.setup_ui()
        self.retranslate_ui()

        self.set_selected(is_selected)
        self.update_count(count)

        logger.debug(f"CategoryButton created: {category.value}")

    def setup_ui(self) -> None:
        """Configure UI layout and widgets."""
        self.container.setProperty("empty", False)

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
        self.count_label.setMinimumWidth(BADGE_MIN_WIDTH)
        self.count_label.setFixedHeight(BADGE_HEIGHT)
        layout.addWidget(self.count_label)

        # Widget configuration
        self.setMinimumHeight(self.MIN_HEIGHT)

    def _clicked_value(self) -> CategoryEnum:
        return self._category

    # ========================================
    # PUBLIC API
    # ========================================

    def update_count(self, count: int) -> None:
        """
        Update mod count and visual state.

        Args:
            count: New mod count
        """
        self._count = count
        self.count_label.setText(str(count))
        self.container.setProperty("empty", count == 0)
        self._refresh_style()

    def retranslate_ui(self) -> None:
        """Update translatable text."""
        self.category_label.setText(CategoryEnum.get_display_name(self._category))

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<CategoryButton category={self._category.value} "
            f"count={self._count} selected={self._is_selected}>"
        )
