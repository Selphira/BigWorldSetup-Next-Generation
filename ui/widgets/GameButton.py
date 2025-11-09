from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.enums.GameEnum import GameEnum


class GameButton(QWidget):
    """Clickable button representing a game type with icon and name.

    Signals:
        clicked: Emitted when button is clicked (GameEnum: selected game)
    """

    clicked = Signal(GameEnum)

    # Visual constants
    BUTTON_HEIGHT = 120
    ICON_SIZE = 64
    DEFAULT_ICON = "🎮"
    DEFAULT_ICON_SIZE = 48

    # Colors
    COLOR_SELECTED_BG = "#655949"
    COLOR_UNSELECTED_BG = "#2a2a2a"
    COLOR_HOVER_BG = "#333333"
    COLOR_SELECTED_TEXT = "#ffffff"
    COLOR_UNSELECTED_TEXT = "#cccccc"

    def __init__(
        self,
        game: GameEnum,
        icon_path: Optional[Path] = None,
        parent: Optional[QWidget] = None
    ) -> None:
        """Initialize game button.

        Args:
            game: GameEnum instance
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
        self.setFixedHeight(self.BUTTON_HEIGHT)
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
                self.ICON_SIZE, self.ICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            label.setPixmap(scaled_pixmap)
        else:
            # Fallback to emoji
            label.setText(self.DEFAULT_ICON)
            font = label.font()
            font.setPointSize(self.DEFAULT_ICON_SIZE)
            label.setFont(font)

        return label

    def _create_name_label(self) -> QLabel:
        """Create game name label.

        Returns:
            Configured QLabel with game name
        """
        label = QLabel(self.game.display_name)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)

        font = label.font()
        font.setBold(True)
        font.setPointSize(10)
        label.setFont(font)

        return label

    def _update_style(self) -> None:
        """Update widget style based on selection state."""
        if self._is_selected:
            self.container.setStyleSheet(f"""
                QFrame#gameButtonFrame {{
                    background-color: {self.COLOR_SELECTED_BG};
                }}
            """)
            self.name_label.setStyleSheet(
                f"color: {self.COLOR_SELECTED_TEXT}; "
                "background: transparent; border: none;"
            )
        else:
            self.container.setStyleSheet(f"""
                QFrame#gameButtonFrame {{
                    background-color: {self.COLOR_UNSELECTED_BG};
                }}
                QFrame#gameButtonFrame:hover {{
                    background-color: {self.COLOR_HOVER_BG};
                }}
            """)
            self.name_label.setStyleSheet(
                f"color: {self.COLOR_UNSELECTED_TEXT}; "
                "background: transparent; border: none;"
            )

        # Icon always transparent
        self.icon_label.setStyleSheet("background: transparent; border: none;")

    def set_selected(self, selected: bool) -> None:
        """Set selection state.

        Args:
            selected: True to select, False to deselect
        """
        if self._is_selected != selected:
            self._is_selected = selected
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