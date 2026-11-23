"""First launch dialog choosing the interface language."""

import logging

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from core.TranslationManager import get_translator, tr
from ui.widgets.LanguageSelector import LanguageSelector

logger = logging.getLogger(__name__)


class LanguageChoiceDialog(QDialog):
    """
    Ask for the interface language before the application loads.

    The interface switches to the highlighted language at once, so that the
    user reads the dialog in the language being considered.
    """

    MINIMUM_WIDTH = 360

    def __init__(
        self, languages: list[str], initial_language: str, parent: QWidget | None = None
    ) -> None:
        """
        Initialize the dialog.

        Args:
            languages: Selectable language codes, in display order
            initial_language: Language highlighted when the dialog opens
            parent: Parent widget
        """
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumWidth(self.MINIMUM_WIDTH)

        self._message = QLabel()
        self._message.setWordWrap(True)

        self._languages = QComboBox()
        for code in languages:
            self._languages.addItem(LanguageSelector.language_label(code), code)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        self._buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.addWidget(self._message)
        layout.addWidget(self._languages)
        layout.addWidget(self._buttons)

        self._select(initial_language)
        self._languages.currentIndexChanged.connect(self._on_language_highlighted)
        self._apply_language(self.selected_language())

    def selected_language(self) -> str:
        """Return the code of the highlighted language."""
        return str(self._languages.currentData())

    def _select(self, code: str) -> None:
        index = self._languages.findData(code)
        if index >= 0:
            self._languages.setCurrentIndex(index)

    def _on_language_highlighted(self, _index: int) -> None:
        self._apply_language(self.selected_language())

    def _apply_language(self, code: str) -> None:
        get_translator().set_language(code)
        self._retranslate_ui()
        logger.debug(f"First launch language previewed: {code}")

    def _retranslate_ui(self) -> None:
        self.setWindowTitle(tr("dialog.ui_language.title"))
        self._message.setText(tr("dialog.ui_language.message"))
        self._buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("button.ok"))

        for index in range(self._languages.count()):
            code = self._languages.itemData(index)
            self._languages.setItemText(index, LanguageSelector.language_label(code))
