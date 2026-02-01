import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from constants import (
    COLOR_ERROR,
    COLOR_SUCCESS,
    COLOR_WARNING,
    ICON_ERROR,
    ICON_WARNING,
    SPACING_SMALL,
)
from core.ComponentReference import ComponentReference, IndexManager
from core.Rules import RuleViolation
from core.TranslationManager import tr
from core.ValidationOrchestrator import ValidationOrchestrator
from ui.pages.mod_selection.ComponentContextMenu import ComponentContextMenu
from ui.pages.mod_selection.SelectionController import SelectionController
from ui.pages.mod_selection.TreeItem import TreeItem
from ui.widgets.HoverTableWidget import HoverTableWidget

logger = logging.getLogger(__name__)


class ViolationPanel(QWidget):
    """Panel displaying violations with table and actions."""

    violation_resolved = Signal()

    def __init__(self, controller: SelectionController, parent=None):
        super().__init__(parent)

        self._controller = controller
        self._context_menu: ComponentContextMenu | None = None
        self._orchestrator: ValidationOrchestrator | None = None
        self._indexes = IndexManager.get_indexes()
        self._current_reference: ComponentReference | None = None

        self._lbl_title: QLabel | None = None
        self._table: HoverTableWidget | None = None

        self._create_widgets()
        self._connect_signals()

    def set_context_menu(self, context_menu: ComponentContextMenu):
        """Set the context menu builder to use."""
        self._context_menu = context_menu

    def set_orchestrator(self, orchestrator) -> None:
        """Inject the orchestrator."""
        self._orchestrator = orchestrator

    def _create_widgets(self) -> None:
        """Create the UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_SMALL)

        header_layout = QHBoxLayout()

        self._lbl_title = QLabel()
        self._lbl_title.setWordWrap(True)
        header_layout.addWidget(self._lbl_title)

        layout.addLayout(header_layout)

        # Violations table
        self._table = HoverTableWidget()
        self._table.setColumnCount(3)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.setMinimumHeight(120)
        self._table.setMouseTracking(True)

        layout.addWidget(self._table)

        self._hide_panel()

    def _connect_signals(self) -> None:
        """Connect to controller signals."""
        self._table.customContextMenuRequested.connect(self._show_row_context_menu)
        self._table.cellEntered.connect(self._on_cell_entered)

    def update_for_reference(self, reference: ComponentReference | None) -> None:
        """Update the table for a specific reference."""
        if not reference or reference.is_mod():
            self._hide_panel()
            return

        self._current_reference = reference

        self._show_panel()
        self._table.clearSelection()

        self._lbl_title.setText(self._get_component_name(reference))

        violations = self._indexes.get_selection_violations(reference)
        unique_violations = {v.rule: v for v in violations}
        violations = list(unique_violations.values())

        if not violations:
            self._table.setRowCount(1)

            item = QTableWidgetItem("✓")
            item.setForeground(QColor(COLOR_SUCCESS))
            self._table.setItem(0, 0, item)
            item = QTableWidgetItem()
            self._table.setItem(0, 1, item)
            item = QTableWidgetItem(tr("page.selection.violation_no_issues_for_component"))
            item.setForeground(QColor(COLOR_SUCCESS))
            self._table.setItem(0, 2, item)
        else:
            self._populate_table(violations)

    def _get_component_name(self, reference: ComponentReference) -> str:
        component = self._indexes.resolve(reference)

        if component:
            return f"[{component.key}] {component.text}"

        return str(reference)

    def _hide_panel(self) -> None:
        """Hide the panel completely."""
        self._lbl_title.hide()
        self._table.hide()
        self._table.setRowCount(0)

    def _show_panel(self) -> None:
        """Show the panel."""
        self._lbl_title.show()
        self._table.show()

    def _populate_table(self, violations: list[RuleViolation]) -> None:
        """Populate the table with violations."""
        filtered = {}
        for violation in violations:
            sources = tuple(sorted(violation.rule.sources, key=str))
            targets = tuple(sorted(violation.rule.targets, key=str))
            key = tuple(sorted((sources, targets), key=str))

            if key not in filtered or (
                self._current_reference in violation.rule.sources
                and self._current_reference not in filtered[key].rule.sources
            ):
                filtered[key] = violation

        violations = list(filtered.values())

        self._table.setRowCount(len(violations))

        for row, violation in enumerate(violations):
            if violation.is_error:
                icon = ICON_ERROR
                color = QColor(COLOR_ERROR)
            else:
                icon = ICON_WARNING
                color = QColor(COLOR_WARNING)

            # Column 0: Severity icon
            icon_item = QTableWidgetItem()
            icon_item.setText(icon)
            icon_item.setForeground(color)
            icon_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, icon_item)

            # Column 1: Type
            type_item = QTableWidgetItem(
                tr(f"page.selection.violation.type_{violation.rule.rule_type.value}")
            )
            type_item.setForeground(color)
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 1, type_item)

            # Column 2: Description
            desc_item = QTableWidgetItem(
                violation.get_message(self._current_reference, self._indexes.selection_index)
            )
            self._table.setItem(row, 2, desc_item)

            # Store violation in row data
            self._table.item(row, 0).setData(Qt.ItemDataRole.UserRole, violation)

    def _on_cell_entered(self, row: int, column: int) -> None:
        item = self._table.item(row, 0)
        violation = item.data(Qt.ItemDataRole.UserRole)
        if violation:
            self._table.viewport().setToolTip(
                violation.get_message(self._current_reference, self._indexes.selection_index)
                if item
                else ""
            )

    def _show_row_context_menu(self, position) -> None:
        """Display context menu for a row."""
        if not self._orchestrator or not self._current_reference:
            return

        row = self._table.rowAt(position.y())
        if row < 0:
            return

        item = self._table.item(row, 0)
        violation: RuleViolation | None = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not violation:
            return

        reference = self._current_reference  # La référence actuellement affichée
        item = IndexManager.get_indexes().get_tree_item(reference)

        if isinstance(item, TreeItem):
            global_pos = self._table.viewport().mapToGlobal(position)
            self._context_menu.show_menu(item, global_pos, specific_violation=violation)

    def retranslate_ui(self) -> None:
        """Update texts after language change."""
        if self._current_reference:
            self.update_for_reference(self._current_reference)

        self._table.setHorizontalHeaderLabels(
            [
                "",
                tr("page.selection.violation_type"),
                tr("page.selection.violation_description"),
            ]
        )
