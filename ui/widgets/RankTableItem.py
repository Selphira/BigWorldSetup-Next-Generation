from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem


class RankTableItem(QTableWidgetItem):
    """Table cell holding a processing rank, sorted numerically.

    A row that follows another one without being processed itself (a mod
    delivered by another archive, for example) takes that row's rank without
    displaying it, so that it stays right below it when sorted by rank.
    """

    def __init__(self, rank: int, displayed: bool = True):
        super().__init__(str(rank) if displayed else "")
        self._sort_key: tuple[int, int] = (rank, 0 if displayed else 1)
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, RankTableItem):
            return self._sort_key < other._sort_key
        return super().__lt__(other)
