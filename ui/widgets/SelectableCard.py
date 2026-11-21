"""Base widget for clickable cards with a selection state."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget


class SelectableCard(QWidget):
    """Clickable card drawn by a styled container frame.

    The container exposes a ``selected`` dynamic property used by the theme.
    Subclasses declare their own typed ``clicked`` signal, fill ``container``
    with their content and return the emitted value from ``_clicked_value``.
    """

    clicked: Signal

    def __init__(self, frame_name: str, parent: QWidget | None = None) -> None:
        """Initialize the card and its empty container.

        Args:
            frame_name: Object name of the container, targeted by the theme
            parent: Parent widget
        """
        super().__init__(parent)

        self._is_selected = False

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.container = QFrame()
        self.container.setObjectName(frame_name)
        self.container.setProperty("selected", False)
        main_layout.addWidget(self.container)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_selected(self, selected: bool) -> None:
        """Set selection state.

        Args:
            selected: True to select, False to deselect
        """
        if self._is_selected == selected:
            return

        self._is_selected = selected
        self.container.setProperty("selected", selected)
        self._refresh_style()

    def is_selected(self) -> bool:
        """Check if the card is selected.

        Returns:
            True if selected
        """
        return self._is_selected

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Emit ``clicked`` on left click.

        Args:
            event: Mouse event
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._clicked_value())
        super().mousePressEvent(event)

    def _clicked_value(self) -> object:
        """Return the value carried by the ``clicked`` signal."""
        raise NotImplementedError

    def _refresh_style(self) -> None:
        """Re-apply the theme after a dynamic property change.

        Children are repolished too, since their rules may depend on the
        container properties (e.g. the category count badge).
        """
        for widget in [self.container, *self.container.findChildren(QWidget)]:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()
