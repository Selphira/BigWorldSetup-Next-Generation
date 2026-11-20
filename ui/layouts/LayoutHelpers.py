"""Helpers shared by the widgets that rebuild the content of a layout."""

from PySide6.QtWidgets import QLayout


def clear_layout(layout: QLayout) -> None:
    """Remove every item of a layout and schedule its widgets for deletion."""
    while layout.count():
        item = layout.takeAt(0)
        if widget := item.widget():
            widget.deleteLater()
