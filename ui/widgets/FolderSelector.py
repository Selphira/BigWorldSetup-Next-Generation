"""Folder selector widget with validation and visual feedback."""

import logging
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from constants import *
from core.GameModels import GameDefinition
from core.TranslationManager import tr
from core.validators.FolderValidator import ExistingFolderValidator, FolderValidator

logger = logging.getLogger(__name__)


class FolderSelector(QWidget):
    """Universal folder selector with pluggable validators and visual feedback.

    Provides a labeled text input with browse button and real-time validation.
    Visual indicators (checkmark/warning) show validation state.

    Signals:
        validation_changed: Emitted when validation state changes (bool: is_valid)
    """

    validation_changed = Signal(bool)

    def __init__(
            self,
            label_key: str,
            select_title_key: str,
            validator: Optional[FolderValidator] = None,
            parent: Optional[QWidget] = None
    ) -> None:
        """Initialize folder selector.

        Args:
            label_key: Translation key for label
            select_title_key: Translation key for dialog title
            validator: Validator instance (defaults to ExistingFolderValidator)
            parent: Parent widget
        """
        super().__init__(parent)

        self.validator = validator or ExistingFolderValidator()
        self._is_valid = False
        self._label_key = label_key
        self._select_title_key = select_title_key
        self._error_message = ""

        # UI components (initialized in _create_widgets)
        self.label: Optional[QLabel] = None
        self.path_input: Optional[QLineEdit] = None
        self.icon_action: Optional[QAction] = None
        self.browse_btn: Optional[QPushButton] = None

        self._create_widgets()
        self._connect_signals()

    # ========================================
    # UI CREATION
    # ========================================

    def _create_widgets(self) -> None:
        """Create and layout UI widgets.
        """
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(5)

        # Label
        self.label = QLabel()
        layout.addWidget(self.label)

        # Input and browse button row
        input_layout = self._create_input_row()
        layout.addLayout(input_layout)

        # Set initial neutral state
        self._set_neutral_state()

    def _create_input_row(self) -> QHBoxLayout:
        """Create input field with browse button.

        Returns:
            Layout with input and button
        """
        layout = QHBoxLayout()
        layout.setSpacing(5)

        # Create input field
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText(tr('widget.select_folder_placeholder'))

        # Add icon action (for validation indicator)
        self.icon_action = QAction(self.path_input)
        self.icon_action.setToolTip("")
        self.path_input.addAction(
            self.icon_action,
            QLineEdit.ActionPosition.LeadingPosition
        )

        layout.addWidget(self.path_input, 1)

        # Create browse button
        self.browse_btn = QPushButton(tr('button.browse'))
        self.browse_btn.setFixedWidth(BUTTON_WIDTH_SMALL)
        layout.addWidget(self.browse_btn)

        return layout

    def _connect_signals(self) -> None:
        """Connect signal handlers."""
        self.browse_btn.clicked.connect(self._on_browse_clicked)  # type: ignore[attr-defined]
        self.path_input.textChanged.connect(self._on_path_changed)  # type: ignore[attr-defined]

        # Override enterEvent for custom tooltip
        self.path_input.enterEvent = self._on_input_hover

    # ========================================
    # EVENT HANDLERS
    # ========================================

    def _on_browse_clicked(self) -> None:
        """Handle browse button click to open folder dialog."""
        # Use current path or home directory as starting point
        start_dir = self.path_input.text() or str(Path.home())

        folder = QFileDialog.getExistingDirectory(
            self,
            tr(self._select_title_key),
            start_dir
        )

        if folder:
            self.set_path(folder)
            logger.debug(f"Folder selected via dialog: {folder}")

    def _on_path_changed(self, path: str) -> None:
        """Handle path text change and validate.

        Args:
            path: New path text
        """
        self._validate_path(path)

    def _on_input_hover(self, event) -> None:
        """Show tooltip on hover if there's a validation error.

        Args:
            event: Enter event
        """
        if not self._is_valid and self._error_message:
            # Calculate tooltip position (slightly above input)
            tooltip_pos = self.path_input.mapToGlobal(
                self.path_input.rect().bottomLeft()
            )
            tooltip_pos.setY(tooltip_pos.y() - 10)

            QToolTip.showText(
                tooltip_pos,
                self._error_message,
                self.path_input
            )

    # ========================================
    # VALIDATION
    # ========================================

    def _validate_path(self, path: str) -> None:
        """Validate the selected path using the validator.

        Args:
            path: Path to validate
        """
        self._is_valid, self._error_message = self.validator.validate(path)
        self._update_visual_state()
        self.validation_changed.emit(self._is_valid)

        if self._is_valid:
            logger.debug(f"Path validated successfully: {path}")
        else:
            logger.debug(f"Path validation failed: {path} - {self._error_message}")

    # ========================================
    # VISUAL STATE MANAGEMENT
    # ========================================

    def _update_visual_state(self) -> None:
        """Update visual indicators based on validation state."""
        if not self.path_input.text():
            self._set_neutral_state()
        elif self._is_valid:
            self._set_success_state()
        else:
            self._set_error_state()

    def _set_neutral_state(self) -> None:
        """Set neutral visual state (empty input)."""
        self.icon_action.setIcon(QIcon())

    def _set_success_state(self) -> None:
        """Set success visual state (valid input)."""
        icon = self._create_text_icon(
            ICON_SUCCESS,
            QColor(COLOR_SUCCESS),
            ICON_SIZE_SMALL
        )
        self.icon_action.setIcon(icon)

    def _set_error_state(self) -> None:
        """Set error visual state (invalid input)."""
        icon = self._create_text_icon(
            ICON_ERROR,
            QColor(COLOR_ERROR),
            ICON_SIZE_SMALL
        )
        self.icon_action.setIcon(icon)
        self.icon_action.setToolTip(self._error_message)

    # ========================================
    # PUBLIC API
    # ========================================

    def retranslate_ui(self) -> None:
        """Update all translatable UI elements."""
        # Label
        self.label.setText(tr(self._label_key))

        # Browse button (standard translation)
        self.browse_btn.setText(tr("button.browse"))

        # Placeholder (standard translation)
        self.path_input.setPlaceholderText(
            tr('widget.select_folder_placeholder')
        )

    def get_path(self) -> str:
        """Get currently selected path.

        Returns:
            Current path text
        """
        return self.path_input.text()

    def set_path(self, path: str) -> None:
        """Set path and trigger validation.

        Args:
            path: Path to set
        """
        self.path_input.setText(path)
        logger.debug(f"Path set: {path}")

    def is_valid(self) -> bool:
        """Check if current path is valid.

        Returns:
            True if path passes validation
        """
        return self._is_valid

    def get_error_message(self) -> str:
        """Get current validation error message.

        Returns:
            Error message or empty string if valid
        """
        return self._error_message

    def set_validator(self, validator: FolderValidator) -> None:
        """Change the validator and revalidate.

        Args:
            validator: New validator instance
        """
        self.validator = validator
        self._validate_path(self.get_path())
        logger.debug(f"Validator changed to: {validator.__class__.__name__}")

    def clear(self) -> None:
        """Clear the path input."""
        self.path_input.clear()
        logger.debug("Path cleared")

    # ========================================
    # UTILITY METHODS
    # ========================================

    @staticmethod
    def _create_text_icon(text: str, color: QColor, size: int = 16) -> QIcon:
        """Create a QIcon from text (emoji or character).

        Args:
            text: Text/emoji to render
            color: Text color
            size: Icon size in pixels

        Returns:
            QIcon with rendered text
        """
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Configure font
        font = QFont()
        font.setPixelSize(size - 4)  # Slightly smaller than icon
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(color)

        # Draw centered text
        painter.drawText(
            pixmap.rect(),
            Qt.AlignmentFlag.AlignCenter,
            text
        )
        painter.end()

        return QIcon(pixmap)

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<FolderSelector path='{self.get_path()}' "
            f"valid={self._is_valid}>"
        )


class GameFolderSelector(FolderSelector):
    """Folder selector specialized for game folders with game name in labels.

    Extends FolderSelector to automatically interpolate the game's display name
    into translated strings for labels and dialog titles.
    """

    def __init__(
            self,
            label_key: str,
            select_title_key: str,
            game: GameDefinition,
            validator: Optional[FolderValidator] = None,
            parent: Optional[QWidget] = None
    ) -> None:
        """Initialize game folder selector.

        Args:
            label_key: Translation key for label (should accept 'game' parameter)
            select_title_key: Translation key for dialog title (should accept 'game' parameter)
            game: Game enum for this selector
            validator: Validator instance (defaults to ExistingFolderValidator)
            parent: Parent widget
        """
        self.game = game

        super().__init__(label_key, select_title_key, validator, parent)

    def _on_browse_clicked(self) -> None:
        """Handle browse button click to open folder dialog.

        Overrides parent to use game-specific dialog title.
        """
        # Use current path or home directory as starting point
        start_dir = self.path_input.text() or str(Path.home())

        folder = QFileDialog.getExistingDirectory(
            self,
            tr(self._select_title_key, game=self.game.name),
            start_dir
        )

        if folder:
            self.set_path(folder)
            logger.debug(f"Folder selected for {self.game.id}: {folder}")

    def retranslate_ui(self) -> None:
        """Update all translatable UI elements with game name interpolation."""
        super().retranslate_ui()

        # Label with game name
        self.label.setText(tr(self._label_key, game=self.game.name))

    def get_game(self) -> GameDefinition:
        """Get the game associated with this selector.

        Returns:
            Game enum
        """
        return self.game

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"<GameFolderSelector game={self.game.id} "
            f"path='{self.get_path()}' valid={self.is_valid()}>"
        )
