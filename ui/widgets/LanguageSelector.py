"""Language selection button for the application toolbar."""

import logging
from typing import Optional

from PySide6.QtCore import Signal, QSize
from PySide6.QtGui import QAction, QIcon, QPixmap, QCursor
from PySide6.QtWidgets import QMenu, QToolButton

from constants import *
from core.TranslationManager import get_translator

logger = logging.getLogger(__name__)


class LanguageSelector(QToolButton):
    """
    Language selection button with dropdown menu.

    Displays current language flag and provides a menu to switch
    between available languages.

    Signals:
        language_changed: Emitted when user selects a new language (str: language_code)
    """

    language_changed = Signal(str)

    def __init__(
            self,
            available_languages: list[str],
            parent: Optional[QToolButton] = None
    ) -> None:
        """
        Initialize the language menu button.

        Args:
            available_languages: List of language codes to display in menu.
            parent: Parent widget
        """
        super().__init__(parent)

        self._available_languages = available_languages
        self._current_lang: Optional[str] = None
        self._actions: dict[str, QAction] = {}
        self._menu = QMenu(self)

        self._setup_ui()
        self._populate_menu()

    def _setup_ui(self) -> None:
        """Configure button appearance and behavior."""
        self.setMenu(self._menu)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setToolTip(get_translator().get("tooltip.select_language"))
        self.setIconSize(QSize(ICON_SIZE_LARGE, ICON_SIZE_LARGE))
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def _populate_menu(self) -> None:
        """Populate menu with available languages."""

        if not self._available_languages:
            logger.warning("No languages available")
            return

        for code in self._available_languages:
            self._add_language_action(code)

    def _add_language_action(self, code: str) -> None:
        """
        Add a language option to the menu.

        Args:
            code: Language code (e.g., 'en_US', 'fr_FR')
        """
        translator = get_translator()
        label = translator.get_language_name(code)
        icon_path = FLAGS_DIR / f"{code}.png"

        action = QAction(label, self)
        action.setData(code)

        if icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                action.setIcon(QIcon(pixmap))

        # Use lambda with default argument to capture code value
        action.triggered.connect(
            lambda checked=False, lang=code: self.select_language(lang)
        )

        self._menu.addAction(action)
        self._actions[code] = action

        logger.debug(f"Added language action: {code} ({label})")

    def select_language(self, code: str) -> bool:
        """
        Select and activate a language.

        Args:
            code: Language code to select

        Returns:
            True if language was changed, False otherwise
        """
        translator = get_translator()
        available_languages = translator.get_available_languages()

        # Validate language code
        if code not in available_languages:
            logger.warning(f"Invalid language code: {code}")
            return False

        # No change needed
        if self._current_lang == code:
            return False

        self._current_lang = code

        # Update visual indicators
        self._update_menu_checkmarks(code)
        self._update_icon(code)

        # Emit signal
        self.language_changed.emit(code)

        logger.info(f"Language selected: {code}")
        return True

    def _update_menu_checkmarks(self, selected_code: str) -> None:
        """
        Update checkmarks in menu to reflect selected language.

        Args:
            selected_code: Currently selected language code
        """
        for code, action in self._actions.items():
            action.setChecked(code == selected_code)

    def _update_icon(self, code: str) -> None:
        """
        Update button icon to show flag of selected language.

        Args:
            code: Language code for icon lookup
        """
        icon_path = FLAGS_DIR / f"{code}.png"

        if icon_path.exists():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                self.setIcon(QIcon(pixmap))
                logger.debug(f"Icon loaded: {icon_path}")
                return

        # Fallback to default icon
        logger.debug(f"Flag icon not found: {icon_path}")

    def current_language(self) -> Optional[str]:
        """
        Get currently selected language code.

        Returns:
            Current language code or None if not set
        """
        return self._current_lang

    def get_available_languages(self) -> list[str]:
        """
        Get list of available language codes.

        Returns:
            List of language codes
        """
        return list(self._actions.keys())
