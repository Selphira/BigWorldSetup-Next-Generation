from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from constants import GAME_BUTTON_HEIGHT, GAME_BUTTON_ICON_SIZE, ICON_GAME_DEFAULT
from core.GameModels import GameDefinition
from ui.widgets.SelectableCard import SelectableCard


class GameButton(SelectableCard):
    """Clickable button representing a game type with icon and name.

    Signals:
        clicked: Emitted when button is clicked (GameDefinition: selected game)
    """

    clicked = Signal(GameDefinition)

    def __init__(
        self, game: GameDefinition, icon_path: Path | None = None, parent: QWidget | None = None
    ) -> None:
        """Initialize game button.

        Args:
            game: GameDefinition instance
            icon_path: Path to icon image (uses emoji fallback if not found)
            parent: Parent widget
        """
        super().__init__("gameButtonFrame", parent)

        self.game = game

        # UI components
        self.icon_label: QLabel | None = None
        self.name_label: QLabel | None = None

        self._create_widgets(icon_path)
        self.setFixedHeight(GAME_BUTTON_HEIGHT)

    def _create_widgets(self, icon_path: Path | None) -> None:
        """Create UI widgets.

        Args:
            icon_path: Optional path to icon image
        """
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

    @staticmethod
    def _create_icon_label(icon_path: Path | None) -> QLabel:
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
                GAME_BUTTON_ICON_SIZE,
                GAME_BUTTON_ICON_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
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

    def _clicked_value(self) -> GameDefinition:
        return self.game
