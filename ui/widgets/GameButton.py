from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from constants import *
from core.GameModels import GameDefinition


class GameButton(QWidget):
    """Clickable button representing a game type with icon and name.

    Signals:
        clicked: Emitted when button is clicked (GameDefinition: selected game)
    """

    clicked = Signal(GameDefinition)

    def __init__(
            self,
            game: GameDefinition,
            icon_path: Optional[Path] = None,
            parent: Optional[QWidget] = None
    ) -> None:
        """Initialize game button.

        Args:
            game: GameDefinition instance
            icon_path: Path to icon image (uses emoji fallback if not found)
            parent: Parent widget
        """
        super().__init__(parent)

        self.game = game
        self._is_selected = False

        # UI components
        self.container: Optional[QFrame] = None
        self.icon_label: Optional[QLabel] = None
        self.name_label: Optional[QLabel] = None

        self._create_widgets(icon_path)
        self.container.setProperty("selected", False)
        self.setFixedHeight(GAME_BUTTON_HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _create_widgets(self, icon_path: Optional[Path]) -> None:
        """Create UI widgets.

        Args:
            icon_path: Optional path to icon image
        """
        # Main layout (no margins)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Container frame with border and background
        self.container = QFrame()
        self.container.setObjectName("gameButtonFrame")
        main_layout.addWidget(self.container)

        # Layout inside container
        layout = QVBoxLayout(self.container)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # Icon
        self.icon_label = self._create_icon_label(icon_path)
        layout.addWidget(self.icon_label)

        # Name
        self.name_label = self._create_name_label()
        layout.addWidget(self.name_label)

        self._update_style()

    def _create_icon_label(self, icon_path: Optional[Path]) -> QLabel:
        """Create icon label with image or emoji fallback.

        Args:
            icon_path: Optional path to icon image

        Returns:
            Configured QLabel with icon
        """
        label = QLabel()
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Try to load image
        if icon_path and icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            scaled_pixmap = pixmap.scaled(
                GAME_BUTTON_ICON_SIZE, GAME_BUTTON_ICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            label.setPixmap(scaled_pixmap)
        else:
            # Fallback to emoji
            label.setText(ICON_GAME_DEFAULT)
            font = label.font()
            font.setPointSize(GAME_BUTTON_ICON_SIZE)
            label.setFont(font)

        return label

    def _create_name_label(self) -> QLabel:
        """Create game name label.

        Returns:
            Configured QLabel with game name
        """
        label = QLabel(self.game.name)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)

        font = label.font()
        font.setBold(True)
        font.setPointSize(10)
        label.setFont(font)

        return label

    def _update_style(self) -> None:
        self.container.style().unpolish(self.container)
        self.container.style().polish(self.container)
        self.container.update()

    def set_selected(self, selected: bool) -> None:
        """Set selection state.

        Args:
            selected: True to select, False to deselect
        """
        if self._is_selected != selected:
            self._is_selected = selected
            self.container.setProperty("selected", selected)
            self._update_style()

    def is_selected(self) -> bool:
        """Check if button is selected.

        Returns:
            True if selected
        """
        return self._is_selected

    def mousePressEvent(self, event) -> None:
        """Handle mouse press event.

        Args:
            event: Mouse event
        """
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.game)
        super().mousePressEvent(event)
