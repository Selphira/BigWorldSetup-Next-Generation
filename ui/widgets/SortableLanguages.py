"""Sortable language icons widget with drag-and-drop support."""

import logging
from typing import List, Optional

from PySide6.QtCore import QMimeData, QPoint, Signal
from PySide6.QtGui import QDrag, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from constants import *
from core.TranslationManager import get_supported_languages

logger = logging.getLogger(__name__)


class SortableIcon(QLabel):
    """
    Draggable icon representing a language with visual feedback.

    Provides hover effects and drag-and-drop functionality for reordering.
    """

    def __init__(
            self,
            code: str,
            image_path: Path,
            tooltip: str,
            parent: Optional[QWidget] = None
    ) -> None:
        """
        Initialize sortable icon.

        Args:
            code: Language code (e.g., 'en_US')
            image_path: Path to flag icon image
            tooltip: Tooltip text (language name)
            parent: Parent widget
        """
        super().__init__(parent)

        self.code = code

        self._setup_icon(image_path)

        self.setToolTip(tooltip or code)
        self.setFixedSize(ICON_SIZE_LARGE + 4, ICON_SIZE_LARGE + 4)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def _setup_icon(self, image_path: Path) -> None:
        """
        Load and scale icon image.

        Args:
            image_path: Path to icon image
        """
        if image_path.exists():
            pixmap = QPixmap(str(image_path)).scaled(
                ICON_SIZE_LARGE,
                ICON_SIZE_LARGE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.setPixmap(pixmap)
        else:
            logger.warning(f"Icon not found: {image_path}")
            self.setText(ICON_LANGUAGE_DEFAULT)

    def mousePressEvent(self, event) -> None:
        """
        Handle mouse press to initiate drag operation.

        Args:
            event: Mouse event
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

            drag = QDrag(self)
            mime_data = QMimeData()
            drag.setMimeData(mime_data)
            drag.setPixmap(self.pixmap())
            drag.exec(Qt.DropAction.MoveAction)

            self.setCursor(Qt.CursorShape.OpenHandCursor)


class SortableLanguages(QFrame):
    """
    Language selector with sortable flag icons.

    Displays flag icons for available languages that can be reordered
    via drag-and-drop. Language order affects mod installation language
    fallback priority.

    Signals:
        order_changed: Emitted when language order changes (List[str]: codes)
    """

    order_changed = Signal(list)

    # Layout constants
    CONTAINER_HEIGHT = 50
    SPACING = 5
    MARGINS = 5

    # Drop indicator
    INDICATOR_MARGIN = 5
    INDICATOR_WIDTH = 2

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """
        Initialize sortable icons container.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)

        self._items: List[SortableIcon] = []

        self._setup_layout()
        self._populate_languages()
        self._create_drop_indicator()

        self.setAcceptDrops(True)
        self.setFixedHeight(self.CONTAINER_HEIGHT)

    def _setup_layout(self) -> None:
        """Configure layout."""
        self.layout = QHBoxLayout(self)
        self.layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.layout.setContentsMargins(
            self.MARGINS, self.MARGINS, self.MARGINS, self.MARGINS
        )
        self.layout.setSpacing(self.SPACING)

    def _populate_languages(self) -> None:
        """Populate with available language icons."""
        for lang_code, lang_name in get_supported_languages():
            icon_path = FLAGS_DIR / f"{lang_code}.png"
            self.add_icon(lang_code, icon_path, lang_name)

        logger.info(f"Populated {len(get_supported_languages())} language icons")

    def _create_drop_indicator(self) -> None:
        """Create drop position indicator."""
        self.drop_indicator = QLabel(self)
        self.drop_indicator.setStyleSheet(
            f"background-color: {COLOR_ACCENT};"
        )
        self.drop_indicator.setFixedWidth(self.INDICATOR_WIDTH)
        self.drop_indicator.hide()

    # ========================================
    # DRAG AND DROP
    # ========================================

    def dragEnterEvent(self, event) -> None:
        """
        Handle drag enter event.

        Args:
            event: Drag event
        """
        event.acceptProposedAction()
        self.drop_indicator.show()

    def dragLeaveEvent(self, event) -> None:
        """
        Handle drag leave event.

        Args:
            event: Drag event
        """
        self.drop_indicator.hide()

    def dragMoveEvent(self, event) -> None:
        """
        Update drop indicator position during drag.

        Args:
            event: Drag event
        """
        pos = event.position().toPoint()
        dragged = event.source()

        if not isinstance(dragged, SortableIcon):
            return

        insert_index = self._calculate_insert_position(pos)

        # Calculate indicator X position
        if self.layout.count() == 0:
            x = self.INDICATOR_MARGIN
        elif insert_index >= self.layout.count():
            # After last widget
            last_widget = self.layout.itemAt(self.layout.count() - 1).widget()
            x = last_widget.x() + last_widget.width() + self.SPACING
        else:
            # Before widget at insert_index
            widget = self.layout.itemAt(insert_index).widget()
            x = widget.x() - self.INDICATOR_WIDTH

        self.drop_indicator.move(x, self.INDICATOR_MARGIN)
        self.drop_indicator.setFixedHeight(
            self.height() - 2 * self.INDICATOR_MARGIN
        )
        self.drop_indicator.show()

    def dropEvent(self, event) -> None:
        """
        Handle drop event to reorder icons.

        Args:
            event: Drop event
        """
        pos = event.position().toPoint()
        dragged = event.source()

        if not isinstance(dragged, SortableIcon):
            return

        # Get old and new positions
        old_index = self.layout.indexOf(dragged)
        insert_index = self._calculate_insert_index(dragged, pos)

        # Reinsert widget
        self.layout.removeWidget(dragged)
        self.layout.insertWidget(insert_index, dragged)

        self.drop_indicator.hide()
        event.acceptProposedAction()

        # Emit signal only if order actually changed
        if old_index != insert_index:
            new_order = self.get_order()
            self.order_changed.emit(new_order)
            logger.debug(f"Icon order changed: {new_order}")

    def _calculate_insert_position(self, pos: QPoint) -> int:
        """
        Calculate where to show drop indicator based on mouse position.

        Args:
            pos: Mouse position

        Returns:
            Index where indicator should appear
        """
        for i in range(self.layout.count()):
            widget = self.layout.itemAt(i).widget()
            if widget and pos.x() < widget.x() + widget.width() // 2:
                return i
        return self.layout.count()

    def _calculate_insert_index(
            self,
            dragged: SortableIcon,
            pos: QPoint
    ) -> int:
        """
        Calculate final insertion index accounting for direction.

        When dragging right, we need to adjust the index because
        removing the dragged item shifts indices.

        Args:
            dragged: Dragged item
            pos: Mouse position

        Returns:
            Index where dragged item should be inserted
        """
        old_index = self.layout.indexOf(dragged)
        new_index = self._calculate_insert_position(pos)

        # Adjust for removal shifting indices
        if new_index > old_index:
            new_index -= 1

        return new_index

    # ========================================
    # PUBLIC API
    # ========================================

    def add_icon(
            self,
            code: str,
            image_path: Path,
            tooltip: str
    ) -> None:
        """
        Add an icon to the container.

        Args:
            code: Language code
            image_path: Path to icon image
            tooltip: Tooltip text
        """
        icon = SortableIcon(code, image_path, tooltip, self)
        self.layout.addWidget(icon)
        self._items.append(icon)
        logger.debug(f"Icon added: {code}")

    def get_order(self) -> List[str]:
        """
        Get current order of language codes.

        Returns:
            List of language codes in current order
        """
        widgets = [
            self.layout.itemAt(i).widget()
            for i in range(self.layout.count())
            if self.layout.itemAt(i).widget() is not None
        ]

        return [
            icon.code for icon in widgets
            if isinstance(icon, SortableIcon)
        ]

    def set_order(self, codes: List[str]) -> None:
        """
        Set icon order by language codes.

        Args:
            codes: Ordered list of language codes
        """
        # Create mapping of code to icon
        icon_map = {icon.code: icon for icon in self._items}

        # Reorder widgets
        for i, code in enumerate(codes):
            if code in icon_map:
                icon = icon_map[code]
                # Remove from current position
                self.layout.removeWidget(icon)
                # Insert at new position
                self.layout.insertWidget(i, icon)

        logger.debug(f"Icon order set: {codes}")

    def clear(self) -> None:
        """Remove all icons from container."""
        for icon in self._items:
            self.layout.removeWidget(icon)
            icon.deleteLater()
        self._items.clear()
        logger.debug("Icons cleared")

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<SortableLanguages order={self.get_order()}>"
