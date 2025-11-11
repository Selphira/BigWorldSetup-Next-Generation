"""Sortable language icons widget with drag-and-drop support."""

import logging
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QDrag, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from core.TranslationManager import get_supported_language_codes, tr

logger = logging.getLogger(__name__)


class SortableIcon(QLabel):
    """Draggable icon representing a language with visual feedback.

    Provides hover effects and drag-and-drop functionality for reordering.
    """

    # Visual constants
    ICON_SIZE = 32
    WIDGET_SIZE = 36
    BORDER_RADIUS = 6

    # Colors
    COLOR_HOVER_BG = "#3a3a3a"

    def __init__(
            self,
            code: str,
            image_path: Path,
            tooltip: str,
            parent: Optional[QWidget] = None
    ) -> None:
        """Initialize sortable icon.

        Args:
            code: Language code (e.g., 'en_US')
            image_path: Path to flag icon image
            tooltip: Tooltip text (language name)
            parent: Parent widget
        """
        super().__init__(parent)

        self.code = code

        self._setup_icon(image_path)
        self._setup_style()

        self.setToolTip(tooltip or code)
        self.setFixedSize(self.WIDGET_SIZE, self.WIDGET_SIZE)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def _setup_icon(self, image_path: Path) -> None:
        """Load and scale icon image.

        Args:
            image_path: Path to icon image
        """
        if image_path.exists():
            pixmap = QPixmap(str(image_path)).scaled(
                self.ICON_SIZE, self.ICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.setPixmap(pixmap)
        else:
            logger.warning(f"Icon not found: {image_path}")
            self.setText("🌐")

    def _setup_style(self) -> None:
        """Configure widget stylesheet."""
        self.setStyleSheet(f"""
            QLabel:hover {{
                border-radius: {self.BORDER_RADIUS}px;
                background-color: {self.COLOR_HOVER_BG};
            }}
        """)

    def mousePressEvent(self, event) -> None:
        """Handle mouse press to initiate drag operation.

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


class SortableIcons(QFrame):
    """Horizontal container for draggable icons with drop indicator.

    Provides visual feedback during drag operations and emits signals
    when order changes.

    Signals:
        order_changed: Emitted when icon order changes (List[str]: codes)
    """

    order_changed = Signal(list)

    # Layout constants
    CONTAINER_HEIGHT = 50
    SPACING = 5
    MARGINS = 5

    # Drop indicator
    INDICATOR_COLOR = "#655949"
    INDICATOR_MARGIN = 5
    INDICATOR_WIDTH = 2

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initialize sortable icons container.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)

        self._items: List[SortableIcon] = []

        self._setup_layout()
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

    def _create_drop_indicator(self) -> None:
        """Create drop position indicator."""
        self.drop_indicator = QLabel(self)
        self.drop_indicator.setStyleSheet(
            f"background-color: {self.INDICATOR_COLOR};"
        )
        self.drop_indicator.setFixedWidth(self.INDICATOR_WIDTH)
        self.drop_indicator.hide()

    # ========================================
    # DRAG AND DROP
    # ========================================

    def dragEnterEvent(self, event) -> None:
        """Handle drag enter event.

        Args:
            event: Drag event
        """
        event.acceptProposedAction()
        self.drop_indicator.show()

    def dragLeaveEvent(self, event) -> None:
        """Handle drag leave event.

        Args:
            event: Drag event
        """
        self.drop_indicator.hide()

    def dragMoveEvent(self, event) -> None:
        """Update drop indicator position during drag.

        Args:
            event: Drag event
        """
        pos = event.position().toPoint()
        insert_index = self._get_insert_index(pos)

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
        """Handle drop event to reorder icons.

        Args:
            event: Drop event
        """
        pos = event.position().toPoint()
        dragged = event.source()

        if not isinstance(dragged, SortableIcon):
            return

        # Get old and new positions
        old_index = self.layout.indexOf(dragged)
        insert_index = self._get_insert_index(pos)

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

    def _get_insert_index(self, pos: QPoint) -> int:
        """Calculate insertion index based on mouse position.

        Args:
            pos: Mouse position

        Returns:
            Index where dragged item should be inserted
        """
        for i in range(self.layout.count()):
            widget = self.layout.itemAt(i).widget()
            if widget and pos.x() < widget.x() + widget.width() // 2:
                return i
        return self.layout.count()

    # ========================================
    # PUBLIC API
    # ========================================

    def add_icon(
            self,
            code: str,
            image_path: Path,
            tooltip: str
    ) -> None:
        """Add an icon to the container.

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
        """Get current order of language codes.

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
        """Set icon order by language codes.

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


class SortableLanguages(QFrame):
    """Language selector with sortable flag icons.

    Displays flag icons for available languages that can be reordered
    via drag-and-drop.

    Signals:
        order_changed: Emitted when language order changes (List[str]: codes)
    """

    order_changed = Signal(list)

    ICONS_DIR = Path("resources") / "flags"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        """Initialize sortable languages widget.

        Args:
            parent: Parent widget
        """
        super().__init__(parent)

        # UI components
        self.label: Optional[QLabel] = None
        self.sortable_icons: Optional[SortableIcons] = None

        self._create_widgets()
        self._populate_languages()
        self.retranslate_ui()

        logger.debug("SortableLanguages initialized")

    def _create_widgets(self) -> None:
        """Create and layout UI widgets."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(5)

        # Label
        self.label = QLabel()
        layout.addWidget(self.label)

        # Sortable icons container
        self.sortable_icons = SortableIcons()
        self.sortable_icons.order_changed.connect(self._on_language_order_changed)
        layout.addWidget(self.sortable_icons)

    def _populate_languages(self) -> None:
        """Populate with available language icons."""
        language_codes = get_supported_language_codes()

        for lang_code in language_codes:
            icon_path = self.ICONS_DIR / f"{lang_code}.png"
            # Use code as tooltip for now (can be improved with display name)
            self.sortable_icons.add_icon(lang_code, icon_path, lang_code)

        logger.info(f"Populated {len(language_codes)} language icons")

    # ========================================
    # EVENT HANDLERS
    # ========================================

    def _on_language_order_changed(self, order: List[str]) -> None:
        """Handle language order change from sortable icons.

        Args:
            order: New order of language codes
        """
        self.order_changed.emit(order)
        logger.info(f"Language order changed: {order}")

    # ========================================
    # PUBLIC API
    # ========================================

    def get_order(self) -> List[str]:
        """Get current language order.

        Returns:
            List of language codes in current order
        """
        return self.sortable_icons.get_order()

    def set_order(self, codes: List[str]) -> None:
        """Set language order.

        Args:
            codes: Ordered list of language codes
        """
        self.sortable_icons.set_order(codes)
        logger.debug(f"Language order set: {codes}")

    def retranslate_ui(self) -> None:
        """Update all translatable UI elements."""
        self.label.setText(tr("widget.languages_order"))

    def __repr__(self) -> str:
        """String representation for debugging."""
        return f"<SortableLanguages order={self.get_order()}>"