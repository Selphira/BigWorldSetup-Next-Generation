"""Cascade effects of selecting or unselecting a reference, without Qt."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from core.ComponentReference import ComponentReference, ReferenceIndexes
from core.Mod import MucComponent, SubComponent

logger = logging.getLogger(__name__)

SelectionListener = Callable[[ComponentReference, bool], None]


@dataclass(frozen=True, slots=True)
class SelectionConstraints:
    """Game constraints a selection obeys: supported components and forced ones."""

    game_id: str | None = None
    forced_components: frozenset[str] = frozenset()


class SelectionCascade:
    """Applies the MUC, SUB, parent and forced cascades to a selection set.

    The set is modified in place and every change is reported to the listener.
    Catalogue indexes are only read, and default options come from the
    catalogue models: a cascade can run off the interface thread.
    """

    def __init__(
        self,
        indexes: ReferenceIndexes,
        selection: set[ComponentReference],
        constraints: SelectionConstraints,
        listener: SelectionListener | None = None,
    ) -> None:
        self._indexes = indexes
        self._selection = selection
        self._constraints = constraints
        self._listener = listener

    # ========================================
    # Public API
    # ========================================

    @property
    def selection(self) -> set[ComponentReference]:
        return self._selection

    def select(self, reference: ComponentReference, cascade: bool = True) -> bool:
        """Select ``reference``, its parents and, with ``cascade``, its children."""
        if reference in self._selection:
            return True

        if (
            not reference.is_sub()
            and not reference.is_sub_option()
            and not self.is_compatible_with_game(reference)
        ):
            logger.debug(f"Cannot select {reference}: incompatible with game")
            return False

        self._apply_select(reference)
        logger.debug(f"Selected: {reference}")

        self._select_parents(reference)

        if cascade:
            self._cascade_to_children_by_type(reference)

        return True

    def unselect(
        self, reference: ComponentReference, cascade: bool = True, from_parent: bool = False
    ) -> bool:
        """Unselect ``reference`` and its children, then parents left without a choice."""
        if reference not in self._selection:
            return True

        if self.has_forced_descendants(reference):
            logger.debug(f"Cannot unselect {reference}: has forced descendants")
            return False

        if not from_parent and self._is_sub_prompt_with_active_parent(reference):
            logger.debug(f"Cannot unselect {reference}: SUB parent active")
            return False

        if cascade and not from_parent and self._is_last_option_in_group(reference):
            logger.debug(f"Cannot unselect {reference}: last option")
            return False

        self._apply_unselect(reference)
        logger.debug(f"Unselected: {reference}")

        if cascade:
            self._unselect_all_children(reference)

        self._update_parent_after_unselect(reference)

        return True

    def is_compatible_with_game(self, reference: ComponentReference) -> bool:
        if not self._constraints.game_id:
            return True
        component = self._indexes.resolve(reference)
        if not component:
            return False
        return component.supports_game(self._constraints.game_id)

    def is_forced(self, reference: ComponentReference) -> bool:
        """Whether the game forces this exact reference."""
        return str(reference) in self._constraints.forced_components

    def has_forced_descendants(self, reference: ComponentReference) -> bool:
        """Whether the reference or any of its descendants is forced."""
        if self.is_forced(reference):
            return True

        children = self._indexes.get_children(reference)
        return any(self.has_forced_descendants(child) for child in children)

    # ========================================
    # Cascade Logic
    # ========================================

    def _select_parents(self, reference: ComponentReference) -> None:
        """Select parent chain without cascading to children."""
        parent = self._indexes.get_parent(reference)

        while parent and parent not in self._selection:
            self._apply_select(parent)
            logger.debug(f"  Parent auto-selected: {parent}")
            parent = self._indexes.get_parent(parent)

    def _cascade_to_children_by_type(self, reference: ComponentReference) -> None:
        """Cascade to children according to component type."""
        if self._is_muc_option(reference):
            for sibling in self._indexes.get_siblings(reference):
                self._apply_unselect(sibling)

            parent = self._indexes.get_parent(reference)
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
            return

        # SUB, STD and MOD
        for child in children:
            self.select(child, cascade=True)

    def _ensure_one_child_selected(self, reference: ComponentReference) -> None:
        """Ensure exactly one visible child is selected."""
        children = self._indexes.get_children(reference)
        compatible = [child for child in children if self.is_compatible_with_game(child)]
        if not compatible:
            return

        if any(child in self._selection for child in compatible):
            return

        default = self._default_option(reference, compatible)
        if self._is_sub_prompt(reference):
            self._select_sub_prompt_with_option(reference, default)
        else:
            self._apply_select(default)

    def _unselect_all_children(self, reference: ComponentReference) -> None:
        """Recursively unselect all children."""
        for child in self._indexes.get_children(reference):
            self.unselect(child, cascade=True, from_parent=True)

    def _update_parent_after_unselect(self, reference: ComponentReference) -> None:
        """Unselect parent if no siblings remain selected."""
        parent = self._indexes.get_parent(reference)
        if not parent:
            return

        if any(sibling in self._selection for sibling in self._indexes.get_siblings(reference)):
            return

        if parent in self._selection and not self.has_forced_descendants(parent):
            self._apply_unselect(parent)
            logger.debug(f"  Parent auto-unselected: {parent}")
            self._update_parent_after_unselect(parent)

    # ========================================
    # SUB Prompts
    # ========================================

    def _handle_sub_prompt_option_selection(self, option: ComponentReference) -> None:
        """Select the whole SUB with this answer for its prompt."""
        prompt = self._indexes.get_parent(option)
        if not prompt:
            return

        sub_parent = self._indexes.get_parent(prompt)
        if not sub_parent:
            return

        self._select_sub_with_answer(sub_parent, prompt, option)

    def _select_sub_prompt_with_option(
        self, prompt: ComponentReference, option: ComponentReference
    ) -> None:
        """Select the whole SUB of a prompt, with ``option`` as its answer."""
        sub_parent = self._indexes.get_parent(prompt)
        if not sub_parent:
            return

        self._select_sub_with_answer(sub_parent, prompt, option)

    def _select_sub_with_answer(
        self,
        sub: ComponentReference,
        answered_prompt: ComponentReference,
        answer: ComponentReference,
    ) -> None:
        """Select a SUB and all its prompts; prompts left unanswered get their default."""
        self._apply_select(sub)

        for prompt in self._indexes.get_children(sub):
            self._apply_select(prompt)

            if prompt == answered_prompt:
                for sibling in self._indexes.get_siblings(answer):
                    self._apply_unselect(sibling)
                self._apply_select(answer)
                continue

            options = self._indexes.get_children(prompt)
            if options and not any(option in self._selection for option in options):
                self._apply_select(self._default_option(prompt, options))

    # ========================================
    # Helpers
    # ========================================

    def _default_option(
        self, parent: ComponentReference, options: list[ComponentReference]
    ) -> ComponentReference:
        """Default option of a MUC or SUB prompt among ``options``, else the first one.

        Same rule as the tree: the declared default when it is one of the options.
        """
        component = self._indexes.resolve(parent.get_base_component_reference())
        default_key = None
        if isinstance(component, MucComponent) and component.default:
            default_key = str(component.default)
        elif isinstance(component, SubComponent) and self._is_sub_prompt(parent):
            prompt = component.get_prompt(parent.comp_key.split(".")[1])
            if prompt and prompt.default:
                default_key = f"{parent.comp_key}.{prompt.default}"

        for option in options:
            if option.comp_key == default_key:
                return option
        return options[0]

    def _is_muc_option(self, reference: ComponentReference) -> bool:
        parent = self._indexes.get_parent(reference)
        return bool(parent and parent.is_muc())

    @staticmethod
    def _is_sub_prompt(reference: ComponentReference) -> bool:
        return reference.comp_key.count(".") == 1

    @staticmethod
    def _is_sub_prompt_option(reference: ComponentReference) -> bool:
        return reference.comp_key.count(".") == 2

    def _is_sub_prompt_with_active_parent(self, reference: ComponentReference) -> bool:
        """Whether this SUB prompt has a selected answer."""
        if not self._is_sub_prompt(reference):
            return False
        return any(child in self._selection for child in self._indexes.get_children(reference))

    def _is_last_option_in_group(self, reference: ComponentReference) -> bool:
        """Whether this SUB answer is the last selected one of its prompt."""
        if not self._is_sub_prompt_option(reference):
            return False
        return not any(
            sibling in self._selection for sibling in self._indexes.get_siblings(reference)
        )

    def _apply_select(self, reference: ComponentReference) -> None:
        """Select without cascade."""
        if reference in self._selection:
            return
        self._selection.add(reference)
        if self._listener:
            self._listener(reference, True)

    def _apply_unselect(self, reference: ComponentReference) -> None:
        """Unselect without cascade."""
        if reference not in self._selection:
            return
        self._selection.discard(reference)
        if self._listener:
            self._listener(reference, False)
