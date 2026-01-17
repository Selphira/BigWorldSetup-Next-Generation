"""
SelectionController - Centralized selection logic with index-based state.

This controller is the ONLY place where selection logic lives.
All modifications go through this controller and update indexes.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, Signal

from constants import ROLE_IS_DEFAULT
from core.ComponentReference import ComponentReference, IndexManager
from core.GameModels import GameDefinition
from core.RuleManager import RuleManager

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
        self._proxy_model = None
        self._cascade_depth = 0

        logger.info("SelectionController initialized")

    def set_game(self, game: GameDefinition) -> None:
        """Set current game and enforce forced components."""
        self._game = game

        forced = self._select_forced_components()

        if forced:
            self.validation_needed.emit()

        logger.info(f"Game set to {game.id}, {len(forced)} forced components")

    def set_proxy_model(self, proxy_model) -> None:
        """Inject proxy model for visibility checks."""
        self._proxy_model = proxy_model

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

        if (
            not reference.is_sub()
            and not reference.is_sub_option()
            and not self._is_compatible_with_game(reference)
        ):
            logger.debug(f"Cannot select {reference}: incompatible with game")
            return False

        self._indexes.select(reference)
        logger.debug(f"Selected: {reference}")

        self.selection_changed.emit(reference, True)

        self._select_parents(reference)

        if cascade:
            self._cascade_to_children_by_type(reference)

        is_root = self._cascade_depth == 0
        if emit_validation and is_root:
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

        if self._has_forced_descendants(reference):
            logger.debug(f"Cannot unselect {reference}: has forced descendants")
            return False

        if not from_parent and self._is_sub_prompt_with_active_parent(reference):
            logger.debug(f"Cannot unselect {reference}: SUB parent active")
            return False

        if cascade and not from_parent and self._is_last_option_in_group(reference):
            logger.debug(f"Cannot unselect {reference}: last option")
            return False

        self._indexes.unselect(reference)
        logger.debug(f"Unselected: {reference}")
        self.selection_changed.emit(reference, False)

        if cascade:
            self._unselect_all_children(reference)

        self._update_parent_after_unselect(reference)

        is_root = self._cascade_depth == 0
        if emit_validation and is_root:
            self.validation_needed.emit()

        return True

    def toggle(self, reference: ComponentReference) -> bool:
        """Toggle selection state."""
        if self._indexes.is_selected(reference):
            return self.unselect(reference, cascade=True)
        else:
            return self.select(reference, cascade=True)

    # ========================================
    # Cascade Logic
    # ========================================

    def _select_parents(self, reference: ComponentReference) -> None:
        """Select parent chain without cascading to children."""
        parent = self._indexes.get_parent(reference)

        while parent:
            if not self._indexes.is_selected(parent):
                self._indexes.select(parent)
                logger.debug(f"  Parent auto-selected: {parent}")
                self.selection_changed.emit(parent, True)

                parent = self._indexes.get_parent(parent)
            else:
                break

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

    def _cascade_to_children_by_type(self, reference: ComponentReference) -> None:
        """Cascade to children according to component type."""

        if self._is_muc_option(reference):
            parent = self._indexes.get_parent(reference)

            # Unselect other options
            for sibling in self._indexes.get_siblings(reference):
                self._apply_unselect(sibling)

            # Select parent MUC if needed
            if parent:
                self._apply_select(parent)

            return

        if self._is_sub_prompt_option(reference):
            self._handle_sub_prompt_option_selection(reference)
            return

        if self._is_sub_prompt(reference):
            self._ensure_one_child_selected(reference)
            return

        children = self._indexes.get_children(reference)

        if not children:
            return

        if reference.is_muc():
            self._ensure_one_child_selected(reference)

        elif reference.is_sub():
            for child in children:
                self.select(child, cascade=True, emit_validation=False)

        else:
            # STD / MOD
            for child in children:
                self.select(child, cascade=True, emit_validation=False)

    def _ensure_one_child_selected(self, reference: ComponentReference) -> None:
        """Ensure exactly one visible child is selected."""
        children = self._indexes.get_children(reference)
        if not children:
            return

        compatible = [c for c in children if self._is_compatible_with_game(c)]
        if not compatible:
            return

        if any(self._indexes.is_selected(c) for c in compatible):
            return

        default = self._find_default_child(compatible)
        if not default:
            return

        if self._is_sub_prompt(reference):
            self._select_sub_prompt_with_option(reference, default)
        else:
            self._apply_select(default)

    def _unselect_all_children(self, reference: ComponentReference) -> None:
        """Recursively unselect all children."""
        for child in self._indexes.get_children(reference):
            self.unselect(
                child,
                cascade=True,
                emit_validation=False,
                from_parent=True,
            )

    def _update_parent_after_unselect(self, reference: ComponentReference) -> None:
        """Unselect parent if no siblings remain selected."""
        parent = self._indexes.get_parent(reference)

        if not parent:
            return

        siblings = self._indexes.get_siblings(reference)

        if not any(self._indexes.is_selected(s) for s in siblings):
            if self._indexes.is_selected(parent) and not self._has_forced_descendants(parent):
                self._indexes.unselect(parent)
                logger.debug(f"  Parent auto-unselected: {parent}")
                self.selection_changed.emit(parent, False)

                self._update_parent_after_unselect(parent)

    # ========================================
    # Helpers
    # ========================================

    def _is_compatible_with_game(self, reference: ComponentReference) -> bool:
        """Check game compatibility."""
        if not self._game:
            return True
        component = self._indexes.resolve(reference)
        if not component:
            return False
        return component.supports_game(self._game.id)

    def _is_forced_component(self, reference: ComponentReference) -> bool:
        """Check if component itself is forced by game."""
        if not self._game:
            return False
        forced = self._game.get_forced_components()
        return str(reference) in forced

    def _has_forced_descendants(self, reference: ComponentReference) -> bool:
        """Check if reference or any of its descendants is forced."""
        if self._is_forced_component(reference):
            return True

        children = self._indexes.get_children(reference)
        for child in children:
            if self._has_forced_descendants(child):
                return True

        return False

    def _is_visible(self, reference: ComponentReference) -> bool:
        """Check if visible through proxy filter."""
        if not self._proxy_model:
            return True

        item = self._indexes.get_tree_item(reference)
        if not item:
            return True

        model = item.model()
        if not model:
            return True

        source_index = model.indexFromItem(item)
        proxy_index = self._proxy_model.mapFromSource(source_index)

        return proxy_index.isValid()

    def _is_muc_option(self, reference: ComponentReference) -> bool:
        """Check if MUC option."""
        parent = self._indexes.get_parent(reference)
        return parent and parent.is_muc()

    def _is_sub_prompt(self, reference: ComponentReference) -> bool:
        """Check if SUB prompt."""
        return reference.comp_key.count(".") == 1

    def _is_sub_prompt_option(self, reference: ComponentReference) -> bool:
        """Check if SUB prompt option."""
        return reference.comp_key.count(".") == 2

    def _is_sub_prompt_with_active_parent(self, reference: ComponentReference) -> bool:
        """Check if SUB prompt has any selected option."""
        if not self._is_sub_prompt(reference):
            return False

        for child in self._indexes.get_children(reference):
            if self._indexes.is_selected(child):
                return True

        return False

    def _is_last_option_in_group(self, reference: ComponentReference) -> bool:
        """Check if last selected option in MUC/SUB group."""
        if not self._is_sub_prompt_option(reference):
            return False

        siblings = self._indexes.get_siblings(reference)
        return not any(self._indexes.is_selected(s) for s in siblings)

    def _find_default_child(
        self, children: list[ComponentReference]
    ) -> ComponentReference | None:
        """Find default child (marked or first)."""
        for child_ref in children:
            item = self._indexes.get_tree_item(child_ref)
            if item and item.data(ROLE_IS_DEFAULT):
                return child_ref
        return children[0] if children else None

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

    # ========================================
    # Low-level operations (no cascade)
    # ========================================

    def _apply_select(self, ref: ComponentReference) -> None:
        """Direct select without cascade."""
        if self._indexes.is_selected(ref):
            return
        self._indexes.select(ref)
        self.selection_changed.emit(ref, True)

    def _apply_unselect(self, ref: ComponentReference) -> None:
        """Direct unselect without cascade."""
        if not self._indexes.is_selected(ref):
            return
        self._indexes.unselect(ref)
        self.selection_changed.emit(ref, False)

    def _handle_sub_prompt_option_selection(self, option: ComponentReference) -> None:
        """Handle selection of a SUB prompt option."""
        prompt = self._indexes.get_parent(option)
        if not prompt:
            return

        sub_parent = self._indexes.get_parent(prompt)
        if not sub_parent:
            return

        self._select_entire_sub_with_option(sub_parent, prompt, option)

    def _select_entire_sub_with_option(
        self,
        sub: ComponentReference,
        selected_prompt: ComponentReference,
        selected_option: ComponentReference,
    ) -> None:
        """Select entire SUB with specific option for one prompt."""
        self._apply_select(sub)

        for prompt in self._indexes.get_children(sub):
            self._apply_select(prompt)

            options = self._indexes.get_children(prompt)

            if prompt == selected_prompt:
                for sibling in self._indexes.get_siblings(selected_option):
                    self._apply_unselect(sibling)
                self._apply_select(selected_option)
                continue

            # Other prompts: ensure default option selected
            if not any(self._indexes.is_selected(o) for o in options):
                default = self._find_default_child(options)
                if default:
                    self._apply_select(default)

    def _select_sub_prompt_with_option(
        self, prompt: ComponentReference, option: ComponentReference
    ) -> None:
        """Select SUB prompt with specific option."""
        sub_parent = self._indexes.get_parent(prompt)
        if not sub_parent:
            return

        self._apply_select(sub_parent)

        for sibling_prompt in self._indexes.get_children(sub_parent):
            self._apply_select(sibling_prompt)

            if sibling_prompt == prompt:
                for sib in self._indexes.get_siblings(option):
                    self._apply_unselect(sib)
                self._apply_select(option)
                continue

            # Other prompts: ensure an option is selected
            options = self._indexes.get_children(sibling_prompt)
            if not any(self._indexes.is_selected(o) for o in options):
                default = self._find_default_child(options)
                if default:
                    self._apply_select(default)
