"""Helpers shared by the panels that list a mod's external links."""

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel

from constants import COLOR_TEXT


def create_link_label(icon: str, text: str, url: str) -> QLabel:
    """Create a label whose link opens in the system browser."""
    label = QLabel(f'{icon} <a href="{url}" style="color: {COLOR_TEXT};">{text}</a>')
    label.setOpenExternalLinks(False)
    label.linkActivated.connect(lambda u: QDesktopServices.openUrl(QUrl(u)))
    label.setToolTip(url)
    return label
