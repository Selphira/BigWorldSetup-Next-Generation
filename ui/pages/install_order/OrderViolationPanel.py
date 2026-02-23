import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from constants import (
    COLOR_ERROR,
    COLOR_WARNING,
    ICON_ERROR,
    ICON_WARNING,
    SPACING_SMALL,
)
from core.ComponentReference import ComponentReference
from core.ModManager import ModManager
from core.RuleManager import RuleManager
from core.Rules import OrderDirection, OrderRule
from core.TranslationManager import tr
from ui.widgets.HoverTableWidget import HoverTableWidget

logger = logging.getLogger(__name__)

COL_ICON = 0
COL_TYPE = 1
COL_BEFORE = 2
COL_AFTER = 3


class OrderViolationPanel(QWidget):
    """Panel showing order violations for selected components."""

    def __init__(self, mod_manager: ModManager, rule_manager: RuleManager, parent=None):
        super().__init__(parent)
        self._mod_manager = mod_manager
        self._rule_manager = rule_manager
        self._current_references: list[ComponentReference] = []
        self._current_order: list[ComponentReference] = []

        self._lbl_title: QLabel | None = None
        self._table: QTableWidget | None = None

        self._create_widgets()

    def _create_widgets(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_SMALL)

        self._lbl_title = QLabel()
        self._lbl_title.setObjectName("section-title")
        layout.addWidget(self._lbl_title)

        self._lbl_ref = QLabel()
        self._lbl_ref.setWordWrap(True)
        layout.addWidget(self._lbl_ref)

        self._table = HoverTableWidget()
        self._table.setColumnCount(4)
        hheader = self._table.horizontalHeader()
        hheader.setSectionResizeMode(COL_ICON, QHeaderView.ResizeMode.ResizeToContents)
        hheader.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeMode.ResizeToContents)
        hheader.setSectionResizeMode(COL_BEFORE, QHeaderView.ResizeMode.Stretch)
        hheader.setSectionResizeMode(COL_AFTER, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.cellEntered.connect(self._on_cell_entered)
        self._table.setMinimumHeight(100)

        layout.addWidget(self._table)
        self._hide_panel()
        self.retranslate_ui()

    def _on_cell_entered(self, row: int, _col: int) -> None:
        """Show rule description as tooltip on hover."""
        icon_item = self._table.item(row, COL_ICON)
        tooltip = icon_item.toolTip() if icon_item else ""
        self._table.viewport().setToolTip(tooltip)

    # ========================================
    # Public API
    # ========================================

    def update_for_references(
        self,
        references: list[ComponentReference],
        current_order: list[ComponentReference] | None = None,
        ignored_violations: dict[ComponentReference, set[int]] | None = None,
    ) -> None:
        self._current_references = references
        self._current_order = current_order or []
        ignored_violations = ignored_violations or {}

        if not references:
            self._hide_panel()
            return

        # Group violations by (rule_id, ref) to aggregate all before/after refs per pair
        rule_ref_data: dict[tuple, tuple] = {}

        for ref in references:
            ignored_ids = ignored_violations.get(ref, set())
            for violation in self._rule_manager.get_order_violations(ref):
                if id(violation.rule) in ignored_ids:
                    continue
                rule_id = id(violation.rule)
                key = (rule_id, id(ref))

                src_ref = violation.affected_components[0]
                tgt_ref = violation.affected_components[1]
                is_source = ref == src_ref
                other_ref = tgt_ref if is_source else src_ref

                direction = (
                    violation.rule.order_direction
                    if isinstance(violation.rule, OrderRule)
                    else OrderDirection.AFTER
                )

                if key not in rule_ref_data:
                    rule_ref_data[key] = (violation, ref, [], [])
                _, _, befores, afters = rule_ref_data[key]

                if direction == OrderDirection.BEFORE:
                    target_list = afters if is_source else befores
                else:
                    target_list = befores if is_source else afters

                if other_ref not in target_list:
                    target_list.append(other_ref)

        if not rule_ref_data:
            self._hide_panel()
            return

        self._show_panel()

        if len(references) == 1:
            self._lbl_ref.setText(str(references[0]))
        else:
            self._lbl_ref.setText(
                tr("page.order.violation_panel.multiple_selected", count=len(references))
            )

        rows = list(rule_ref_data.values())
        self._table.setRowCount(len(rows))

        for row, (violation, ref, befores, afters) in enumerate(rows):
            is_broad = violation.is_broad_rule()
            base_color = QColor(COLOR_ERROR if violation.is_error else COLOR_WARNING)
            color = (
                QColor(
                    (base_color.red() + 180) // 2,
                    (base_color.green() + 180) // 2,
                    (base_color.blue() + 180) // 2,
                )
                if is_broad
                else base_color
            )

            def make_item(text: str = "") -> QTableWidgetItem:
                item = QTableWidgetItem(text)
                item.setForeground(color)
                if is_broad:
                    font = item.font()
                    font.setItalic(True)
                    item.setFont(font)
                return item

            severity_icon = ICON_ERROR if violation.is_error else ICON_WARNING
            icon_item = make_item(severity_icon)
            icon_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_item.setData(Qt.ItemDataRole.UserRole, violation)
            icon_item.setToolTip(violation.get_order_message(ref, self._current_order))

            type_item = make_item(
                tr(f"page.selection.violation.type_{violation.rule.rule_type.value}")
            )
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self._table.setItem(row, COL_ICON, icon_item)
            self._table.setItem(row, COL_TYPE, type_item)
            self._table.setItem(row, COL_BEFORE, make_item(self._format_refs_grouped(befores)))
            self._table.setItem(row, COL_AFTER, make_item(self._format_refs_grouped(afters)))

    def retranslate_ui(self) -> None:
        """Update column headers after language change."""
        self._table.setHorizontalHeaderLabels(
            [
                "",
                tr("page.order.violation.col_type"),
                tr("page.order.violation.col_before"),
                tr("page.order.violation.col_after"),
            ]
        )
        self._lbl_title.setText(tr("page.order.violation_title"))
        if self._current_references:
            self.update_for_references(self._current_references)

    # ========================================
    # Helpers
    # ========================================

    @staticmethod
    def _format_refs_grouped(refs: list[ComponentReference]) -> str:
        """Format refs grouped by mod: 'mod1:1,2,3, mod2:0'"""
        by_mod: dict[str, list[str]] = {}
        for ref in refs:
            keys = by_mod.setdefault(ref.mod_id, [])
            if not ref.is_mod():
                keys.append(ref.comp_key)
        return ", ".join(
            f"{mod}:{','.join(keys)}" if keys else mod for mod, keys in by_mod.items()
        )

    def _hide_panel(self) -> None:
        self._lbl_title.hide()
        self._lbl_ref.hide()
        self._table.hide()
        self._table.setRowCount(0)

    def _show_panel(self) -> None:
        self._lbl_title.show()
        self._lbl_ref.show()
        self._table.show()
