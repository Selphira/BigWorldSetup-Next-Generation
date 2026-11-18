"""Review proposed component changes before applying them to the selection."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from constants import (
    COLOR_ERROR,
    COLOR_SUCCESS,
    RESOLUTION_PREVIEW_MAX_VISIBLE_ROWS,
    RESOLUTION_PREVIEW_WIDTH,
)
from core.TranslationManager import tr


class ResolutionPreviewDialog(QDialog):
    """Show the affected components in a scrollable, read-only table."""

    def __init__(
        self,
        changes: list[tuple[str, str, bool]],
        component_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("page.selection.resolution_title", component=component_name))
        layout = QVBoxLayout(self)
        headers = [
            tr("page.order.col_mod"),
            tr("page.order.col_component"),
            tr("page.selection.resolution_action"),
        ]
        table = QTableWidget(len(changes), len(headers), self)
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().hide()
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setWordWrap(False)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for row, (mod, component, added) in enumerate(changes):
            for column, name in enumerate((mod, component)):
                item = QTableWidgetItem(name)
                item.setToolTip(name)
                table.setItem(row, column, item)
            action = QTableWidgetItem("+" if added else "−")
            action.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            action.setForeground(QColor(COLOR_SUCCESS if added else COLOR_ERROR))
            description = tr(
                "page.selection.resolution_add" if added else "page.selection.resolution_remove"
            )
            action.setToolTip(description)
            action.setData(Qt.ItemDataRole.AccessibleTextRole, description)
            table.setItem(row, 2, action)
        layout.addWidget(table)

        cancel = QPushButton(tr("button.cancel"), self)
        apply = QPushButton(tr("button.apply"), self)
        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(apply)
        apply.setAutoDefault(False)
        cancel.setDefault(True)
        apply.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        layout.addLayout(buttons)

        rows = min(len(changes), RESOLUTION_PREVIEW_MAX_VISIBLE_ROWS)
        margins = layout.contentsMargins()
        height = (
            header.sizeHint().height()
            + rows * table.verticalHeader().defaultSectionSize()
            + 2 * table.frameWidth()
            + margins.top()
            + margins.bottom()
            + layout.spacing()
            + buttons.sizeHint().height()
        )
        self.resize(RESOLUTION_PREVIEW_WIDTH, height)
