"""Color helpers shared by UI components."""

from PySide6.QtGui import QColor


def blend(color: QColor, toward: int) -> QColor:
    """Return the midpoint between a color and the gray level ``toward``."""
    return QColor(
        (color.red() + toward) // 2,
        (color.green() + toward) // 2,
        (color.blue() + toward) // 2,
    )
