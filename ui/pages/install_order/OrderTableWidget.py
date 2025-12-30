from __future__ import annotations

import logging

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QToolTip,
)

from constants import (
    ROLE_COMPONENT,
)
from core.ComponentReference import ComponentReference
from ui.pages.install_order.DraggableTable import DraggableTableWidget

logger = logging.getLogger(__name__)


class OrderTableWidget(DraggableTableWidget):
    """Table with lazy tooltip calculation for order violations."""

    def __init__(self, rule_manager, mod_manager, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rule_manager = rule_manager
        self._mod_manager = mod_manager
        self._current_order: list[ComponentReference] = []

    def set_current_order(self, order: list[ComponentReference]) -> None:
        """Update current order for tooltip calculation."""
        self._current_order = order

    def viewportEvent(self, event) -> bool:
        """Intercept tooltip events to calculate on-demand."""
        if event.type() == QEvent.Type.ToolTip:
            return self._handle_tooltip_event(event)
        return super().viewportEvent(event)

    def _handle_tooltip_event(self, event) -> bool:
        """Calculate and show tooltip only when needed."""
        pos = event.pos()
        item = self.itemAt(pos)

        if not item:
            QToolTip.hideText()
            return True

        row = item.row()
        mod_item = self.item(row, 0)

        if not mod_item:
            QToolTip.hideText()
            return True

        reference = mod_item.data(ROLE_COMPONENT)
        if not reference:
            QToolTip.hideText()
            return True

        violations = self._rule_manager.get_order_violations(reference)

        if not violations:
            QToolTip.hideText()
            return True

        unique_violations = {v.rule: v for v in violations}
        violations = list(unique_violations.values())

        tooltip_lines = []
        for v in violations:
            msg = v.get_order_message(reference, self._current_order)
            tooltip_lines.append(f"{v.icon} {msg}")

        tooltip_text = "\n".join(tooltip_lines)
        QToolTip.showText(event.globalPos(), tooltip_text, self)

        return True
