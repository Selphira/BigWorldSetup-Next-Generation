"""
SelectionController - Centralized selection logic with index-based state.

This controller is the ONLY place where selection logic lives.
All modifications go through this controller and update indexes.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, Signal

from core.ComponentReference import ComponentReference, IndexManager
from core.GameModels import GameDefinition
from core.RuleManager import RuleManager
from core.SelectionCascade import SelectionCascade, SelectionConstraints

logger = logging.getLogger(__name__)


class SelectionController(QObject):
    """Centralized controller for selection logic."""

    selection_changed = Signal(ComponentReference, bool)
    selections_bulk_changed = Signal(list, list)
    validation_needed = Signal()

    def __init__(self, rule_manager: RuleManager):
        super().__init__()
        self._rule_manager = rule_manager
        self._indexes = IndexManager.get_indexes()
        self._game: GameDefinition | None = None
        self._cascade_depth = 0

        logger.info("SelectionController initialized")

    def forced_selection(self) -> set[ComponentReference]:
        if not self._game:
            return set()
        return set(ComponentReference.from_string_list(self._game.get_forced_components()))

    def selection_snapshot(self) -> frozenset[ComponentReference]:
        return frozenset(self._indexes.selection_index)

    def selection_constraints(self) -> SelectionConstraints:
        if not self._game:
            return SelectionConstraints()
        return SelectionConstraints(
            self._game.id, frozenset(self._game.get_forced_components())
        )

    def apply_selection(self, selection: set[ComponentReference]) -> None:
        """Publish an already validated selection, including all cascade effects."""
        before = self._indexes.selection_index.copy()
        self._indexes.selection_index.clear()
        self._indexes.selection_index.update(selection)
        self.selections_bulk_changed.emit(list(selection - before), list(before - selection))
        self.validation_needed.emit()

    def set_game(self, game: GameDefinition) -> None:
        """Set current game and enforce forced components."""
        self._game = game

        unselected = self._unselect_incompatible_components()
        forced = self._select_forced_components()

        if forced or unselected:
            self.validation_needed.emit()

        logger.info(
            f"Game set to {game.id}, {len(forced)} forced components, "
            f"{len(unselected)} incompatible components unselected"
        )

    # ========================================
    # Main API
    # ========================================

    def select(
        self, reference: ComponentReference, cascade: bool = True, emit_validation: bool = True
    ) -> bool:
        """Select component with full validation and propagation.

        Args:
            reference: Component to select
            cascade: If True, propagate to parents/children
            emit_validation: If True, emit validation_needed (False for batch)
        """
        if self._indexes.is_selected(reference):
            return True

        if not self._cascade().select(reference, cascade):
            return False

        if emit_validation and self._cascade_depth == 0:
            self.validation_needed.emit()

        return True

    def unselect(
        self,
        reference: ComponentReference,
        cascade: bool = True,
        emit_validation: bool = True,
        from_parent: bool = False,
    ) -> bool:
        """Unselect component with full propagation."""
        if not self._indexes.is_selected(reference):
            return True

        if not self._cascade().unselect(reference, cascade, from_parent):
            return False

        if emit_validation and self._cascade_depth == 0:
            self.validation_needed.emit()

        return True

    def toggle(self, reference: ComponentReference) -> bool:
        """Toggle selection state."""
        if self._indexes.is_selected(reference):
            return self.unselect(reference, cascade=True)
        return self.select(reference, cascade=True)

    # ========================================
    # Game Constraints
    # ========================================

    def _cascade(self) -> SelectionCascade:
        """Cascade over the published selection, reporting each change by signal."""
        return SelectionCascade(
            self._indexes,
            self._indexes.selection_index,
            self.selection_constraints(),
            self.selection_changed.emit,
        )

    def _select_forced_components(self) -> list[str]:
        if not self._game:
            return []

        forced = self._game.get_forced_components()

        for ref_str in forced:
            reference = ComponentReference.from_string(ref_str)
            current = reference
            while current:
                item = self._indexes.get_tree_item(current)
                if item:
                    flags = item.flags()
                    flags &= ~Qt.ItemFlag.ItemIsUserCheckable
                    item.setFlags(flags)
                    logger.debug(f"Disabled checkbox for: {current}")

                current = self._indexes.get_parent(current)
            self.select(reference, cascade=True, emit_validation=False)

        return forced

    def _unselect_incompatible_components(self) -> list[ComponentReference]:
        """Unselect all components that are incompatible with current game."""
        if not self._game:
            return []

        cascade = self._cascade()
        unselected = []
        currently_selected = list(self._indexes.selection_index)

        for reference in currently_selected:
            if cascade.is_forced(reference):
                continue

            if reference.is_sub() or reference.is_sub_option():
                continue

            if cascade.is_compatible_with_game(reference):
                continue

            if self.unselect(reference, cascade=True, emit_validation=False):
                unselected.append(reference)
                logger.debug(f"Unselected incompatible component: {reference}")

        return unselected

    # ========================================
    # Bulk Operations
    # ========================================

    def select_bulk(self, references: list[ComponentReference]) -> None:
        """Select multiple components efficiently."""
        self._cascade_depth += 1
        selected = []

        try:
            for ref in references:
                if self.select(ref, cascade=True, emit_validation=False):
                    selected.append(ref)
        finally:
            self._cascade_depth -= 1

        if selected:
            self.selections_bulk_changed.emit(selected, [])
            self.validation_needed.emit()

    def clear_all(self) -> None:
        """Clear all except forced."""
        self._cascade_depth += 1
        unselected = []
        selected = []

        try:
            unselected = list(self._indexes.selection_index)
            self._indexes.clear_selection()
            self._select_forced_components()
            selected = list(self._indexes.selection_index)
        finally:
            self._cascade_depth -= 1

        if unselected or selected:
            self.selections_bulk_changed.emit(selected, unselected)
            self.validation_needed.emit()

    # ========================================
    # Query API
    # ========================================

    def is_selected(self, reference: ComponentReference) -> bool:
        return self._indexes.is_selected(reference)

    def get_selected_components(self) -> list[ComponentReference]:
        return self._indexes.get_selected_components()

    def get_selection_count(self) -> int:
        return len(self._indexes.get_selected_components())
