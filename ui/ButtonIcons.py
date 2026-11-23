"""Flat font icons of the buttons, drawn in the colors of the button text."""

from PySide6.QtGui import QIcon
import qtawesome

from constants import COLOR_TEXT, COLOR_TEXT_DISABLED


def button_icon(name: str) -> QIcon:
    """Icon of a button, dimmed like its text when the button is disabled.

    Args:
        name: qtawesome icon name, e.g. "fa6s.play"

    Returns:
        Icon following the enabled and disabled colors of the theme
    """
    return qtawesome.icon(name, color=COLOR_TEXT, color_disabled=COLOR_TEXT_DISABLED)
