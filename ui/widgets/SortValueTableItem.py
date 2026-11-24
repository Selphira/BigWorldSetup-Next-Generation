from datetime import datetime

from PySide6.QtWidgets import QTableWidgetItem


class SortValueTableItem(QTableWidgetItem):
    """Table cell displaying formatted text but sorted on the value it shows.

    A date or a size formatted for the interface language does not sort
    correctly as text ("24/11/2026" before "25/10/2026", "900 MB" after "1.2 GB").
    """

    def __init__(self, text: str, sort_value: int | float | datetime):
        super().__init__(text)
        self._sort_value: int | float | datetime = sort_value

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, SortValueTableItem):
            return self._sort_value < other._sort_value
        return super().__lt__(other)
