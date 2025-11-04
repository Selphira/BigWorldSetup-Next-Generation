from PySide6.QtWidgets import (
    QVBoxLayout
)

from bwsng.core.StateManager import StateManager
from bwsng.core.TranslationManager import tr
from bwsng.ui.pages.BasePage import BasePage


class InstallationTypePage(BasePage):
    """Page simple pour choisir type d'installation."""
    def __init__(self, state_manager: StateManager):
        super().__init__(state_manager)

        layout = QVBoxLayout(self)
        layout.addStretch()

    def get_page_title(self) -> str:
        return tr("page.type.title")

    def get_page_id(self) -> str:
        return "installation_type"

    def can_proceed(self) -> bool:
        return False